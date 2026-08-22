import os
import tempfile
import sqlite3
import hashlib
import json
import uuid
import shutil
from datetime import datetime, timezone

def now_utc():
    return datetime.now(timezone.utc).isoformat()

SCHEMA_SQL = """
CREATE TABLE manifest_admission_receipts (
    admission_receipt_id TEXT PRIMARY KEY,
    manifest_canonical_sha256 TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    adapter_id TEXT NOT NULL,
    peer_kind TEXT NOT NULL,
    inventory_generation INTEGER NOT NULL,
    trust_root_json TEXT NOT NULL,
    observed_vendor_json TEXT NOT NULL,
    acl_evaluation_json TEXT,
    chain_complete INTEGER NOT NULL,
    aggregate_chain_digest TEXT NOT NULL,
    timestamp_utc TEXT NOT NULL,
    transitive_executable_chain_json TEXT NOT NULL,
    companion_binaries_json TEXT NOT NULL,
    admitted_at_utc TEXT NOT NULL,
    prov_chain_complete INTEGER NOT NULL
);

CREATE TABLE shim_registry_entries (
    shim_registration_id TEXT PRIMARY KEY,
    shim_name TEXT NOT NULL,
    canonical_shim_path TEXT NOT NULL,
    shim_path TEXT NOT NULL,
    downstream_target_path TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('ACTIVE', 'RETIRED')),
    profile_name TEXT NOT NULL,
    admission_receipt_id TEXT NOT NULL REFERENCES manifest_admission_receipts(admission_receipt_id),
    shim_file_sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX idx_active_shim_name ON shim_registry_entries(shim_name) WHERE status = 'ACTIVE';
CREATE UNIQUE INDEX idx_active_canonical_path ON shim_registry_entries(canonical_shim_path) WHERE status = 'ACTIVE';

CREATE TABLE shim_pending_operations (
    idempotency_key TEXT PRIMARY KEY,
    request_digest TEXT NOT NULL,
    shim_registration_id TEXT NOT NULL REFERENCES shim_registry_entries(shim_registration_id),
    operation_type TEXT NOT NULL CHECK (operation_type IN ('INSTALL', 'RESTORE')),
    install_sub_path TEXT CHECK (install_sub_path IN ('ABSENT', 'EXTERNAL_COLLISION', 'MANAGED_UPDATE')),
    expected_hash TEXT,
    pre_effect_hash TEXT,
    intended_profile_name TEXT,
    intended_downstream_target_path TEXT,
    intended_admission_receipt_id TEXT REFERENCES manifest_admission_receipts(admission_receipt_id),
    selected_backup_sequence_id INTEGER,
    operation_state TEXT NOT NULL CHECK (operation_state IN ('INTENT_DECLARED', 'FS_STAGED', 'COMPLETED')),
    created_at TEXT NOT NULL,
    CHECK (
        (operation_type = 'INSTALL' AND expected_hash IS NOT NULL AND selected_backup_sequence_id IS NULL AND install_sub_path IS NOT NULL
            AND intended_profile_name IS NOT NULL AND intended_downstream_target_path IS NOT NULL AND intended_admission_receipt_id IS NOT NULL
            AND (
                (install_sub_path IN ('ABSENT', 'EXTERNAL_COLLISION') AND pre_effect_hash IS NULL) OR
                (install_sub_path = 'MANAGED_UPDATE' AND pre_effect_hash IS NOT NULL)
            ))
        OR
        (operation_type = 'RESTORE' AND selected_backup_sequence_id IS NOT NULL AND expected_hash IS NULL AND install_sub_path IS NULL AND pre_effect_hash IS NULL
            AND intended_profile_name IS NULL AND intended_downstream_target_path IS NULL AND intended_admission_receipt_id IS NULL)
    ),
    FOREIGN KEY (shim_registration_id, selected_backup_sequence_id) REFERENCES shim_backup_entries(shim_registration_id, backup_sequence_id)
);
CREATE UNIQUE INDEX idx_active_pending_operation ON shim_pending_operations(shim_registration_id) WHERE operation_state != 'COMPLETED';
CREATE UNIQUE INDEX idx_pending_op_reg_key ON shim_pending_operations(shim_registration_id, idempotency_key);

CREATE TABLE shim_backup_entries (
    backup_sequence_id INTEGER PRIMARY KEY AUTOINCREMENT,
    shim_registration_id TEXT NOT NULL REFERENCES shim_registry_entries(shim_registration_id),
    originating_idempotency_key TEXT NOT NULL UNIQUE,
    target_path TEXT NOT NULL,
    backup_file_path TEXT NOT NULL,
    original_sha256 TEXT NOT NULL,
    original_mtime_epoch REAL NOT NULL,
    original_file_size_bytes INTEGER NOT NULL,
    original_permissions_octal TEXT NOT NULL,
    override_reason TEXT NOT NULL,
    backup_created_at TEXT NOT NULL,
    restored INTEGER NOT NULL DEFAULT 0 CHECK (restored IN (0, 1)),
    restored_at TEXT,
    FOREIGN KEY (shim_registration_id, originating_idempotency_key) REFERENCES shim_pending_operations(shim_registration_id, idempotency_key)
);
CREATE UNIQUE INDEX idx_backup_reg_seq ON shim_backup_entries(shim_registration_id, backup_sequence_id);
"""

