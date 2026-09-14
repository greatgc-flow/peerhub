"""D-CTX workspace trust anchor primitives (Q2/D2, DIR-006-unanimous per
docs/design/peerhub-dctx-trust-anchor-closure-2026-09-14.md)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from peerhub.persistence.dispatch_context import (
    issue_credential,
    revoke_credentials_below_epoch,
    verify_credential,
)
from peerhub.persistence.sqlite import SqliteStateStore


def _store(path: Path, workspace_home_id: str = "test-workspace") -> SqliteStateStore:
    return SqliteStateStore(path, workspace_home_id=workspace_home_id)


def _seed_dispatch_request(conn: sqlite3.Connection, command_id: str) -> None:
    conn.execute(
        """INSERT INTO dispatch_requests (command_id, client_id, client_request_id, correlation_id, authenticated_principal, command_type, idempotency_key, payload_digest, scope_json, params_json, expected_policy_revision_json, expected_configuration_revision_json, policy_revision_json, configuration_revision_json, completion_contract_json, required_capability_tier, selected_peer_instance_id, selected_profile_id, route_decision_digest, lease_id, state, revision, created_at, updated_at)
        VALUES (?, 'client', ?, 'corr', 'prin', 'type', ?, 'hash', '[]', '{}', '0', '0', '0', '0', '{"contract_id":"x","kind":"ALL_OF","requirements":[],"replay_safe":true}', 'READ_ONLY', 'inst', 'prof', 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', ?, 'ADMITTED', 1, 0, 0)""",
        (command_id, f"req-{command_id}", f"idem-{command_id}", f"L-{command_id}"),
    )


def _identity(conn: sqlite3.Connection) -> tuple[str, int]:
    row = conn.execute(
        "SELECT workspace_home_id, activation_epoch FROM workspace_identity WHERE singleton = 1"
    ).fetchone()
    return (row[0], row[1])


def test_issue_and_verify_credential_round_trip(tmp_path: Path) -> None:
    db_path = tmp_path / "peerhub.sqlite3"
    store = _store(db_path)
    store.initialize()
    store.close()

    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        workspace_home_id, epoch = _identity(conn)
        _seed_dispatch_request(conn, "cmd-1")
        issue_credential(
            conn,
            credential_id="cred-1",
            command_id="cmd-1",
            workspace_home_id=workspace_home_id,
            activation_epoch=epoch,
            issued_at=1000,
            expires_at=2000,
        )
        conn.commit()

        assert verify_credential(
            conn,
            credential_id="cred-1",
            command_id="cmd-1",
            workspace_home_id=workspace_home_id,
            activation_epoch=epoch,
            now=1500,
        )


def test_verify_credential_rejects_wrong_command_id(tmp_path: Path) -> None:
    db_path = tmp_path / "peerhub.sqlite3"
    store = _store(db_path)
    store.initialize()
    store.close()

    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        workspace_home_id, epoch = _identity(conn)
        _seed_dispatch_request(conn, "cmd-1")
        _seed_dispatch_request(conn, "cmd-2")
        issue_credential(
            conn,
            credential_id="cred-1",
            command_id="cmd-1",
            workspace_home_id=workspace_home_id,
            activation_epoch=epoch,
            issued_at=1000,
            expires_at=2000,
        )
        conn.commit()

        # A credential minted for cmd-1 must not authorize cmd-2, even
        # though every other field matches -- a copy-pasted credential
        # presented against the wrong command is exactly the accidental-
        # confusion case this design exists to catch.
        assert not verify_credential(
            conn,
            credential_id="cred-1",
            command_id="cmd-2",
            workspace_home_id=workspace_home_id,
            activation_epoch=epoch,
            now=1500,
        )


def test_verify_credential_rejects_wrong_workspace(tmp_path: Path) -> None:
    db_path = tmp_path / "peerhub.sqlite3"
    store = _store(db_path)
    store.initialize()
    store.close()

    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        workspace_home_id, epoch = _identity(conn)
        _seed_dispatch_request(conn, "cmd-1")
        issue_credential(
            conn,
            credential_id="cred-1",
            command_id="cmd-1",
            workspace_home_id=workspace_home_id,
            activation_epoch=epoch,
            issued_at=1000,
            expires_at=2000,
        )
        conn.commit()

        assert not verify_credential(
            conn,
            credential_id="cred-1",
            command_id="cmd-1",
            workspace_home_id="a-different-workspace-id",
            activation_epoch=epoch,
            now=1500,
        )


