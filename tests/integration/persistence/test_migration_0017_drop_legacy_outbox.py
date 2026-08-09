"""Step F Verification Gate: Migration 0017 drop legacy outbox tables and automated backup/restore drill."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from peerhub.core.protocol import (
    PROTOCOL_MAJOR,
    PROTOCOL_MINOR,
    SCHEMA_VERSION,
)
from peerhub.dispatch.contract import (
    ArtifactManifestRecord,
    ArtifactMetadata,
    ArtifactState,
    OutboxCheckpoint,
)
from peerhub.governance.contract import (
    EffectIntent,
    EffectOutcome,
    MutationRequest,
    OutboxState,
)
from peerhub.persistence.sqlite import SqliteStateStore


def _table_names(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table'"
    ).fetchall()
    return {row[0] for row in rows}


def test_migration_0017_drops_legacy_outbox_and_verifies_backup_restore(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "migration_0017_test.sqlite3"
    backup_path = tmp_path / "migration_0017_backup_v16.sqlite3"
    restored_path = tmp_path / "migration_0017_restored_v16.sqlite3"

    migrations_dir = Path(__file__).parent.parent.parent.parent / "peerhub" / "persistence" / "migrations"

    # Step 1: Initialize database up to version 16
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    for v in range(1, 17):
        sql_file = next(migrations_dir.glob(f"{v:04d}_*.sql"))
        conn.executescript(sql_file.read_text(encoding="utf-8"))

    # Assert schema is at version 16 and legacy outbox tables exist
    user_version_v16 = conn.execute("PRAGMA user_version").fetchone()[0]
    assert user_version_v16 == 16
    tables_v16 = _table_names(conn)
    assert "outbox_events" in tables_v16
    assert "outbox_checkpoints" in tables_v16
    assert "event_log" in tables_v16
    assert "effect_deliveries" in tables_v16
    assert "effect_receipts" in tables_v16
    assert "consumer_offsets" in tables_v16

    # Step 2: Populate full operational state in the v16 DB across all relational boundaries
    event_id = "11111111-1111-4111-8111-111111111111"
    request_id = "req-step-f-1"
    receipt_id = "receipt-step-f-1"
    attempt_id = "attempt-step-f-1"
    command_id = "cmd-step-f-1"
    topic = "governance.effect.step-f"

    # Insert governance mutation request, plan, transition receipt
    conn.execute(
        """
        INSERT INTO mutation_requests (
            request_id, command_id, correlation_id, client_id, command_type,
            idempotency_key, actor_id, policy_revision, target_id,
            expected_revision, operation, desired_state_json, effect_kind,
            effect_payload_json, payload_digest, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            request_id, command_id, "corr-1", "client-1", "test.op",
            "idemp-1", "actor-1", "1", "target-1",
            0, "set", json.dumps({"key": "val"}), "effect",
            json.dumps({"kind": "effect"}), "digest-1", 100,
        ),
    )
    conn.execute(
        """
        INSERT INTO mutation_plans (
            plan_id, request_id, request_digest, target_id, previous_revision,
            next_revision, next_state_json, effect_kind, effect_payload_json, planned_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "plan-step-f-1", request_id, "digest-1", "target-1", 0,
            1, json.dumps({"key": "val"}), "effect", json.dumps({"kind": "effect"}), 100,
        ),
    )
    conn.execute(
        """
        INSERT INTO transition_receipts (
            receipt_id, request_id, plan_id, target_id, previous_revision,
            next_revision, status, committed_at, outbox_event_id, evidence_refs_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            receipt_id, request_id, "plan-step-f-1", "target-1", 0,
            1, "COMMITTED_ENFORCEMENT_PENDING", 100, event_id, json.dumps([]),
        ),
    )

    # Insert canonical event_log and effect_deliveries
    conn.execute(
        """
        INSERT INTO event_log (
            outbox_position, event_id, protocol_major, protocol_minor, schema_version,
            correlation_id, occurred_at, event_kind, payload_json, request_id,
            evidence_refs_json, appended_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            1, event_id, PROTOCOL_MAJOR, PROTOCOL_MINOR, SCHEMA_VERSION,
            "corr-1", 100, "DISPATCH_INTENT", json.dumps({"manifest_digest": "sha256:digest"}), request_id,
            json.dumps([]), 100,
        ),
    )
    conn.execute(
        """
        INSERT INTO effect_deliveries (
            event_id, outbox_position, request_id, transition_receipt_id, topic,
            claimed_by, claim_attempt_id, claimed_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event_id, 1, request_id, receipt_id, topic,
            "owner-step-f", "attempt-step-f", 100,
        ),
    )
    conn.execute(
        """
        INSERT INTO effect_receipts (
            effect_receipt_id, request_id, outbox_event_id, attempt_id, owner_id,
            outcome, completed_at, evidence_refs_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "ereceipt-step-f-1", request_id, event_id, "attempt-step-f", "owner-step-f",
            "EFFECT_SUCCEEDED", 100, json.dumps([]),
        ),
    )
    conn.execute(
        """
        INSERT INTO consumer_offsets (
            consumer_id, outbox_position, event_id, revision
        ) VALUES (?, ?, ?, ?)
        """,
        ("consumer-step-f", 1, event_id, 1),
    )

    # Insert legacy outbox rows to verify pre-drop presence
    conn.execute(
        """
        INSERT INTO outbox_events (
            outbox_position, event_id, protocol_major, protocol_minor, schema_version,
            correlation_id, occurred_at, event_kind, payload_json, request_id,
            evidence_refs_json, transition_receipt_id, topic, state, created_at,
            claimed_by, claim_attempt_id, claimed_at, consumed_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            1, event_id, PROTOCOL_MAJOR, PROTOCOL_MINOR, SCHEMA_VERSION,
            "corr-1", 100, "governance.effect.step-f", json.dumps({"payload": 1}), request_id,
            json.dumps([]), receipt_id, topic, "CONSUMED", 100,
            "owner-step-f", "attempt-step-f", 100, 100,
        ),
    )
    conn.execute(
        """
        INSERT INTO outbox_checkpoints (
            consumer_id, outbox_position, event_id, revision
        ) VALUES (?, ?, ?, ?)
        """,
        ("consumer-legacy", 1, event_id, 1),
    )

    # Insert dispatch lease, request, attempt, manifest
    lease_id = "lease-step-f-1"
    conn.execute(
        """
        INSERT INTO leases (
            lease_id, session_id, command_id, fencing_token, authority_epoch,
            revision, owner_principal_id, owner_instance_id, owner_peer_id,
            state, heartbeat_expires_at, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            lease_id, "session-1", command_id, 1, 1,
            1, "princ-1", "inst-1", "peer-1",
            "RESERVED", 5000, 100, 100,
        ),
    )
    conn.execute(
        """
        INSERT INTO dispatch_requests (
            command_id, client_id, client_request_id, correlation_id, authenticated_principal,
            command_type, idempotency_key, payload_digest, scope_json, params_json,
            expected_policy_revision_json, expected_configuration_revision_json,
            policy_revision_json, configuration_revision_json, completion_contract_json,
            selected_peer_instance_id, selected_profile_id, route_decision_digest,
            lease_id, state, revision, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            command_id, "client-1", "client-req-1", "corr-1", "princ-1",
            "peer.ask", "idemp-disp-1", "digest-p", json.dumps({}), json.dumps({}),
            json.dumps(1), json.dumps(1), json.dumps(1), json.dumps(1),
            json.dumps({"kind": "DELIVERY_ONLY"}), "inst-1", "prof-1", "d" * 64,
            lease_id, "ADMITTED", 1, 100, 100,
        ),
    )
    conn.execute(
        """
        INSERT INTO dispatch_attempts (
            attempt_id, command_id, attempt_number, lease_id,
            state, execution_certainty, revision, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            attempt_id, command_id, 1, lease_id,
            "RUNNING", "STARTED", 1, 100, 100,
        ),
    )
    conn.execute(
        """
        INSERT INTO dispatch_artifact_manifests (
            attempt_id, workspace_scope_id, staging_root_ref, manifest_digest,
            item_count, intent_event_id, created_at, revision
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            attempt_id, "scope-1", "staging://root", "sha256:digest",
            1, event_id, 100, 1,
        ),
    )
    conn.commit()

    # Step 3: Take SQLite online backup snapshot of the v16 DB before applying migration 0017
    backup_conn = sqlite3.connect(backup_path)
    conn.backup(backup_conn)
    backup_conn.close()

    # Step 4: Apply migration 0017 to the live database
    migration_17_sql = (migrations_dir / "0017_drop_legacy_outbox.sql").read_text(encoding="utf-8")
    conn.executescript(migration_17_sql)

    # Assert migration 0017 results:
    # 1. Schema version is 17
    user_version_v17 = conn.execute("PRAGMA user_version").fetchone()[0]
    assert user_version_v17 == 17
    schema_row = conn.execute("SELECT name FROM schema_migrations WHERE version = 17").fetchone()
    assert schema_row is not None and schema_row[0] == "0017_drop_legacy_outbox"

    # 2. Legacy outbox tables are dropped
    tables_v17 = _table_names(conn)
    assert "outbox_events" not in tables_v17
    assert "outbox_checkpoints" not in tables_v17

    # 3. Foreign key integrity holds with zero violations
    fk_violations = conn.execute("PRAGMA foreign_key_check").fetchall()
    assert fk_violations == []
    conn.close()

    # Step 5: Verify post-drop store operations on v17 database via SqliteStateStore
    v17_store = SqliteStateStore(db_path, workspace_home_id="workspace-step-f-test")
    v17_store.initialize()
    with v17_store.unit_of_work() as unit:
        delivery = unit.get_effect_delivery(event_id)
        assert delivery is not None
        assert delivery.state is OutboxState.CONSUMED

        digest = unit.get_artifact_recovery_digest(attempt_id)
        assert digest is not None
        assert digest.intent_event_id == event_id
        assert digest.intent_event_verified is True

        checkpoint_v17 = unit.get_outbox_checkpoint("consumer-step-f")
        assert checkpoint_v17 is not None
        assert checkpoint_v17.event_id == event_id

        # Facade methods list correctly from event_log
        events = unit.list_outbox_events((OutboxState.CONSUMED,), limit=10)
        assert len(events) == 1
        assert events[0].event_id == event_id
    v17_store.close()

    # Step 6: Automated Restore Drill: Restore from v16 snapshot and verify pre-drop state
    restore_target = sqlite3.connect(restored_path)
    restore_source = sqlite3.connect(backup_path)
    restore_source.backup(restore_target)
    restore_source.close()

    # Assert restored database is at v16 with tables and foreign keys completely intact
    user_version_restored = restore_target.execute("PRAGMA user_version").fetchone()[0]
    assert user_version_restored == 16
    tables_restored = _table_names(restore_target)
    assert "outbox_events" in tables_restored
    assert "outbox_checkpoints" in tables_restored
    assert "event_log" in tables_restored
    assert "effect_deliveries" in tables_restored

    restored_fk_violations = restore_target.execute("PRAGMA foreign_key_check").fetchall()
    assert restored_fk_violations == []

    # Verify rows in restored legacy tables
    legacy_event = restore_target.execute("SELECT event_id, state FROM outbox_events WHERE event_id = ?", (event_id,)).fetchone()
    assert legacy_event is not None and legacy_event[0] == event_id
    restore_target.close()

    # Step 7: Verify restored store operates cleanly
    restored_store = SqliteStateStore(restored_path, workspace_home_id="workspace-step-f-test")
    # Note: store.initialize() on v16 db will run migration 17 and upgrade to v17 cleanly
    restored_store.initialize()
    with restored_store.unit_of_work() as unit:
        restored_delivery = unit.get_effect_delivery(event_id)
        assert restored_delivery is not None
        restored_digest = unit.get_artifact_recovery_digest(attempt_id)
        assert restored_digest is not None
        assert restored_digest.intent_event_id == event_id
    restored_store.close()
