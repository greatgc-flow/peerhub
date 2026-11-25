"""Bridge schema installation, trigger reconciliation, and legacy context migration."""
from __future__ import annotations

import json
import re
import sqlite3
from typing import Any, Callable, cast

from peerhub.extensions import ask_budgets, bridge_registry
from peerhub.extensions.bridge_claims import ClaimStore
from peerhub.extensions.bridge_control import TERMINAL
from peerhub.extensions.bridge_ledger import digest
from peerhub.extensions.schema_guard import refuse_future_schema


_DDL = [
    """CREATE TABLE IF NOT EXISTS bridge_session_events (
        seq INTEGER PRIMARY KEY AUTOINCREMENT, stream_id TEXT NOT NULL, peer_id TEXT NOT NULL, event TEXT NOT NULL,
        from_state TEXT NOT NULL, to_state TEXT NOT NULL, generation INTEGER NOT NULL, detail TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS bridge_deliveries (
        delivery_id TEXT PRIMARY KEY, seq INTEGER NOT NULL, stream_id TEXT NOT NULL, peer_id TEXT NOT NULL,
        record_id TEXT NOT NULL, record_position INTEGER NOT NULL, attempt INTEGER NOT NULL, certainty TEXT NOT NULL,
        claim_generation INTEGER NOT NULL, external_session_id TEXT, session_generation INTEGER, execution_id TEXT,
        result_json TEXT, result_digest TEXT, terminal_at TEXT, response_record_id TEXT, acked INTEGER NOT NULL DEFAULT 0,
        reconcile_record_id TEXT, UNIQUE (stream_id, peer_id, record_id, attempt))""",
    """CREATE TABLE IF NOT EXISTS bridge_evidence (
        seq INTEGER PRIMARY KEY AUTOINCREMENT, delivery_id TEXT, stream_id TEXT NOT NULL, peer_id TEXT NOT NULL,
        kind TEXT NOT NULL, certainty TEXT, detail TEXT NOT NULL)""",
    "CREATE INDEX IF NOT EXISTS idx_be_delivery_seq ON bridge_evidence(delivery_id,seq)",
    "CREATE INDEX IF NOT EXISTS idx_bd_record_peer_attempt ON bridge_deliveries(record_id,peer_id,attempt)",
    "CREATE INDEX IF NOT EXISTS idx_bd_scope_generation ON bridge_deliveries(stream_id,peer_id,session_generation)",
    "CREATE INDEX IF NOT EXISTS idx_be_stream_kind_seq ON bridge_evidence(stream_id,kind,seq)",
    # #330: `diag running` reads only the live claim generation of a scope. No schema version change: older writers on the same
    # store keep it up to date, and the read-only reader works (more slowly) on a store that does not have it yet.
    "CREATE INDEX IF NOT EXISTS idx_bd_scope_claim_generation ON bridge_deliveries(stream_id, peer_id, claim_generation)",
    # A retried invocation can belong to a newer claim than its delivery row.
    "CREATE INDEX IF NOT EXISTS idx_bd_running_scope ON bridge_deliveries(stream_id,peer_id) "
    "WHERE acked=0 AND certainty IN ('NOT_STARTED','STARTED')",
    """CREATE TABLE IF NOT EXISTS bridge_reconciliations (
        delivery_id TEXT PRIMARY KEY, reconcile_record_id TEXT NOT NULL, decision TEXT NOT NULL, consumed_attempt INTEGER)""",
    """CREATE TABLE IF NOT EXISTS bridge_controls (
        record_id TEXT NOT NULL, stream_id TEXT NOT NULL, peer_id TEXT NOT NULL, kind TEXT NOT NULL, position INTEGER NOT NULL,
        effect TEXT NOT NULL, outcome TEXT, error TEXT, delivery_id TEXT, lease_id TEXT, lease_expires REAL,
        attempts INTEGER NOT NULL DEFAULT 0, accepted_seq INTEGER NOT NULL DEFAULT 0, target_delivery_id TEXT,
        target_session_id TEXT, target_claim_generation INTEGER, PRIMARY KEY (record_id, peer_id))""",
    """CREATE TRIGGER IF NOT EXISTS bridge_controls_target_write_once
        BEFORE UPDATE OF target_delivery_id, target_session_id, target_claim_generation ON bridge_controls
        WHEN OLD.target_delivery_id IS NOT NULL
        BEGIN SELECT RAISE(ABORT, 'control target is persisted once and never retargeted'); END""",
    """CREATE TRIGGER IF NOT EXISTS bridge_controls_no_delete BEFORE DELETE ON bridge_controls
        BEGIN SELECT RAISE(ABORT, 'bridge_controls rows are never deleted'); END""",
    """CREATE TRIGGER IF NOT EXISTS bridge_controls_identity_immutable
        BEFORE UPDATE OF record_id, stream_id, peer_id, kind, position, effect, accepted_seq ON bridge_controls
        BEGIN SELECT RAISE(ABORT, 'control intent identity is immutable'); END""",
    """CREATE TRIGGER IF NOT EXISTS bridge_controls_outcome_write_once
        BEFORE UPDATE OF outcome, error, delivery_id ON bridge_controls WHEN OLD.outcome IS NOT NULL
        BEGIN SELECT RAISE(ABORT, 'control outcome is write-once'); END""",
    """CREATE TRIGGER IF NOT EXISTS bridge_evidence_no_update BEFORE UPDATE ON bridge_evidence
        BEGIN SELECT RAISE(ABORT, 'bridge_evidence is append-only'); END""",
    """CREATE TRIGGER IF NOT EXISTS bridge_evidence_no_delete BEFORE DELETE ON bridge_evidence
        BEGIN SELECT RAISE(ABORT, 'bridge_evidence is append-only'); END""",
    """CREATE TRIGGER IF NOT EXISTS bridge_terminal_immutable
        BEFORE UPDATE OF certainty, result_json, result_digest, terminal_at ON bridge_deliveries WHEN OLD.certainty = 'TERMINAL'
        BEGIN SELECT RAISE(ABORT, 'terminal delivery is immutable'); END""",
    """CREATE TRIGGER IF NOT EXISTS bridge_session_events_no_update BEFORE UPDATE ON bridge_session_events
        BEGIN SELECT RAISE(ABORT, 'bridge_session_events is append-only'); END""",
    """CREATE TRIGGER IF NOT EXISTS bridge_session_events_no_delete BEFORE DELETE ON bridge_session_events
        BEGIN SELECT RAISE(ABORT, 'bridge_session_events is append-only'); END""",
    """CREATE TRIGGER IF NOT EXISTS bridge_reconciliations_no_delete BEFORE DELETE ON bridge_reconciliations
        BEGIN SELECT RAISE(ABORT, 'bridge_reconciliations rows are never deleted'); END""",
    """CREATE TRIGGER IF NOT EXISTS bridge_reconciliations_immutable
        BEFORE UPDATE OF delivery_id, decision ON bridge_reconciliations
        BEGIN SELECT RAISE(ABORT, 'authorization is immutable'); END""",
    """CREATE TRIGGER IF NOT EXISTS bridge_reconciliations_valid_insert BEFORE INSERT ON bridge_reconciliations
        WHEN NOT EXISTS (SELECT 1 FROM records n JOIN bridge_deliveries d ON d.delivery_id = NEW.delivery_id
            WHERE n.record_id = NEW.reconcile_record_id AND n.kind = 'control.reconcile' AND n.stream_id = d.stream_id AND n.author_peer_id != d.peer_id AND json_valid(n.body_json)
            AND (json_array_length(n.targets_json) = 0 OR EXISTS (SELECT 1 FROM json_each(n.targets_json) WHERE value = d.peer_id))
            AND json_extract(n.body_json, '$.decision') = 'RETRY' AND json_extract(n.body_json, '$.delivery_id') = NEW.delivery_id)
        BEGIN SELECT RAISE(ABORT, 'authorization must be a durable control.reconcile RETRY for this delivery, not authored by the bridged peer'); END""",
    """CREATE TRIGGER IF NOT EXISTS bridge_reconciliations_consumed_write_once
        BEFORE UPDATE OF consumed_attempt ON bridge_reconciliations WHEN OLD.consumed_attempt IS NOT NULL
        BEGIN SELECT RAISE(ABORT, 'consumed_attempt is write-once (NULL -> attempt only)'); END""",
    """CREATE TRIGGER IF NOT EXISTS bridge_reconciliations_consumed_immutable
        BEFORE UPDATE OF reconcile_record_id ON bridge_reconciliations WHEN OLD.consumed_attempt IS NOT NULL OR NOT EXISTS (
            SELECT 1 FROM records n JOIN records o ON o.record_id = OLD.reconcile_record_id
            JOIN bridge_deliveries d ON d.delivery_id = NEW.delivery_id
            WHERE n.record_id = NEW.reconcile_record_id AND n.position > o.position AND n.stream_id = o.stream_id AND n.kind = 'control.reconcile' AND n.stream_id = d.stream_id AND n.author_peer_id != d.peer_id AND json_valid(n.body_json)
            AND (json_array_length(n.targets_json) = 0 OR EXISTS (SELECT 1 FROM json_each(n.targets_json) WHERE value = d.peer_id))
            AND json_extract(n.body_json, '$.decision') = 'RETRY' AND json_extract(n.body_json, '$.delivery_id') = NEW.delivery_id)
        BEGIN SELECT RAISE(ABORT, 'authorization may only move to a NEWER valid control.reconcile RETRY for this delivery, never once consumed'); END""",
    """CREATE TRIGGER IF NOT EXISTS bridge_evidence_no_replace BEFORE INSERT ON bridge_evidence
        WHEN EXISTS (SELECT 1 FROM bridge_evidence WHERE seq = NEW.seq)
        BEGIN SELECT RAISE(ABORT, 'bridge_evidence is append-only'); END""",
    """CREATE TRIGGER IF NOT EXISTS bridge_session_events_no_replace BEFORE INSERT ON bridge_session_events
        WHEN EXISTS (SELECT 1 FROM bridge_session_events WHERE seq = NEW.seq)
        BEGIN SELECT RAISE(ABORT, 'bridge_session_events is append-only'); END""",
    """CREATE TRIGGER IF NOT EXISTS bridge_deliveries_no_replace BEFORE INSERT ON bridge_deliveries
        WHEN EXISTS (SELECT 1 FROM bridge_deliveries WHERE delivery_id = NEW.delivery_id OR (stream_id = NEW.stream_id AND peer_id = NEW.peer_id AND record_id = NEW.record_id AND attempt = NEW.attempt))
        BEGIN SELECT RAISE(ABORT, 'bridge_deliveries rows are never replaced'); END""",
    """CREATE TRIGGER IF NOT EXISTS bridge_reconciliations_no_replace BEFORE INSERT ON bridge_reconciliations
        WHEN EXISTS (SELECT 1 FROM bridge_reconciliations WHERE delivery_id = NEW.delivery_id)
        BEGIN SELECT RAISE(ABORT, 'bridge_reconciliations rows are never replaced'); END""",
    """CREATE TRIGGER IF NOT EXISTS bridge_controls_no_replace BEFORE INSERT ON bridge_controls
        WHEN EXISTS (SELECT 1 FROM bridge_controls WHERE record_id = NEW.record_id AND peer_id = NEW.peer_id)
        BEGIN SELECT RAISE(ABORT, 'bridge_controls rows are never replaced'); END""",
    """CREATE TABLE IF NOT EXISTS bridge_invocations (
        delivery_id TEXT NOT NULL, invocation_no INTEGER NOT NULL, stream_id TEXT NOT NULL, peer_id TEXT NOT NULL,
        claim_generation INTEGER NOT NULL CHECK (claim_generation >= 0), external_session_id TEXT, session_generation INTEGER,
        PRIMARY KEY (delivery_id, invocation_no), CHECK (invocation_no >= 1))""",
    """CREATE TABLE IF NOT EXISTS bridge_invocation_resolutions (
        delivery_id TEXT NOT NULL, invocation_no INTEGER NOT NULL CHECK (invocation_no >= 1),
        reason TEXT NOT NULL CHECK (reason IN ('prespawn_failure', 'control_halt', 'promoted_may_have_started')),
        claim_generation INTEGER NOT NULL CHECK (claim_generation >= 0),
        PRIMARY KEY (delivery_id, invocation_no))""",
    """CREATE TRIGGER IF NOT EXISTS bridge_invocations_no_update BEFORE UPDATE ON bridge_invocations
        BEGIN SELECT RAISE(ABORT, 'bridge_invocations is append-only'); END""",
    """CREATE TRIGGER IF NOT EXISTS bridge_invocations_no_delete BEFORE DELETE ON bridge_invocations
        BEGIN SELECT RAISE(ABORT, 'bridge_invocations is append-only'); END""",
    """CREATE TRIGGER IF NOT EXISTS bridge_invocations_no_replace BEFORE INSERT ON bridge_invocations
        WHEN EXISTS (SELECT 1 FROM bridge_invocations WHERE delivery_id = NEW.delivery_id AND invocation_no = NEW.invocation_no)
        BEGIN SELECT RAISE(ABORT, 'bridge_invocations rows are never replaced'); END""",
    """CREATE TRIGGER IF NOT EXISTS bridge_invocation_resolutions_no_update BEFORE UPDATE ON bridge_invocation_resolutions
        BEGIN SELECT RAISE(ABORT, 'bridge_invocation_resolutions is append-only'); END""",
    """CREATE TRIGGER IF NOT EXISTS bridge_invocation_resolutions_no_delete BEFORE DELETE ON bridge_invocation_resolutions
        BEGIN SELECT RAISE(ABORT, 'bridge_invocation_resolutions is append-only'); END""",
    """CREATE TRIGGER IF NOT EXISTS bridge_invocation_resolutions_no_replace BEFORE INSERT ON bridge_invocation_resolutions
        WHEN EXISTS (SELECT 1 FROM bridge_invocation_resolutions WHERE delivery_id = NEW.delivery_id AND invocation_no = NEW.invocation_no)
        BEGIN SELECT RAISE(ABORT, 'bridge_invocation_resolutions rows are never replaced'); END""",
    """CREATE TRIGGER IF NOT EXISTS bridge_invocation_resolutions_verifiable BEFORE INSERT ON bridge_invocation_resolutions
        WHEN NOT (
            EXISTS (SELECT 1 FROM bridge_invocations i WHERE i.delivery_id = NEW.delivery_id AND i.invocation_no = NEW.invocation_no)
            AND EXISTS (SELECT 1 FROM bridge_deliveries d WHERE d.delivery_id = NEW.delivery_id AND (
                (NEW.reason IN ('prespawn_failure', 'control_halt') AND d.certainty = 'NOT_STARTED' AND d.execution_id IS NULL)
                OR (NEW.reason = 'promoted_may_have_started' AND d.certainty != 'NOT_STARTED')))
            AND EXISTS (SELECT 1 FROM bridge_evidence e WHERE e.delivery_id = NEW.delivery_id AND json_valid(e.detail)
                AND json_extract(e.detail, '$.invocation_no') = NEW.invocation_no
                AND e.kind = CASE NEW.reason WHEN 'prespawn_failure' THEN 'prespawn_failure' WHEN 'control_halt' THEN 'control_halt'
                                             ELSE 'invocation_unresolved_recovered' END))
        BEGIN SELECT RAISE(ABORT, 'a resolution needs the open marker, a matching certainty state and its evidence'); END""",
    """CREATE TRIGGER IF NOT EXISTS bridge_deliveries_certainty_forward_only BEFORE UPDATE OF certainty ON bridge_deliveries
        WHEN NEW.certainty != OLD.certainty AND NOT (
            (OLD.certainty = 'NOT_STARTED' AND NEW.certainty IN ('MAY_HAVE_STARTED', 'STARTED'))
            OR (OLD.certainty = 'MAY_HAVE_STARTED' AND NEW.certainty IN ('STARTED', 'TERMINAL'))
            OR (OLD.certainty = 'STARTED' AND NEW.certainty = 'TERMINAL'))
        BEGIN SELECT RAISE(ABORT, 'execution certainty is forward-only (no downgrade, no NOT_STARTED to TERMINAL)'); END""",
    """CREATE TRIGGER IF NOT EXISTS bridge_deliveries_insert_not_started BEFORE INSERT ON bridge_deliveries WHEN NEW.certainty != 'NOT_STARTED'
        BEGIN SELECT RAISE(ABORT, 'an attempt starts NOT_STARTED'); END""",
    """CREATE TRIGGER IF NOT EXISTS bridge_deliveries_no_rowid_replace BEFORE INSERT ON bridge_deliveries
        WHEN NEW.rowid IS NOT NULL AND EXISTS (SELECT 1 FROM bridge_deliveries WHERE rowid = NEW.rowid)
        BEGIN SELECT RAISE(ABORT, 'bridge_deliveries rows are never replaced'); END""",
    """CREATE TRIGGER IF NOT EXISTS bridge_reconciliations_no_rowid_replace BEFORE INSERT ON bridge_reconciliations
        WHEN NEW.rowid IS NOT NULL AND EXISTS (SELECT 1 FROM bridge_reconciliations WHERE rowid = NEW.rowid)
        BEGIN SELECT RAISE(ABORT, 'bridge_reconciliations rows are never replaced'); END""",
    """CREATE TRIGGER IF NOT EXISTS bridge_controls_no_rowid_replace BEFORE INSERT ON bridge_controls
        WHEN NEW.rowid IS NOT NULL AND EXISTS (SELECT 1 FROM bridge_controls WHERE rowid = NEW.rowid)
        BEGIN SELECT RAISE(ABORT, 'bridge_controls rows are never replaced'); END""",
    """CREATE TRIGGER IF NOT EXISTS bridge_invocations_no_rowid_replace BEFORE INSERT ON bridge_invocations
        WHEN NEW.rowid IS NOT NULL AND EXISTS (SELECT 1 FROM bridge_invocations WHERE rowid = NEW.rowid)
        BEGIN SELECT RAISE(ABORT, 'bridge_invocations rows are never replaced'); END""",
    """CREATE TRIGGER IF NOT EXISTS bridge_invocation_resolutions_no_rowid_replace BEFORE INSERT ON bridge_invocation_resolutions
        WHEN NEW.rowid IS NOT NULL AND EXISTS (SELECT 1 FROM bridge_invocation_resolutions WHERE rowid = NEW.rowid)
        BEGIN SELECT RAISE(ABORT, 'bridge_invocation_resolutions rows are never replaced'); END""",
    """CREATE TRIGGER IF NOT EXISTS bridge_deliveries_no_delete BEFORE DELETE ON bridge_deliveries
        BEGIN SELECT RAISE(ABORT, 'bridge_deliveries rows are never deleted'); END""",
]