class SqliteShimRegistryUnitOfWork:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def __enter__(self):
        self.conn.execute("BEGIN IMMEDIATE")
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        try:
            if exc_type is not None:
                self.conn.rollback()
        finally:
            self.conn.close()

    def commit(self):
        self.conn.commit()

    def rollback(self):
        self.conn.rollback()

    def declare_install_intent(self, idempotency_key, request_digest, shim_name, canonical_shim_path, shim_path, actual_hash_of_p, expected_hash, intended_profile_name, intended_downstream_target_path, intended_admission_receipt_id):
        cur = self.conn.cursor()

        row = cur.execute("SELECT * FROM shim_pending_operations WHERE idempotency_key=?", (idempotency_key,)).fetchone()
        if row:
            if row['request_digest'] != request_digest:
                raise ValueError("Conflict: request_digest mismatch")
            return {
                "install_sub_path": row['install_sub_path'],
                "shim_registration_id": row['shim_registration_id'],
                "pre_effect_hash": row['pre_effect_hash'],
                "operation_state": row['operation_state']
            }

        registry_rows = cur.execute("SELECT * FROM shim_registry_entries WHERE status='ACTIVE' AND (shim_name=? OR canonical_shim_path=?)", (shim_name, canonical_shim_path)).fetchall()

        receipt_row = cur.execute("SELECT transitive_executable_chain_json FROM manifest_admission_receipts WHERE admission_receipt_id=?", (intended_admission_receipt_id,)).fetchone()
        if not receipt_row:
            raise ValueError("Admission receipt not found")
        chain = json.loads(receipt_row['transitive_executable_chain_json'])
        entrypoint_path = chain[0]['canonical_path']
        if entrypoint_path != intended_downstream_target_path:
            raise ValueError(f"Admission target validation failed")

        if len(registry_rows) == 1 and registry_rows[0]['shim_name'] == shim_name and registry_rows[0]['canonical_shim_path'] == canonical_shim_path:
            reg = registry_rows[0]
            shim_registration_id = reg['shim_registration_id']

            pending = cur.execute("SELECT idempotency_key FROM shim_pending_operations WHERE shim_registration_id=? AND operation_state != 'COMPLETED'", (shim_registration_id,)).fetchone()
            if pending:
                raise ValueError("Conflict: operation already in progress")

            if actual_hash_of_p != reg['shim_file_sha256']:
                raise ValueError("ERR_SHIM_EXTERNALLY_MODIFIED")

            cur.execute("""
                INSERT INTO shim_pending_operations (
                    idempotency_key, request_digest, shim_registration_id, operation_type, install_sub_path, expected_hash,
                    pre_effect_hash, intended_profile_name, intended_downstream_target_path, intended_admission_receipt_id,
                    operation_state, created_at
                ) VALUES (?, ?, ?, 'INSTALL', 'MANAGED_UPDATE', ?, ?, ?, ?, ?, 'INTENT_DECLARED', ?)
            """, (idempotency_key, request_digest, shim_registration_id, expected_hash, reg['shim_file_sha256'],
                  intended_profile_name, intended_downstream_target_path, intended_admission_receipt_id, now_utc()))

            return {
                "install_sub_path": "MANAGED_UPDATE",
                "shim_registration_id": shim_registration_id,
                "pre_effect_hash": reg['shim_file_sha256'],
                "operation_state": 'INTENT_DECLARED'
            }

        elif len(registry_rows) == 0:
            install_sub_path = "EXTERNAL_COLLISION" if actual_hash_of_p is not None else "ABSENT"
            shim_registration_id = str(uuid.uuid4())

            cur.execute("""
                INSERT INTO shim_registry_entries (
                    shim_registration_id, shim_name, canonical_shim_path, shim_path, downstream_target_path, status,
                    profile_name, admission_receipt_id, shim_file_sha256, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 'ACTIVE', ?, ?, ?, ?, ?)
            """, (shim_registration_id, shim_name, canonical_shim_path, shim_path, intended_downstream_target_path,
                  intended_profile_name, intended_admission_receipt_id, expected_hash, now_utc(), now_utc()))

            cur.execute("""
                INSERT INTO shim_pending_operations (
                    idempotency_key, request_digest, shim_registration_id, operation_type, install_sub_path, expected_hash,
                    intended_profile_name, intended_downstream_target_path, intended_admission_receipt_id,
                    operation_state, created_at
                ) VALUES (?, ?, ?, 'INSTALL', ?, ?, ?, ?, ?, 'INTENT_DECLARED', ?)
            """, (idempotency_key, request_digest, shim_registration_id, install_sub_path, expected_hash,
                  intended_profile_name, intended_downstream_target_path, intended_admission_receipt_id, now_utc()))

            return {
                "install_sub_path": install_sub_path,
                "shim_registration_id": shim_registration_id,
                "pre_effect_hash": None,
                "operation_state": 'INTENT_DECLARED'
            }
        else:
            raise ValueError("Identity conflict: Exactly one of shim_name/canonical_shim_path matches")

    def mark_backup_staged(self, idempotency_key, shim_registration_id, install_sub_path, target_path, backup_file_path, original_sha256, original_mtime_epoch, original_file_size_bytes, original_permissions_octal, override_reason):
        cur = self.conn.cursor()
        if install_sub_path == 'EXTERNAL_COLLISION':
            cur.execute("""
                INSERT INTO shim_backup_entries (
                    shim_registration_id, originating_idempotency_key, target_path, backup_file_path, original_sha256,
                    original_mtime_epoch, original_file_size_bytes, original_permissions_octal, override_reason, backup_created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT (originating_idempotency_key) DO NOTHING
            """, (shim_registration_id, idempotency_key, target_path, backup_file_path, original_sha256, original_mtime_epoch,
                  original_file_size_bytes, original_permissions_octal, override_reason, now_utc()))

        cur.execute("UPDATE shim_pending_operations SET operation_state='FS_STAGED' WHERE idempotency_key=? AND operation_state='INTENT_DECLARED'", (idempotency_key,))
        if cur.rowcount != 1:
            raise ValueError("CAS failed: operation not in INTENT_DECLARED state")

    def commit_shim_replacement(self, idempotency_key):
        cur = self.conn.cursor()
        row = cur.execute("SELECT * FROM shim_pending_operations WHERE idempotency_key=?", (idempotency_key,)).fetchone()
        if not row:
            raise ValueError("Operation not found")

        if row['install_sub_path'] == 'MANAGED_UPDATE':
            cur.execute("""
                UPDATE shim_registry_entries
                SET shim_file_sha256=?, updated_at=?, profile_name=?, downstream_target_path=?, admission_receipt_id=?
                WHERE shim_registration_id=?
            """, (row['expected_hash'], now_utc(), row['intended_profile_name'], row['intended_downstream_target_path'],
                  row['intended_admission_receipt_id'], row['shim_registration_id']))

        cur.execute("UPDATE shim_pending_operations SET operation_state='COMPLETED' WHERE idempotency_key=? AND operation_state='FS_STAGED'", (idempotency_key,))
        if cur.rowcount != 1:
            raise ValueError("CAS failed: operation not in FS_STAGED state")

    def declare_restore_intent(self, idempotency_key, request_digest, shim_registration_id):
        cur = self.conn.cursor()
        row = cur.execute("SELECT * FROM shim_pending_operations WHERE idempotency_key=?", (idempotency_key,)).fetchone()
        if row:
            if row['request_digest'] != request_digest:
                raise ValueError("Conflict: request_digest mismatch")
            return {
                "selected_backup_sequence_id": row['selected_backup_sequence_id'],
                "shim_registration_id": row['shim_registration_id'],
                "operation_state": row['operation_state']
            }

        backup = cur.execute("SELECT backup_sequence_id, original_sha256 FROM shim_backup_entries WHERE shim_registration_id=? AND restored=0 ORDER BY backup_sequence_id DESC LIMIT 1", (shim_registration_id,)).fetchone()
        if not backup:
            raise ValueError("Nothing to restore")

        cur.execute("""
            INSERT INTO shim_pending_operations (
                idempotency_key, request_digest, shim_registration_id, operation_type, selected_backup_sequence_id,
                operation_state, created_at
            ) VALUES (?, ?, ?, 'RESTORE', ?, 'INTENT_DECLARED', ?)
        """, (idempotency_key, request_digest, shim_registration_id, backup['backup_sequence_id'], now_utc()))

        return {
            "selected_backup_sequence_id": backup['backup_sequence_id'],
            "shim_registration_id": shim_registration_id,
            "operation_state": 'INTENT_DECLARED'
        }

    def mark_restore_staged(self, idempotency_key):
        cur = self.conn.cursor()
        cur.execute("UPDATE shim_pending_operations SET operation_state='FS_STAGED' WHERE idempotency_key=? AND operation_state='INTENT_DECLARED'", (idempotency_key,))
        if cur.rowcount != 1:
            raise ValueError("CAS failed: operation not in INTENT_DECLARED state")

    def commit_restore(self, idempotency_key):
        cur = self.conn.cursor()
        row = cur.execute("SELECT * FROM shim_pending_operations WHERE idempotency_key=?", (idempotency_key,)).fetchone()
        if not row:
            raise ValueError("Operation not found")

        cur.execute("UPDATE shim_backup_entries SET restored=1, restored_at=? WHERE backup_sequence_id=?", (now_utc(), row['selected_backup_sequence_id']))
        cur.execute("UPDATE shim_registry_entries SET status='RETIRED' WHERE shim_registration_id=?", (row['shim_registration_id'],))

        cur.execute("UPDATE shim_pending_operations SET operation_state='COMPLETED' WHERE idempotency_key=? AND operation_state='FS_STAGED'", (idempotency_key,))
        if cur.rowcount != 1:
            raise ValueError("CAS failed")