def test_verify_credential_rejects_expired(tmp_path: Path) -> None:
    db_path = tmp_path / "peerhub.sqlite3"
    store = _store(db_path)
    store.initialize()
    store.close()

    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        workspace_home_id, epoch = _identity(conn)
        _seed_dispatch_request(conn, "cmd-1")
        issue_credential(
            conn,
            credential_id="cred-1",
            command_id="cmd-1",
            workspace_home_id=workspace_home_id,
            activation_epoch=epoch,
            issued_at=1000,
            expires_at=2000,
        )
        conn.commit()

        assert not verify_credential(
            conn,
            credential_id="cred-1",
            command_id="cmd-1",
            workspace_home_id=workspace_home_id,
            activation_epoch=epoch,
            now=2000,
        )


def test_verify_credential_rejects_superseded_epoch(tmp_path: Path) -> None:
    """A credential minted under an earlier epoch must not verify against
    the current (restore-bumped) epoch -- D12's no-authority-resurrection
    guarantee, extended to D-CTX credentials."""

    db_path = tmp_path / "peerhub.sqlite3"
    store = _store(db_path)
    store.initialize()

    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        workspace_home_id, old_epoch = _identity(conn)
        _seed_dispatch_request(conn, "cmd-1")
        issue_credential(
            conn,
            credential_id="cred-1",
            command_id="cmd-1",
            workspace_home_id=workspace_home_id,
            activation_epoch=old_epoch,
            issued_at=1000,
            expires_at=2000,
        )
        conn.commit()

    # Simulate a restore: the epoch advances.
    store.mint_new_epoch()
    store.close()

    with sqlite3.connect(db_path) as conn:
        _, new_epoch = _identity(conn)
        assert new_epoch != old_epoch

        # Presenting the credential's original epoch against a DB now at a
        # higher epoch must fail -- the credential's own recorded epoch no
        # longer matches "the current epoch" a real verifier would check.
        assert not verify_credential(
            conn,
            credential_id="cred-1",
            command_id="cmd-1",
            workspace_home_id=workspace_home_id,
            activation_epoch=new_epoch,
            now=1500,
        )


def test_revoke_credentials_below_epoch_revokes_prior_epoch_only(tmp_path: Path) -> None:
    db_path = tmp_path / "peerhub.sqlite3"
    store = _store(db_path)
    store.initialize()

    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        workspace_home_id, epoch_1 = _identity(conn)
        _seed_dispatch_request(conn, "cmd-1")
        issue_credential(
            conn,
            credential_id="cred-old",
            command_id="cmd-1",
            workspace_home_id=workspace_home_id,
            activation_epoch=epoch_1,
            issued_at=1000,
            expires_at=999999,
        )
        conn.commit()

    store.mint_new_epoch()
    store.close()

    with sqlite3.connect(db_path) as conn:
        _, epoch_2 = _identity(conn)
        _seed_dispatch_request(conn, "cmd-2")
        issue_credential(
            conn,
            credential_id="cred-new",
            command_id="cmd-2",
            workspace_home_id=workspace_home_id,
            activation_epoch=epoch_2,
            issued_at=1000,
            expires_at=999999,
        )
        revoke_credentials_below_epoch(
            conn,
            workspace_home_id=workspace_home_id,
            minimum_epoch=epoch_2,
            revoked_at=1234,
        )
        conn.commit()

        assert not verify_credential(
            conn,
            credential_id="cred-old",
            command_id="cmd-1",
            workspace_home_id=workspace_home_id,
            activation_epoch=epoch_1,
            now=1500,
        )
        assert verify_credential(
            conn,
            credential_id="cred-new",
            command_id="cmd-2",
            workspace_home_id=workspace_home_id,
            activation_epoch=epoch_2,
            now=1500,
        )