_POST_REGISTRY_DDL = (
    """CREATE TRIGGER IF NOT EXISTS bridge_delivery_session_immutable
                BEFORE UPDATE OF external_session_id,session_generation,session_handle,session_snapshot_json ON bridge_deliveries
                WHEN OLD.certainty!='NOT_STARTED' OR OLD.result_digest IS NOT NULL OR
                    EXISTS(SELECT 1 FROM bridge_invocations i WHERE i.delivery_id=OLD.delivery_id AND NOT EXISTS
                        (SELECT 1 FROM bridge_invocation_resolutions r WHERE r.delivery_id=i.delivery_id AND r.invocation_no=i.invocation_no))
                BEGIN SELECT RAISE(ABORT,'accepted session is immutable after execution or settlement'); END""",
    """CREATE TRIGGER IF NOT EXISTS bridge_result_write_once
                BEFORE UPDATE OF result_json,result_digest ON bridge_deliveries WHEN OLD.result_digest IS NOT NULL
                BEGIN SELECT RAISE(ABORT,'settled result is immutable'); END""",
)


def initialize(claims: ClaimStore, fire: Callable[[str], None]) -> None:
    refuse_future_schema(claims.db_path)  # MIG-003: refuse a future schema before any DDL/DML
    with claims.transaction() as conn:
        owned = [m.group(1) for stmt in _DDL if (m := re.match(r"\s*CREATE TABLE IF NOT EXISTS (\w+)", stmt))]
        marks = ",".join("?" * len(owned))
        # Compare complete SQL, not only names or a cached schema version.
        # Only remove SQLite's CREATE-prefix IF NOT EXISTS; never normalize
        # body whitespace, which may be significant inside string literals.
        expected = {
            match.group(1): stmt.replace("CREATE TRIGGER IF NOT EXISTS ", "CREATE TRIGGER ", 1)
            for stmt in (*_DDL, *_POST_REGISTRY_DDL)
            if (match := re.match(r"CREATE TRIGGER IF NOT EXISTS (\w+)", stmt))
        }
        installed = dict(conn.execute(
            f"SELECT name,sql FROM sqlite_master WHERE type='trigger' AND tbl_name IN ({marks})", owned))
        session_columns = {row[1] for row in conn.execute("PRAGMA table_info(bridge_sessions)")}
        legacy_registry = bool(session_columns) and "session_handle" not in session_columns
        # Conservative choice: legacy registries keep the pre-0.24.8 full
        # rebuild INSIDE this transaction. The upgrade itself writes only
        # bridge_sessions/bridge_session_events, not delivery metadata.
        if installed != expected or legacy_registry:
            for name in installed:
                quoted = '"' + name.replace('"', '""') + '"'
                conn.execute(f"DROP TRIGGER {quoted}")
        for stmt in _DDL:
            conn.execute(stmt)
        bridge_registry.initialize(conn, upgrade_session_context, fire)
        ask_budgets.install_ask_metrics(conn)
        for stmt in _POST_REGISTRY_DDL:
            conn.execute(stmt)