class FakeStateStore:
    def __init__(self, db_path):
        self.db_path = db_path
        self._keeper_conn = None

    def initialize(self):
        self._keeper_conn = sqlite3.connect(self.db_path, uri=True)
        self._keeper_conn.executescript(SCHEMA_SQL)

    def unit_of_work(self):
        conn = sqlite3.connect(self.db_path, timeout=6.0, uri=True)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        return SqliteShimRegistryUnitOfWork(conn)

def run_install(store, idempotency_key, shim_name, shim_path, expected_payload, profile, target_path, receipt_id, crash_before_fs_write=False, crash_after_fs_write=False):
    request_digest = hashlib.sha256(expected_payload).hexdigest()
    expected_hash = request_digest
    canonical_shim_path = os.path.normpath(os.path.abspath(shim_path)).lower()

    with store.unit_of_work() as uow:
        actual_hash_of_p = None
        if os.path.exists(shim_path):
            with open(shim_path, "rb") as f:
                actual_hash_of_p = hashlib.sha256(f.read()).hexdigest().upper()

        intent = uow.declare_install_intent(
            idempotency_key, request_digest, shim_name, canonical_shim_path, shim_path,
            actual_hash_of_p, expected_hash.upper(), profile, target_path, receipt_id
        )
        uow.commit()

    if intent['operation_state'] == 'INTENT_DECLARED':
        with store.unit_of_work() as uow:
            if intent['install_sub_path'] == 'EXTERNAL_COLLISION':
                backup_path = shim_path + ".bak"
                shutil.copy2(shim_path, backup_path)
                stat = os.stat(shim_path)
                uow.mark_backup_staged(idempotency_key, intent['shim_registration_id'], 'EXTERNAL_COLLISION', shim_path, backup_path, actual_hash_of_p, stat.st_mtime, stat.st_size, oct(stat.st_mode), "collision")
            else:
                uow.mark_backup_staged(idempotency_key, intent['shim_registration_id'], intent['install_sub_path'], "", "", "", 0, 0, "", "")
            uow.commit()
            intent['operation_state'] = 'FS_STAGED'

    if intent['operation_state'] == 'FS_STAGED':
        current_hash = None
        if os.path.exists(shim_path):
            with open(shim_path, "rb") as f:
                current_hash = hashlib.sha256(f.read()).hexdigest().upper()

        if current_hash == expected_hash.upper():
            pass
        elif current_hash == intent['pre_effect_hash'] or (current_hash is None and intent['install_sub_path'] == 'ABSENT') or (intent['install_sub_path'] == 'EXTERNAL_COLLISION' and current_hash == actual_hash_of_p):
            if crash_before_fs_write:
                return
            with open(shim_path, "wb") as f:
                f.write(expected_payload)
            if crash_after_fs_write:
                return
        else:
            raise ValueError("ERR_SHIM_EXTERNALLY_MODIFIED")

        with store.unit_of_work() as uow:
            uow.commit_shim_replacement(idempotency_key)
            uow.commit()

