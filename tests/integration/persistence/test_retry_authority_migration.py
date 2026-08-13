import sqlite3
from pathlib import Path
import pytest
from peerhub.persistence.sqlite import SqliteStateStore
from peerhub.dispatch.contract import AdmissionReceipt, LeaseSnapshot, LeaseState
from peerhub.dispatch.capability import CapabilityLease, CapabilityTier, EnforcementLevel
from peerhub.core.protocol import CommandID

def _store(path: Path) -> SqliteStateStore:
    return SqliteStateStore(path, workspace_home_id="test-workspace")

def test_v21_migrates_to_v22_cleanly(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db_path = tmp_path / "peerhub.sqlite3"

    # Init up to v21
    from importlib import resources
    real_migrations = Path(str(resources.files("peerhub.persistence.migrations")))
    migration_dir = tmp_path / "migrations"
    migration_dir.mkdir()

    import shutil
    for entry in sorted(real_migrations.glob("[0-9][0-9][0-9][0-9]_*.sql")):
        version = int(entry.name[:4])
        if version <= 21:
            shutil.copy2(entry, migration_dir / entry.name)

    monkeypatch.setattr(
        SqliteStateStore,
        "_migration_directory",
        staticmethod(lambda: migration_dir),
        raising=False,
    )

    store = _store(db_path)
    store.initialize()
    store.close()

    # Insert some capability_leases data at v21
    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA foreign_keys = ON")

        # We need related rows for foreign keys:
        # dispatch_requests, admission_receipts, leases
        conn.execute("INSERT INTO leases (lease_id, session_id, command_id, state, fencing_token, authority_epoch, revision, owner_instance_id, owner_principal_id, heartbeat_expires_at, created_at, updated_at) VALUES ('L1', 'S1', 'cmd-1', 'RESERVED', 1, 1, 1, 'inst', 'prin', 0, 0, 0)")
        conn.execute(
            """INSERT INTO dispatch_requests (command_id, client_id, client_request_id, correlation_id, authenticated_principal, command_type, idempotency_key, payload_digest, scope_json, params_json, expected_policy_revision_json, expected_configuration_revision_json, policy_revision_json, configuration_revision_json, completion_contract_json, required_capability_tier, selected_peer_instance_id, selected_profile_id, route_decision_digest, lease_id, state, revision, created_at, updated_at)
            VALUES ('cmd-1', 'client', 'req-1', 'corr', 'prin', 'type', 'idem', 'hash', '[]', '{}', '0', '0', '0', '0', '{"contract_id":"x","kind":"ALL_OF","requirements":[],"replay_safe":true}', 'READ_ONLY', 'inst', 'prof', 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', 'L1', 'ADMITTED', 1, 0, 0)"""
        )
        conn.execute(
            """INSERT INTO admission_receipts (admission_receipt_id, command_id, client_id, client_request_id, command_type, idempotency_key, payload_digest, completion_contract_id, lease_id, policy_revision_json, configuration_revision_json, admitted_at)
            VALUES ('ar-1', 'cmd-1', 'client', 'req-1', 'type', 'idem', 'hash', 'x', 'L1', '0', '0', 0)"""
        )

        conn.execute(
            """INSERT INTO capability_leases (
                capability_lease_id, command_id, admission_receipt_id, session_lease_id, subject_principal_id, selected_peer_kind, required_tier, authorized_tier, minimum_enforcement, selected_peer_instance_id, selected_profile_id, route_decision_digest, policy_revision_json, issuer_id, issued_at, expires_at
            ) VALUES (
                'cap-1', 'cmd-1', 'ar-1', 'L1', 'prin', 'kind', 'READ_ONLY', 'READ_ONLY', 'ENFORCED', 'inst', 'prof', 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', '0', 'issuer', 0, NULL
            )"""
        )
        conn.commit()

    # Now add v22 and initialize to migrate
    shutil.copy2(real_migrations / "0022_retry_authority.sql", migration_dir / "0022_retry_authority.sql")

    store = _store(db_path)
    store.initialize()
    store.close()

    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        violations = conn.execute("PRAGMA foreign_key_check").fetchall()
        assert not violations

        row = conn.execute("SELECT authorized_attempt_number, previous_attempt_id FROM capability_leases WHERE capability_lease_id = 'cap-1'").fetchone()
        assert row[0] == 1
        assert row[1] is None

def test_constraints_on_capability_leases(tmp_path: Path):
    db_path = tmp_path / "peerhub.sqlite3"
    store = _store(db_path)
    store.initialize()
    store.close()

    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA foreign_keys = ON")

        # Setup foreign keys
        conn.execute("INSERT INTO leases (lease_id, session_id, command_id, state, fencing_token, authority_epoch, revision, owner_instance_id, owner_principal_id, heartbeat_expires_at, created_at, updated_at) VALUES ('L1', 'S1', 'cmd-1', 'RESERVED', 1, 1, 1, 'inst', 'prin', 0, 0, 0)")
        conn.execute("INSERT INTO leases (lease_id, session_id, command_id, state, fencing_token, authority_epoch, revision, owner_instance_id, owner_principal_id, heartbeat_expires_at, created_at, updated_at) VALUES ('L2', 'S2', 'cmd-1', 'RESERVED', 1, 1, 1, 'inst', 'prin', 0, 0, 0)")
        conn.execute("INSERT INTO leases (lease_id, session_id, command_id, state, fencing_token, authority_epoch, revision, owner_instance_id, owner_principal_id, heartbeat_expires_at, created_at, updated_at) VALUES ('L3', 'S3', 'cmd-1', 'RESERVED', 1, 1, 1, 'inst', 'prin', 0, 0, 0)")

        conn.execute(
            """INSERT INTO dispatch_requests (command_id, client_id, client_request_id, correlation_id, authenticated_principal, command_type, idempotency_key, payload_digest, scope_json, params_json, expected_policy_revision_json, expected_configuration_revision_json, policy_revision_json, configuration_revision_json, completion_contract_json, required_capability_tier, selected_peer_instance_id, selected_profile_id, route_decision_digest, lease_id, state, revision, created_at, updated_at)
            VALUES ('cmd-1', 'client', 'req-1', 'corr', 'prin', 'type', 'idem', 'hash', '[]', '{}', '0', '0', '0', '0', '{"contract_id":"x","kind":"ALL_OF","requirements":[],"replay_safe":true}', 'READ_ONLY', 'inst', 'prof', 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', 'L1', 'ADMITTED', 1, 0, 0)"""
        )
        conn.execute(
            """INSERT INTO admission_receipts (admission_receipt_id, command_id, client_id, client_request_id, command_type, idempotency_key, payload_digest, completion_contract_id, lease_id, policy_revision_json, configuration_revision_json, admitted_at)
            VALUES ('ar-1', 'cmd-1', 'client', 'req-1', 'type', 'idem', 'hash', 'x', 'L1', '0', '0', 0)"""
        )
        conn.execute(
            """INSERT INTO dispatch_attempts (attempt_id, command_id, attempt_number, lease_id, state, execution_certainty, revision, reconciliation_complete, result_json, terminal_error_code, created_at, updated_at)
            VALUES ('att-1', 'cmd-1', 1, 'L1', 'SUCCEEDED_VERIFIED', 'TERMINAL', 1, 1, NULL, NULL, 0, 0)"""
        )

        # 3. Two capability rows CAN now share command_id as long as authorized_attempt_number differs
        conn.execute(
            """INSERT INTO capability_leases (
                capability_lease_id, command_id, admission_receipt_id, session_lease_id, subject_principal_id, selected_peer_kind, required_tier, authorized_tier, minimum_enforcement, selected_peer_instance_id, selected_profile_id, route_decision_digest, policy_revision_json, issuer_id, issued_at, expires_at, authorized_attempt_number, previous_attempt_id
            ) VALUES (
                'cap-1', 'cmd-1', 'ar-1', 'L1', 'prin', 'kind', 'READ_ONLY', 'READ_ONLY', 'ENFORCED', 'inst', 'prof', 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', '0', 'issuer', 0, NULL, 1, NULL
            )"""
        )
        conn.execute(
            """INSERT INTO capability_leases (
                capability_lease_id, command_id, admission_receipt_id, session_lease_id, subject_principal_id, selected_peer_kind, required_tier, authorized_tier, minimum_enforcement, selected_peer_instance_id, selected_profile_id, route_decision_digest, policy_revision_json, issuer_id, issued_at, expires_at, authorized_attempt_number, previous_attempt_id
            ) VALUES (
                'cap-2', 'cmd-1', 'ar-1', 'L2', 'prin', 'kind', 'READ_ONLY', 'READ_ONLY', 'ENFORCED', 'inst', 'prof', 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', '0', 'issuer', 0, NULL, 2, 'att-1'
            )"""
        )

        # 4. Duplicate (command_id, authorized_attempt_number) fails
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """INSERT INTO capability_leases (
                    capability_lease_id, command_id, admission_receipt_id, session_lease_id, subject_principal_id, selected_peer_kind, required_tier, authorized_tier, minimum_enforcement, selected_peer_instance_id, selected_profile_id, route_decision_digest, policy_revision_json, issuer_id, issued_at, expires_at, authorized_attempt_number, previous_attempt_id
                ) VALUES (
                    'cap-3', 'cmd-1', 'ar-1', 'L3', 'prin', 'kind', 'READ_ONLY', 'READ_ONLY', 'ENFORCED', 'inst', 'prof', 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', '0', 'issuer', 0, NULL, 1, NULL
                )"""
            )

        # 5. Duplicate session_lease_id still fails
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """INSERT INTO capability_leases (
                    capability_lease_id, command_id, admission_receipt_id, session_lease_id, subject_principal_id, selected_peer_kind, required_tier, authorized_tier, minimum_enforcement, selected_peer_instance_id, selected_profile_id, route_decision_digest, policy_revision_json, issuer_id, issued_at, expires_at, authorized_attempt_number, previous_attempt_id
                ) VALUES (
                    'cap-4', 'cmd-1', 'ar-1', 'L1', 'prin', 'kind', 'READ_ONLY', 'READ_ONLY', 'ENFORCED', 'inst', 'prof', 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', '0', 'issuer', 0, NULL, 3, 'att-1'
                )"""
            )

        # 6. Shape CHECK rejects (1, non-null)
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """INSERT INTO capability_leases (
                    capability_lease_id, command_id, admission_receipt_id, session_lease_id, subject_principal_id, selected_peer_kind, required_tier, authorized_tier, minimum_enforcement, selected_peer_instance_id, selected_profile_id, route_decision_digest, policy_revision_json, issuer_id, issued_at, expires_at, authorized_attempt_number, previous_attempt_id
                ) VALUES (
                    'cap-5', 'cmd-1', 'ar-1', 'L3', 'prin', 'kind', 'READ_ONLY', 'READ_ONLY', 'ENFORCED', 'inst', 'prof', 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', '0', 'issuer', 0, NULL, 1, 'att-1'
                )"""
            )

        # 6. Shape CHECK rejects (>1, NULL)
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """INSERT INTO capability_leases (
                    capability_lease_id, command_id, admission_receipt_id, session_lease_id, subject_principal_id, selected_peer_kind, required_tier, authorized_tier, minimum_enforcement, selected_peer_instance_id, selected_profile_id, route_decision_digest, policy_revision_json, issuer_id, issued_at, expires_at, authorized_attempt_number, previous_attempt_id
                ) VALUES (
                    'cap-6', 'cmd-1', 'ar-1', 'L3', 'prin', 'kind', 'READ_ONLY', 'READ_ONLY', 'ENFORCED', 'inst', 'prof', 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', '0', 'issuer', 0, NULL, 2, NULL
                )"""
            )

