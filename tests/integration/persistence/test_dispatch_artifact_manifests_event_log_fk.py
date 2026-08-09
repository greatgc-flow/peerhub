"""Integration coverage for the dispatch-artifact-manifests event_log foreign key (Migration 0016)."""

from __future__ import annotations

import sqlite3
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest

from peerhub.core.protocol import (
    PROTOCOL_MAJOR,
    PROTOCOL_MINOR,
    SCHEMA_VERSION,
    CommandEnvelope,
)
from peerhub.dispatch.contract import (
    ArtifactManifestRecord,
    ArtifactMetadata,
    ArtifactState,
    CompletionContract,
    CompletionContractKind,
)
from peerhub.dispatch.service import DispatchService
from peerhub.persistence.sqlite import SqliteStateStore
from tests.fakes import DeterministicClock, SequentialIdSource


@pytest.fixture
def store(tmp_path: Path) -> Iterator[SqliteStateStore]:
    state_store = SqliteStateStore(
        tmp_path / "dispatch_artifact_manifests_event_log_fk.sqlite3",
        workspace_home_id="workspace-manifest-fk-test",
    )
    state_store.initialize()
    try:
        yield state_store
    finally:
        state_store.close()


def _seed_attempt(store: SqliteStateStore) -> str:
    service = DispatchService(
        store,
        clock=DeterministicClock(start=100),
        ids=SequentialIdSource(),
    )
    unique_suffix = str(uuid.uuid4())[:8]
    envelope = CommandEnvelope(
        protocol_major=PROTOCOL_MAJOR,
        protocol_minor=PROTOCOL_MINOR,
        schema_version=SCHEMA_VERSION,
        client_request_id=f"client-req-{unique_suffix}",
        correlation_id="corr-01",
        client_id="client-01",
        actor_id="actor-01",
        scope={"workspace_id": "workspace-01", "home_id": "home-01"},
        method="peer.ask",
        params={"prompt": "hello"},
        idempotency_key=f"idempotency-{unique_suffix}",
        expected_policy_revision=7,
        expected_configuration_revision=11,
        client_timestamp=10,
    )
    contract = CompletionContract(
        contract_id=f"contract-{unique_suffix}",
        kind=CompletionContractKind.DELIVERY_ONLY,
        requirements=(),
        replay_safe=False,
    )
    admitted, receipt, reserved = service.admit_request(
        envelope,
        authenticated_principal="principal-01",
        actor_authorized=True,
        completion_contract=contract,
        policy_revision=7,
        configuration_revision=11,
        selected_peer_instance_id="instance-01",
        selected_profile_id="profile-01",
        route_decision_digest="b" * 64,
        session_id=f"session-{unique_suffix}",
        owner_principal_id="principal-01",
        owner_instance_id="instance-01",
        authority_epoch=5,
        heartbeat_timeout_ms=5_000,
        owner_peer_id="peer-01",
    )
    service.prepare_request(admitted.command_id)
    attempt = service.create_attempt(admitted.command_id)
    return attempt.attempt_id


def _seed_event_log(unit, event_id: str, manifest_digest: str = "sha256:manifest-digest-1") -> None:
    unit._db().execute(  # pyright: ignore[reportPrivateUsage]
        """
        INSERT OR IGNORE INTO event_log (
            event_id, protocol_major, protocol_minor, schema_version,
            correlation_id, occurred_at, event_kind, payload_json,
            request_id, round_id, evidence_refs_json, predecessor_digest,
            recovery_context_json, appended_at
        ) VALUES (
            ?, 1, 0, '1.0',
            'corr-1', 10, 'DISPATCH_INTENT',
            ?,
            'req-1', 'round-1', '[]', NULL, NULL, 10
        )
        """,
        (event_id, f'{{"manifest_digest": "{manifest_digest}"}}'),
    )


def _make_manifest(
    attempt_id: str,
    intent_event_id: str | None = None,
    manifest_digest: str = "sha256:manifest-digest-1",
) -> ArtifactManifestRecord:
    return ArtifactManifestRecord(
        attempt_id=attempt_id,
        workspace_scope_id="workspace-scope-1",
        staging_root_ref="staging://root-1",
        manifest_digest=manifest_digest,
        item_count=1,
        created_at=100,
        revision=1,
        intent_event_id=intent_event_id,
        consumed_at=None,
    )


def _make_artifact(attempt_id: str, artifact_id: str = "art-01") -> ArtifactMetadata:
    return ArtifactMetadata(
        attempt_id=attempt_id,
        artifact_id=artifact_id,
        placeholder=f"__{artifact_id.upper()}__",
        workspace_scope_id="workspace-scope-1",
        staging_ref=f"staging://{artifact_id}",
        access_mode="READ_WRITE",
        declared_lifecycle="DISPATCH_BOUND",
        state=ArtifactState.VERIFIED,
        declared_at=100,
        revision=1,
    )