def run_restore(store, idempotency_key, shim_registration_id):
    request_digest = "dummy_digest"

    with store.unit_of_work() as uow:
        intent = uow.declare_restore_intent(idempotency_key, request_digest, shim_registration_id)
        uow.commit()

    if intent['operation_state'] == 'INTENT_DECLARED':
        with store.unit_of_work() as uow:
            uow.mark_restore_staged(idempotency_key)
            uow.commit()
            intent['operation_state'] = 'FS_STAGED'

    if intent['operation_state'] == 'FS_STAGED':
        with store.unit_of_work() as uow:
            uow.commit_restore(idempotency_key)
            uow.commit()

if __name__ == "__main__":
    print("--- TRACE DEMONSTRATION START ---")

    with tempfile.TemporaryDirectory() as temp_dir:
        db_path = os.path.join(temp_dir, "test.db")
        store = FakeStateStore(db_path)
        store.initialize()

        with store.unit_of_work() as uow:
            receipt1_chain = [{"canonical_path": "/path/to/bin1"}]
            receipt2_chain = [{"canonical_path": "/path/to/bin2"}]
            uow.conn.execute("INSERT INTO manifest_admission_receipts (admission_receipt_id, manifest_canonical_sha256, schema_version, adapter_id, peer_kind, inventory_generation, trust_root_json, observed_vendor_json, chain_complete, aggregate_chain_digest, timestamp_utc, transitive_executable_chain_json, companion_binaries_json, admitted_at_utc, prov_chain_complete) VALUES (?, 'sha', '2', 'ad', 'pk', 1, '{}', '{}', 0, 'dig', 'time', ?, '[]', 'time', 0)", ("receipt-1", json.dumps(receipt1_chain)))
            uow.conn.execute("INSERT INTO manifest_admission_receipts (admission_receipt_id, manifest_canonical_sha256, schema_version, adapter_id, peer_kind, inventory_generation, trust_root_json, observed_vendor_json, chain_complete, aggregate_chain_digest, timestamp_utc, transitive_executable_chain_json, companion_binaries_json, admitted_at_utc, prov_chain_complete) VALUES (?, 'sha', '2', 'ad', 'pk', 1, '{}', '{}', 0, 'dig', 'time', ?, '[]', 'time', 0)", ("receipt-2", json.dumps(receipt2_chain)))
            uow.commit()

        shim_1 = os.path.join(temp_dir, "shim1.bat")
        shim_2 = os.path.join(temp_dir, "shim2.bat")
        shim_3 = os.path.join(temp_dir, "shim3.bat")
        shim_4 = os.path.join(temp_dir, "shim4.bat")

        print("\n[Scenario A] Admission target validation")
        try:
            run_install(store, "idemp-A", "shim1", shim_1, b"payload", "prof1", "/path/to/wrong", "receipt-1")
            print("FAIL: Expected ValueError")
        except ValueError as e:
            if "Admission target validation failed" in str(e):
                print("PASS: Rejected mismatched downstream_target_path")
            else:
                print(f"FAIL: Wrong error: {e}")

        print("\n[Scenario B] ABSENT sub-path")
        run_install(store, "idemp-B", "shim1", shim_1, b"payload1", "prof1", "/path/to/bin1", "receipt-1")
        print("PASS: Completed ABSENT install")

        print("\n[Scenario C] EXTERNAL_COLLISION sub-path")
        with open(shim_2, "wb") as f:
            f.write(b"foreign")
        run_install(store, "idemp-C", "shim2", shim_2, b"payload2", "prof1", "/path/to/bin1", "receipt-1")
        print("PASS: Completed EXTERNAL_COLLISION install")

        print("\n[Scenario D] MANAGED_UPDATE sub-path (changing bindings)")
        with store.unit_of_work() as uow:
            row_before = uow.conn.execute("SELECT downstream_target_path, profile_name FROM shim_registry_entries WHERE shim_name='shim1'").fetchone()
            print(f"Before: target={row_before['downstream_target_path']}, profile={row_before['profile_name']}")

        run_install(store, "idemp-D", "shim1", shim_1, b"payload1_updated", "prof2", "/path/to/bin2", "receipt-2")

        with store.unit_of_work() as uow:
            row_after = uow.conn.execute("SELECT downstream_target_path, profile_name FROM shim_registry_entries WHERE shim_name='shim1'").fetchone()
            print(f"After: target={row_after['downstream_target_path']}, profile={row_after['profile_name']}")
            if row_after['downstream_target_path'] == "/path/to/bin2" and row_after['profile_name'] == "prof2":
                print("PASS: Bindings updated atomically")
            else:
                print("FAIL: Bindings not updated")

        print("\n[Scenario E] RESTORE deterministic selection")
        with store.unit_of_work() as uow:
            reg_id = uow.conn.execute("SELECT shim_registration_id FROM shim_registry_entries WHERE shim_name='shim2'").fetchone()['shim_registration_id']
            uow.conn.execute("INSERT INTO shim_pending_operations (idempotency_key, request_digest, shim_registration_id, operation_type, install_sub_path, expected_hash, intended_profile_name, intended_downstream_target_path, intended_admission_receipt_id, operation_state, created_at) VALUES ('fake-op', 'dig', ?, 'INSTALL', 'EXTERNAL_COLLISION', 'hash', 'p', 't', 'receipt-1', 'COMPLETED', ?)", (reg_id, now_utc()))
            uow.conn.execute("INSERT INTO shim_backup_entries (shim_registration_id, originating_idempotency_key, target_path, backup_file_path, original_sha256, original_mtime_epoch, original_file_size_bytes, original_permissions_octal, override_reason, backup_created_at) VALUES (?, 'fake-op', 'tgt', 'bak', 'sha', 0, 0, '0', 'rsn', ?)", (reg_id, now_utc()))
            uow.commit()

        run_restore(store, "idemp-E", reg_id)
        with store.unit_of_work() as uow:
            backups = uow.conn.execute("SELECT backup_sequence_id, restored FROM shim_backup_entries WHERE shim_registration_id=? ORDER BY backup_sequence_id", (reg_id,)).fetchall()
            if backups[1]['restored'] == 1 and backups[0]['restored'] == 0:
                print("PASS: Selected most recent unrestored backup deterministically")
            else:
                print("FAIL: Selected wrong backup")

        print("\n[Scenario F] Composite-FK cross-registration rejection")
        with store.unit_of_work() as uow:
            reg1_id = uow.conn.execute("SELECT shim_registration_id FROM shim_registry_entries WHERE shim_name='shim1'").fetchone()['shim_registration_id']
            backup_seq_id = backups[0]['backup_sequence_id']
            try:
                uow.conn.execute("INSERT INTO shim_pending_operations (idempotency_key, request_digest, shim_registration_id, operation_type, selected_backup_sequence_id, operation_state, created_at) VALUES ('bad-fk', 'dig', ?, 'RESTORE', ?, 'INTENT_DECLARED', ?)", (reg1_id, backup_seq_id, now_utc()))
                uow.commit()
                print("FAIL: FK constraint should have failed")
            except sqlite3.IntegrityError as e:
                if "FOREIGN KEY constraint failed" in str(e):
                    print("PASS: Rejected cross-registration backup reference")
                else:
                    print(f"FAIL: Wrong IntegrityError: {e}")

        print("\n[Scenario G] Crash-then-resume (DB FS_STAGED, file replaced)")
        run_install(store, "idemp-G", "shim3", shim_3, b"payload3", "prof1", "/path/to/bin1", "receipt-1", crash_after_fs_write=True)
        with store.unit_of_work() as uow:
            state = uow.conn.execute("SELECT operation_state FROM shim_pending_operations WHERE idempotency_key='idemp-G'").fetchone()['operation_state']
            print(f"Status before resume: {state}")
        run_install(store, "idemp-G", "shim3", shim_3, b"payload3", "prof1", "/path/to/bin1", "receipt-1")
        with store.unit_of_work() as uow:
            state = uow.conn.execute("SELECT operation_state FROM shim_pending_operations WHERE idempotency_key='idemp-G'").fetchone()['operation_state']
            print(f"Status after resume: {state}")
            if state == 'COMPLETED':
                print("PASS: Resumed successfully")
            else:
                print("FAIL: Did not complete")

        print("\n[Scenario H] Crash-then-resume (DB FS_STAGED, file NOT replaced)")
        run_install(store, "idemp-H", "shim4", shim_4, b"payload4", "prof1", "/path/to/bin1", "receipt-1", crash_before_fs_write=True)
        print(f"File exists before resume: {os.path.exists(shim_4)}")
        run_install(store, "idemp-H", "shim4", shim_4, b"payload4", "prof1", "/path/to/bin1", "receipt-1")
        print(f"File exists after resume: {os.path.exists(shim_4)}")
        with store.unit_of_work() as uow:
            state = uow.conn.execute("SELECT operation_state FROM shim_pending_operations WHERE idempotency_key='idemp-H'").fetchone()['operation_state']
            if state == 'COMPLETED' and os.path.exists(shim_4):
                print("PASS: Resumed successfully and wrote file")
            else:
                print("FAIL: Did not complete")

        store._keeper_conn.close()

    print("--- TRACE DEMONSTRATION END ---")