def test_persistence_ports_round_trip(tmp_path: Path):
    db_path = tmp_path / "peerhub.sqlite3"
    store = _store(db_path)
    store.initialize()

    # 7. add_retry_policy / get_retry_policy_max_attempts round-trip
    with store.unit_of_work() as unit:
        # FK for dispatch_requests
        unit._db().execute("INSERT INTO leases (lease_id, session_id, command_id, state, fencing_token, authority_epoch, revision, owner_instance_id, owner_principal_id, heartbeat_expires_at, created_at, updated_at) VALUES ('L1', 'S1', 'cmd-1', 'RESERVED', 1, 1, 1, 'inst', 'prin', 0, 0, 0)")
        unit._db().execute(
            """INSERT INTO dispatch_requests (command_id, client_id, client_request_id, correlation_id, authenticated_principal, command_type, idempotency_key, payload_digest, scope_json, params_json, expected_policy_revision_json, expected_configuration_revision_json, policy_revision_json, configuration_revision_json, completion_contract_json, required_capability_tier, selected_peer_instance_id, selected_profile_id, route_decision_digest, lease_id, state, revision, created_at, updated_at)
            VALUES ('cmd-1', 'client', 'req-1', 'corr', 'prin', 'type', 'idem', 'hash', '[]', '{}', '0', '0', '0', '0', '{"contract_id":"x","kind":"ALL_OF","requirements":[],"replay_safe":true}', 'READ_ONLY', 'inst', 'prof', 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', 'L1', 'ADMITTED', 1, 0, 0)"""
        )

        # Unkown command
        assert unit.get_retry_policy_max_attempts(CommandID("cmd-2")) is None

        unit.add_retry_policy(CommandID("cmd-1"), 3)
        unit.commit()

    with store.read_unit_of_work() as read_unit:
        assert read_unit.get_retry_policy_max_attempts(CommandID("cmd-1")) == 3

    # 8. 3 new capability lookups
    with store.unit_of_work() as unit:
        unit._db().execute("INSERT INTO leases (lease_id, session_id, command_id, state, fencing_token, authority_epoch, revision, owner_instance_id, owner_principal_id, heartbeat_expires_at, created_at, updated_at) VALUES ('L2', 'S2', 'cmd-1', 'RESERVED', 1, 1, 1, 'inst', 'prin', 0, 0, 0)")
        unit._db().execute("INSERT INTO leases (lease_id, session_id, command_id, state, fencing_token, authority_epoch, revision, owner_instance_id, owner_principal_id, heartbeat_expires_at, created_at, updated_at) VALUES ('L3', 'S3', 'cmd-1', 'RESERVED', 1, 1, 1, 'inst', 'prin', 0, 0, 0)")
        unit._db().execute(
            """INSERT INTO admission_receipts (admission_receipt_id, command_id, client_id, client_request_id, command_type, idempotency_key, payload_digest, completion_contract_id, lease_id, policy_revision_json, configuration_revision_json, admitted_at)
            VALUES ('ar-1', 'cmd-1', 'client', 'req-1', 'type', 'idem', 'hash', 'x', 'L1', '0', '0', 0)"""
        )
        unit._db().execute(
            """INSERT INTO dispatch_attempts (attempt_id, command_id, attempt_number, lease_id, state, execution_certainty, revision, reconciliation_complete, result_json, terminal_error_code, created_at, updated_at)
            VALUES ('att-1', 'cmd-1', 1, 'L1', 'SUCCEEDED_VERIFIED', 'TERMINAL', 1, 1, NULL, NULL, 0, 0)"""
        )

        unit._db().execute(
            """INSERT INTO capability_leases (
                capability_lease_id, command_id, admission_receipt_id, session_lease_id, subject_principal_id, selected_peer_kind, required_tier, authorized_tier, minimum_enforcement, selected_peer_instance_id, selected_profile_id, route_decision_digest, policy_revision_json, issuer_id, issued_at, expires_at, authorized_attempt_number, previous_attempt_id
            ) VALUES (
                'cap-1', 'cmd-1', 'ar-1', 'L2', 'prin', 'kind', 'READ_ONLY', 'READ_ONLY', 'ENFORCED', 'inst', 'prof', 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', '0', 'issuer', 0, NULL, 1, NULL
            )"""
        )
        unit._db().execute(
            """INSERT INTO capability_leases (
                capability_lease_id, command_id, admission_receipt_id, session_lease_id, subject_principal_id, selected_peer_kind, required_tier, authorized_tier, minimum_enforcement, selected_peer_instance_id, selected_profile_id, route_decision_digest, policy_revision_json, issuer_id, issued_at, expires_at, authorized_attempt_number, previous_attempt_id
            ) VALUES (
                'cap-2', 'cmd-1', 'ar-1', 'L3', 'prin', 'kind', 'READ_ONLY', 'READ_ONLY', 'ENFORCED', 'inst', 'prof', 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', '0', 'issuer', 0, NULL, 2, 'att-1'
            )"""
        )
        unit.commit()

    with store.read_unit_of_work() as read_unit:
        cap_1 = read_unit.get_capability_lease('cap-1')
        assert cap_1 is not None
        assert cap_1.command_id == 'cmd-1'

        cap_L3 = read_unit.get_capability_lease_by_session_lease_id('L3')
        assert cap_L3 is not None
        assert cap_L3.capability_lease_id == 'cap-2'

        cap_attempt2 = read_unit.get_capability_lease_for_attempt('cmd-1', 2)
        assert cap_attempt2 is not None
        assert cap_attempt2.capability_lease_id == 'cap-2'

        assert read_unit.get_capability_lease_for_attempt('cmd-1', 3) is None

    store.close()
