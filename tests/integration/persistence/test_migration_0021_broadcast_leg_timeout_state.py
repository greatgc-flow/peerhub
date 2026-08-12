"""Verification test for Migration 0021: broadcast_legs table recreation."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

def test_migration_0021_broadcast_leg_timeout_state(tmp_path: Path) -> None:
    db_path = tmp_path / "migration_0021_test.sqlite3"
    migrations_dir = Path(__file__).parent.parent.parent.parent / "peerhub" / "persistence" / "migrations"

    # Step 1: Initialize database up to version 20
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    for v in range(1, 21):
        sql_file = next(migrations_dir.glob(f"{v:04d}_*.sql"))
        conn.executescript(sql_file.read_text(encoding="utf-8"))

    user_version_v20 = conn.execute("PRAGMA user_version").fetchone()[0]
    assert user_version_v20 == 20

    # Step 2: Insert prerequisite records
    for i in range(1, 6):
        lease_id_i = f"lease-21-test-{i}"
        cmd_id_i = f"cmd-{i}"

        conn.execute(
            """
            INSERT INTO leases (
                lease_id, session_id, command_id, fencing_token, authority_epoch,
                revision, owner_principal_id, owner_instance_id, owner_peer_id,
                state, heartbeat_expires_at, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                lease_id_i, "session-1", cmd_id_i, 1, 1,
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
                cmd_id_i, f"client-{i}", f"client-req-{i}", f"corr-{i}", "princ-1",
                "peer.ask", f"idemp-disp-{i}", "digest-p", json.dumps({}), json.dumps({}),
                json.dumps(1), json.dumps(1), json.dumps(1), json.dumps(1),
                json.dumps({"kind": "DELIVERY_ONLY"}), "inst-1", "prof-1", "d" * 64,
                lease_id_i, "ADMITTED", 1, 100, 100,
            ),
        )

    round_id = "round-1"
    conn.execute(
        """
        INSERT INTO broadcast_rounds (
            broadcast_round_id, prompt_digest, requested_targets, deadline_at,
            status, created_at
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (round_id, "digest-1", 5, 200, "open", 100),
    )

    legs_to_insert = [
        ("target-1", "client-1", "req-1", None, "admitting", None),
        ("target-2", "client-2", "req-2", "cmd-2", "pending", None),
        ("target-3", "client-3", "req-3", "cmd-3", "completed", 150),
        ("target-4", "client-4", "req-4", "cmd-4", "failed", 150),
        ("target-5", "client-5", "req-5", "cmd-5", "timed_out", 150),
    ]

    for leg in legs_to_insert:
        conn.execute(
            """
            INSERT INTO broadcast_legs (
                broadcast_round_id, leg_target, client_id, client_leg_request_id,
                command_id, leg_state, terminal_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (round_id, leg[0], leg[1], leg[2], leg[3], leg[4], leg[5])
        )
    conn.commit()

    # Step 3: Apply migration 0021
    migration_21_sql = (migrations_dir / "0021_broadcast_leg_timeout_state.sql").read_text(encoding="utf-8")
    conn.executescript(migration_21_sql)

    # Step 4: Verify migration 21 applied and table state
    user_version_v21 = conn.execute("PRAGMA user_version").fetchone()[0]
    assert user_version_v21 == 21

    fk_violations = conn.execute("PRAGMA foreign_key_check").fetchall()
    assert fk_violations == []

    surviving_legs = conn.execute(
        """
        SELECT leg_target, client_id, client_leg_request_id, command_id, leg_state, terminal_at
        FROM broadcast_legs
        ORDER BY leg_target
        """
    ).fetchall()

    assert len(surviving_legs) == len(legs_to_insert)
    for i, expected_leg in enumerate(legs_to_insert):
        assert surviving_legs[i] == expected_leg

    # Step 5: Assert new constraint accepts ('timed_out', NULL command_id)
    try:
        conn.execute(
            """
            INSERT INTO broadcast_legs (
                broadcast_round_id, leg_target, client_id, client_leg_request_id,
                command_id, leg_state, terminal_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (round_id, "target-6", "client-6", "req-6", None, "timed_out", 160)
        )
        conn.commit()
    except sqlite3.IntegrityError as e:
        pytest.fail(f"New constraint incorrectly rejected ('timed_out', NULL command_id): {e}")

    # Verify that 'pending' with NULL command_id is still rejected
    with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint failed"):
        conn.execute(
            """
            INSERT INTO broadcast_legs (
                broadcast_round_id, leg_target, client_id, client_leg_request_id,
                command_id, leg_state, terminal_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (round_id, "target-7", "client-7", "req-7", None, "pending", None)
        )
    
    conn.close()
