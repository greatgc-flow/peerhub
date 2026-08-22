"""Independent adversarial probes against the Round 103 delivered implementation."""
import os, sys, json, sqlite3, tempfile, hashlib
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import r103
from r103 import FakeStateStore, run_install, run_restore, now_utc

def seed(store):
    with store.unit_of_work() as uow:
        for rid, p in (("receipt-1", "/path/to/bin1"), ("receipt-2", "/path/to/bin2")):
            uow.conn.execute(
                "INSERT INTO manifest_admission_receipts (admission_receipt_id, manifest_canonical_sha256,"
                " schema_version, adapter_id, peer_kind, inventory_generation, trust_root_json,"
                " observed_vendor_json, chain_complete, aggregate_chain_digest, timestamp_utc,"
                " transitive_executable_chain_json, companion_binaries_json, admitted_at_utc,"
                " prov_chain_complete) VALUES (?, 'sha','2','ad','pk',1,'{}','{}',0,'dig','t',?,'[]','t',0)",
                (rid, json.dumps([{"canonical_path": p}])))
        uow.commit()

print("=" * 72)
print("PROBE 1: EXTERNAL_COLLISION crash-resume WITH external tampering in between")
print("  Spec (consolidation doc L1052-1054): step 4 must compare actual_hash against")
print("  the pre-effect reference (original_sha256 for EXTERNAL_COLLISION) and abort")
print("  ERR_SHIM_EXTERNALLY_MODIFIED on 'any other outcome'.")
print("=" * 72)
with tempfile.TemporaryDirectory() as td:
    store = FakeStateStore(os.path.join(td, "t.db")); store.initialize(); seed(store)
    P = os.path.join(td, "cc.bat")
    with open(P, "wb") as f:
        f.write(b"ORIGINAL FOREIGN FILE")          # the pre-existing colliding file
    # attempt 1: crash after intent + backup staging, before the FS write
    run_install(store, "k1", "cc", P, b"NEW SHIM PAYLOAD", "prof", "/path/to/bin1",
                "receipt-1", crash_before_fs_write=True)
    with store.unit_of_work() as uow:
        st = uow.conn.execute("SELECT operation_state FROM shim_pending_operations WHERE idempotency_key='k1'").fetchone()[0]
    print(f"  state after simulated crash: {st}")
    print(f"  P content now: {open(P,'rb').read()!r}")
    # >>> an external process tampers with the colliding file while we were down <<<
    with open(P, "wb") as f:
        f.write(b"ATTACKER SUBSTITUTED CONTENT")
    print(f"  external writer replaced P with: {open(P,'rb').read()!r}")
    try:
        run_install(store, "k1", "cc", P, b"NEW SHIM PAYLOAD", "prof", "/path/to/bin1", "receipt-1")
        print(f"  RESULT: resume COMPLETED. P is now: {open(P,'rb').read()!r}")
        print("  >>> NO ERR_SHIM_EXTERNALLY_MODIFIED RAISED. Tampering silently overwritten. <<<")
    except ValueError as e:
        print(f"  RESULT: correctly aborted -> {e}")
    with store.unit_of_work() as uow:
        b = uow.conn.execute("SELECT original_sha256 FROM shim_backup_entries WHERE originating_idempotency_key='k1'").fetchone()
        print(f"  DB-persisted original_sha256 (the reference the spec says to use): {b[0]}")
        print(f"  sha256 of attacker content                                      : "
              f"{hashlib.sha256(b'ATTACKER SUBSTITUTED CONTENT').hexdigest().upper()}")
    store._keeper_conn.close()