def _reconstruct_consumed_prompt(conn: sqlite3.Connection, m: sqlite3.Row) -> int | None:
    """Require exact terminal identity and complete ordered context for every turn.

    Historical same-peer exclusions and response watermarks are not proof.
    A missing receipt, skipped record or uncertain attempt invalidates the chain.
    """
    if m["state"] not in ("ACTIVE", "FRESH") or m["vendor_session_id"] is None or m["bootstrap_truncated"] != 0:
        return None
    deliveries = conn.execute(
        "SELECT * FROM bridge_deliveries WHERE stream_id=? AND peer_id=? AND session_generation=? ORDER BY seq",
        (m["stream_id"], m["peer_id"], m["session_generation"])).fetchall()
    watermark = 0
    for index, d in enumerate(deliveries):
        if d["certainty"] != TERMINAL or d["external_session_id"] != m["external_session_id"] or d["record_position"] <= watermark:
            return None
        receipts = conn.execute("SELECT detail FROM bridge_evidence WHERE delivery_id=? AND kind='terminal'", (d["delivery_id"],)).fetchall()
        if len(receipts) != 1:
            return None
        try:
            detail = json.loads(receipts[0][0])
            result = json.loads(d["result_json"])
            if not isinstance(detail, dict) or not isinstance(result, dict):
                return None
            detail = cast("dict[str, Any]", detail)
            if detail.get("digest") != d["result_digest"] or digest(result) != d["result_digest"]:
                return None
            bound = detail.get("session_bound")
            if not isinstance(bound, dict):
                return None
            bound = cast("dict[str, Any]", bound)
            if any(bound.get(key) != value for key, value in {
                "vendor_session_digest": digest(m["vendor_session_id"]), "watermark": d["record_position"],
                "binding": m["binding"], "adapter_fingerprint": m["adapter_fingerprint"],
                "session_generation": m["session_generation"], "claim_generation": d["claim_generation"],
                "execution_id": d["execution_id"],
            }.items()):
                return None
            if detail.get("effective_mode") != ("fresh" if index == 0 else "resumed") or detail.get("bootstrap_truncated") is not False:
                return None
            record = conn.execute("SELECT record_id FROM records WHERE stream_id=? AND position=?",
                                  (m["stream_id"], d["record_position"])).fetchone()
            if record is None or record[0] != d["record_id"]:
                return None
            prior = conn.execute("SELECT record_id, kind, targets_json FROM records WHERE stream_id=? AND position>? AND position<? ORDER BY position",
                                 (m["stream_id"], watermark, d["record_position"])).fetchall()
            expected = [r["record_id"] for r in prior if index == 0 or r["kind"] != "control.redirect"
                        or not json.loads(r["targets_json"]) or m["peer_id"] in json.loads(r["targets_json"])]
            if detail.get("injected_record_ids") != expected:
                return None
        except (ValueError, TypeError):
            return None
        watermark = d["record_position"]
    return watermark or None