def test_migration_0016_applied_and_fk_references_event_log(
    store: SqliteStateStore,
) -> None:
    with store.unit_of_work() as unit:
        migration = unit._db().execute(  # pyright: ignore[reportPrivateUsage]
            "SELECT name FROM schema_migrations WHERE version = 16"
        ).fetchone()
        foreign_keys = unit._db().execute(  # pyright: ignore[reportPrivateUsage]
            "PRAGMA foreign_key_list(dispatch_artifact_manifests)"
        ).fetchall()
        violations = unit._db().execute(  # pyright: ignore[reportPrivateUsage]
            "PRAGMA foreign_key_check(dispatch_artifact_manifests)"
        ).fetchall()

    assert migration is not None
    assert migration["name"] == "0016_dispatch_artifact_manifests_event_log_fk"
    assert any(
        row["table"] == "event_log"
        and row["from"] == "intent_event_id"
        and row["to"] == "event_id"
        for row in foreign_keys
    )
    assert violations == []


def test_manifest_rejects_intent_event_id_not_in_event_log(
    store: SqliteStateStore,
) -> None:
    attempt_id = _seed_attempt(store)
    manifest = _make_manifest(attempt_id, intent_event_id="dangling-event-id")
    art = _make_artifact(attempt_id)

    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY constraint failed"):
        with store.unit_of_work() as unit:
            unit.add_artifact_manifest(manifest, (art,))


def test_manifest_accepts_intent_event_id_in_event_log(
    store: SqliteStateStore,
) -> None:
    attempt_id = _seed_attempt(store)
    event_id = "event-valid-intent"
    art = _make_artifact(attempt_id)

    with store.unit_of_work() as unit:
        _seed_event_log(unit, event_id)
        manifest = _make_manifest(attempt_id, intent_event_id=event_id)
        unit.add_artifact_manifest(manifest, (art,))
        unit.commit()

    with store.unit_of_work() as unit:
        retrieved = unit.get_artifact_manifest(attempt_id)
        violations = unit._db().execute(  # pyright: ignore[reportPrivateUsage]
            "PRAGMA foreign_key_check(dispatch_artifact_manifests)"
        ).fetchall()

    assert retrieved is not None
    assert retrieved.intent_event_id == event_id
    assert violations == []


def test_recovery_digest_reads_intent_event_from_event_log(
    store: SqliteStateStore,
) -> None:
    attempt_id = _seed_attempt(store)
    event_id = "event-digest-test"
    manifest_digest = "sha256:digest-abc"
    art = _make_artifact(attempt_id)

    with store.unit_of_work() as unit:
        _seed_event_log(unit, event_id, manifest_digest=manifest_digest)
        manifest = _make_manifest(attempt_id, intent_event_id=event_id, manifest_digest=manifest_digest)
        unit.add_artifact_manifest(manifest, (art,))
        unit.commit()

    with store.unit_of_work() as unit:
        digest = unit.get_artifact_recovery_digest(attempt_id)

    assert digest is not None
    assert digest.intent_event_id == event_id
    assert digest.intent_event_verified is True


def test_migration_0016_fails_closed_on_orphaned_intent_event_id(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "orphan_test.sqlite3"
    state_store = SqliteStateStore(db_path, workspace_home_id="workspace-orphan-test")
    state_store.initialize()
    attempt_id = _seed_attempt(state_store)
    state_store.close()

    # Open raw SQLite connection, revert to v15 schema state and insert orphan intent_event_id
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute("DELETE FROM schema_migrations WHERE version = 16")
    conn.execute("PRAGMA user_version = 15")
    conn.execute(
        """
        INSERT INTO dispatch_artifact_manifests (
            attempt_id, workspace_scope_id, staging_root_ref,
            manifest_digest, item_count, intent_event_id,
            created_at, consumed_at, revision
        ) VALUES (
            ?, 'scope-1', 'staging://root',
            'digest-1', 1, 'orphan-event-id-not-in-event-log',
            100, NULL, 1
        )
        ON CONFLICT(attempt_id) DO UPDATE SET intent_event_id = 'orphan-event-id-not-in-event-log'
        """,
        (attempt_id,),
    )
    conn.commit()

    migrations_dir = Path(__file__).parent.parent.parent.parent / "peerhub" / "persistence" / "migrations"
    migration_16_sql = (migrations_dir / "0016_dispatch_artifact_manifests_event_log_fk.sql").read_text(encoding="utf-8")

    # Applying migration 16 must fail closed because orphan-event-id is not in event_log
    with pytest.raises(sqlite3.IntegrityError, match=r"(CHECK constraint failed|FOREIGN KEY constraint failed)"):
        conn.executescript(migration_16_sql)

    # Verify rollback: schema version remains 15
    row = conn.execute("SELECT version FROM schema_migrations WHERE version = 16").fetchone()
    assert row is None
    user_version = conn.execute("PRAGMA user_version").fetchone()[0]
    assert user_version == 15
    conn.close()