print()
print("=" * 72)
print("PROBE 2: abandoned ABSENT install -> can the shim_name ever be recovered?")
print("=" * 72)
with tempfile.TemporaryDirectory() as td:
    store = FakeStateStore(os.path.join(td, "t.db")); store.initialize(); seed(store)
    P = os.path.join(td, "cc.bat")
    # user runs `peerhub shim add cc`, process is killed after intent commit
    run_install(store, "kA", "cc", P, b"PAYLOAD", "prof", "/path/to/bin1",
                "receipt-1", crash_before_fs_write=True)
    with store.unit_of_work() as uow:
        st = uow.conn.execute("SELECT operation_state, install_sub_path FROM shim_pending_operations WHERE idempotency_key='kA'").fetchone()
        rg = uow.conn.execute("SELECT shim_registration_id, status FROM shim_registry_entries WHERE shim_name='cc'").fetchone()
    print(f"  pending op: state={st[0]} sub_path={st[1]}")
    print(f"  registry row: status={rg[1]}  (visible as ACTIVE but not consumable)")
    print(f"  file on disk: exists={os.path.exists(P)}")
    print()
    print("  (2a) user re-runs the command; CLI mints a FRESH idempotency key:")
    try:
        run_install(store, "kB", "cc", P, b"PAYLOAD", "prof", "/path/to/bin1", "receipt-1")
        print("       -> succeeded")
    except Exception as e:
        print(f"       -> BLOCKED: {type(e).__name__}: {e}")
    print("  (2b) user re-runs wanting DIFFERENT bindings (same key, new digest):")
    try:
        run_install(store, "kA", "cc", P, b"OTHER", "prof2", "/path/to/bin2", "receipt-2")
        print("       -> succeeded")
    except Exception as e:
        print(f"       -> BLOCKED: {type(e).__name__}: {e}")
    print("  (2c) user tries RESTORE to undo the wedged registration:")
    try:
        run_restore(store, "kR", rg[0])
        print("       -> succeeded")
    except Exception as e:
        print(f"       -> BLOCKED: {type(e).__name__}: {e}")
    print("  (2d) is there any other defined operation? REMOVE is deferred (schema CHECK")
    print("       allows only INSTALL|RESTORE), so no.")
    with store.unit_of_work() as uow:
        try:
            uow.conn.execute(
                "INSERT INTO shim_pending_operations (idempotency_key, request_digest,"
                " shim_registration_id, operation_type, operation_state, created_at)"
                " VALUES ('kX','d',?, 'REMOVE','INTENT_DECLARED',?)", (rg[0], now_utc()))
            uow.commit(); print("       -> REMOVE accepted?!")
        except sqlite3.IntegrityError as e:
            print(f"       -> REMOVE rejected by schema: {e}")
    store._keeper_conn.close()

print()
print("=" * 72)
print("PROBE 3: is the same wedge reachable for a plain successful install that is")
print("         later interrupted mid-MANAGED_UPDATE?")
print("=" * 72)
with tempfile.TemporaryDirectory() as td:
    store = FakeStateStore(os.path.join(td, "t.db")); store.initialize(); seed(store)
    P = os.path.join(td, "cc.bat")
    run_install(store, "k1", "cc", P, b"V1", "prof", "/path/to/bin1", "receipt-1")
    print("  initial install COMPLETED, shim live on disk")
    run_install(store, "k2", "cc", P, b"V2", "prof", "/path/to/bin1", "receipt-1",
                crash_before_fs_write=True)
    print("  MANAGED_UPDATE interrupted (crash before FS write)")
    print("  (3a) retry with a fresh idempotency key:")
    try:
        run_install(store, "k3", "cc", P, b"V2", "prof", "/path/to/bin1", "receipt-1")
        print("       -> succeeded")
    except Exception as e:
        print(f"       -> BLOCKED: {type(e).__name__}: {e}")
    print("  (3b) meanwhile, is the shim still usable per the visibility rule (L1034)?")
    with store.unit_of_work() as uow:
        n = uow.conn.execute(
            "SELECT COUNT(*) FROM shim_registry_entries r JOIN shim_pending_operations o"
            " ON o.shim_registration_id=r.shim_registration_id AND o.operation_state!='COMPLETED'"
            " WHERE r.shim_name='cc' AND r.status='ACTIVE'").fetchone()[0]
    print(f"       -> in-flight ops on this ACTIVE row: {n}  => consumers must treat it as UNUSABLE")
    print(f"       -> but the file on disk is the perfectly good V1 shim: {open(P,'rb').read()!r}")
    store._keeper_conn.close()