def upgrade_session_context(conn: sqlite3.Connection) -> None:
    """Writer-owned, atomic upgrade; capability alone never proves consumed context.

    Old terminal receipts must prove complete context, not just delivery;
    deltas may have skipped same-peer responses or concurrent records below
    a response-position watermark. Unprovable chains are invalidated.
    Local custom sessions keep their delivery-evidence continuity contract.
    """
    for m in conn.execute("SELECT * FROM bridge_sessions WHERE context_watermark_version=0").fetchall():
        cli = m["runtime_kind"] in ("cc", "cx", "ag")
        native = cli or m["vendor_session_id"] is not None
        reconstructed = _reconstruct_consumed_prompt(conn, m) if native else None
        lost = native and reconstructed is None
        conn.execute(
            "UPDATE bridge_sessions SET resumable=CASE WHEN runtime_kind IN ('cc','cx','ag') THEN 1 ELSE resumable END, "
            "state=?, vendor_session_id=?, context_watermark=?, bootstrap_truncated=?, usage_json=?, context_watermark_version=1 "
            "WHERE stream_id=? AND peer_id=? AND context_watermark_version=0",
            ("LOST" if lost else m["state"], None if lost else m["vendor_session_id"],
             None if lost else reconstructed if native else m["context_watermark"], None if lost else m["bootstrap_truncated"],
             None if lost else m["usage_json"], m["stream_id"], m["peer_id"]))
        if lost:
            conn.execute(
                "INSERT INTO bridge_session_events (stream_id, peer_id, event, from_state, to_state, generation, detail) "
                "VALUES (?,?,'session_lost',?,'LOST',?,?)",
                (m["stream_id"], m["peer_id"], m["state"], m["session_generation"],
                 json.dumps({"reason": "continuity_unproven"}, sort_keys=True)))
