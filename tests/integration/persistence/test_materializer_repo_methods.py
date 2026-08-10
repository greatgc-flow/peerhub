"""Integration tests for the three new narrow typed artifact repository methods.

Covers ``mark_artifact_staged``, ``mark_artifact_verified``, and
``reclaim_orphaned_artifact`` added per the ratified ArtifactMaterializer
contract in ``docs/design/SLICE5-KICKOFF-R1.md``.
"""

from __future__ import annotations

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
from peerhub.dispatch.capability import CapabilityTier
from peerhub.dispatch.service import DispatchService
from peerhub.persistence.sqlite import SqliteStateStore
from tests.fakes import DeterministicClock, SequentialIdSource


@pytest.fixture
def store(tmp_path: Path) -> Iterator[SqliteStateStore]:
    state_store = SqliteStateStore(
        tmp_path / "materializer-repo-test.sqlite3",
        workspace_home_id="workspace-materializer-test",
    )
    state_store.initialize()
    try:
        yield state_store
    finally:
        state_store.close()


def _seed_attempt(store: SqliteStateStore) -> str:
    """Create a dispatch attempt and return its attempt_id."""
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
        contract_id="contract-01",
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
        required_capability_tier=CapabilityTier.READ_ONLY,
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


def _make_manifest(
    attempt_id: str = "attempt-01",
    workspace_scope_id: str = "ws-01",
    staging_root_ref: str = ".artifacts/staging",
    manifest_digest: str = "digest-01",
    item_count: int = 1,
    created_at: int = 100,
    revision: int = 1,
    intent_event_id: str | None = None,
    consumed_at: int | None = None,
) -> ArtifactManifestRecord:
    return ArtifactManifestRecord(
        attempt_id=attempt_id,
        workspace_scope_id=workspace_scope_id,
        staging_root_ref=staging_root_ref,
        manifest_digest=manifest_digest,
        item_count=item_count,
        created_at=created_at,
        revision=revision,
        intent_event_id=intent_event_id,
        consumed_at=consumed_at,
    )


def _make_artifact(
    attempt_id: str = "attempt-01",
    artifact_id: str = "art-01",
    placeholder: str = "__ART_01__",
    workspace_scope_id: str = "ws-01",
    staging_ref: str = "rel/staging/art-01.dat",
    access_mode: str = "READ_WRITE",
    declared_lifecycle: str = "EPHEMERAL",
    state: ArtifactState = ArtifactState.DECLARED,
    declared_at: int = 100,
    revision: int = 1,
    expected_sha256_hex: str | None = "abc123",
    expected_length: int | None = 1024,
    verified_sha256_hex: str | None = None,
    verified_length: int | None = None,
    verified_object_identity_json: str | None = None,
    failure_code: str | None = None,
    staged_at: int | None = None,
    verified_at: int | None = None,
    reserved_at: int | None = None,
    consumed_at: int | None = None,
    cleaned_at: int | None = None,
    orphaned_at: int | None = None,
) -> ArtifactMetadata:
    return ArtifactMetadata(
        attempt_id=attempt_id,
        artifact_id=artifact_id,
        placeholder=placeholder,
        workspace_scope_id=workspace_scope_id,
        staging_ref=staging_ref,
        access_mode=access_mode,
        declared_lifecycle=declared_lifecycle,
        state=state,
        declared_at=declared_at,
        revision=revision,
        expected_sha256_hex=expected_sha256_hex,
        expected_length=expected_length,
        verified_sha256_hex=verified_sha256_hex,
        verified_length=verified_length,
        verified_object_identity_json=verified_object_identity_json,
        failure_code=failure_code,
        staged_at=staged_at,
        verified_at=verified_at,
        reserved_at=reserved_at,
        consumed_at=consumed_at,
        cleaned_at=cleaned_at,
        orphaned_at=orphaned_at,
    )


class TestMarkArtifactStaged:
    """mark_artifact_staged: DECLARED → STAGED with CAS guard."""

    def test_declared_to_staged_succeeds(self, store: SqliteStateStore) -> None:
        attempt_id = _seed_attempt(store)
        manifest = _make_manifest(attempt_id=attempt_id)
        art = _make_artifact(
            attempt_id=attempt_id,
            artifact_id="art-01",
            state=ArtifactState.DECLARED,
        )

        with store.unit_of_work() as unit:
            unit.add_artifact_manifest(manifest, (art,))
            unit.commit()

        with store.unit_of_work() as unit:
            ok = unit.mark_artifact_staged(
                attempt_id=attempt_id,
                artifact_id="art-01",
                staging_path_relative="staging/art-01.tmp.uuid",
                expected_revision=1,
                staged_at=150,
            )
            assert ok, "mark_artifact_staged should succeed for DECLARED artifact"
            unit.commit()

        with store.unit_of_work() as unit:
            meta = unit.get_artifact_metadata(attempt_id, "art-01")
            assert meta is not None
            assert meta.state == ArtifactState.STAGED
            assert meta.staged_at == 150
            assert meta.staging_ref == "staging/art-01.tmp.uuid"
            assert meta.revision == 2

    def test_rejects_non_declared_artifact(self, store: SqliteStateStore) -> None:
        """mark_artifact_staged must reject a STAGED artifact."""
        attempt_id = _seed_attempt(store)
        manifest = _make_manifest(attempt_id=attempt_id)
        art = _make_artifact(
            attempt_id=attempt_id,
            artifact_id="art-01",
            state=ArtifactState.STAGED,
            staged_at=120,
        )

        with store.unit_of_work() as unit:
            unit.add_artifact_manifest(manifest, (art,))
            unit.commit()

        with store.unit_of_work() as unit:
            ok = unit.mark_artifact_staged(
                attempt_id=attempt_id,
                artifact_id="art-01",
                staging_path_relative="staging/art-01.tmp.uuid",
                expected_revision=1,
                staged_at=150,
            )
            assert not ok, "mark_artifact_staged must reject non-DECLARED artifact"
            unit.commit()

    def test_rejects_stale_revision(self, store: SqliteStateStore) -> None:
        """mark_artifact_staged must reject a stale revision."""
        attempt_id = _seed_attempt(store)
        manifest = _make_manifest(attempt_id=attempt_id)
        art = _make_artifact(
            attempt_id=attempt_id,
            artifact_id="art-01",
            state=ArtifactState.DECLARED,
        )

        with store.unit_of_work() as unit:
            unit.add_artifact_manifest(manifest, (art,))
            unit.commit()

        with store.unit_of_work() as unit:
            ok = unit.mark_artifact_staged(
                attempt_id=attempt_id,
                artifact_id="art-01",
                staging_path_relative="staging/art-01.tmp.uuid",
                expected_revision=999,  # stale
                staged_at=150,
            )
            assert not ok, "mark_artifact_staged must reject stale revision"
            unit.commit()


class TestMarkArtifactVerified:
    """mark_artifact_verified: STAGED → VERIFIED with CAS guard."""

    def test_staged_to_verified_succeeds(self, store: SqliteStateStore) -> None:
        attempt_id = _seed_attempt(store)
        manifest = _make_manifest(attempt_id=attempt_id)
        art = _make_artifact(
            attempt_id=attempt_id,
            artifact_id="art-01",
            state=ArtifactState.DECLARED,
        )

        with store.unit_of_work() as unit:
            unit.add_artifact_manifest(manifest, (art,))
            unit.commit()

        # Stage first
        with store.unit_of_work() as unit:
            ok = unit.mark_artifact_staged(
                attempt_id=attempt_id,
                artifact_id="art-01",
                staging_path_relative="staging/art-01.tmp.uuid",
                expected_revision=1,
                staged_at=150,
            )
            assert ok
            unit.commit()

        # Then verify
        with store.unit_of_work() as unit:
            ok = unit.mark_artifact_verified(
                attempt_id=attempt_id,
                artifact_id="art-01",
                verified_digest="sha256:abcdef",
                verified_length=1024,
                target_path_relative="output/art-01.dat",
                expected_revision=2,
                verified_at=200,
            )
            assert ok, "mark_artifact_verified should succeed for STAGED artifact"
            unit.commit()

        with store.unit_of_work() as unit:
            meta = unit.get_artifact_metadata(attempt_id, "art-01")
            assert meta is not None
            assert meta.state == ArtifactState.VERIFIED
            assert meta.verified_sha256_hex == "sha256:abcdef"
            assert meta.verified_length == 1024
            assert meta.staging_ref == "output/art-01.dat"
            assert meta.verified_at == 200
            assert meta.revision == 3

    def test_rejects_declared_artifact(self, store: SqliteStateStore) -> None:
        """mark_artifact_verified must reject a DECLARED artifact (requires STAGED)."""
        attempt_id = _seed_attempt(store)
        manifest = _make_manifest(attempt_id=attempt_id)
        art = _make_artifact(
            attempt_id=attempt_id,
            artifact_id="art-01",
            state=ArtifactState.DECLARED,
        )

        with store.unit_of_work() as unit:
            unit.add_artifact_manifest(manifest, (art,))
            unit.commit()

        with store.unit_of_work() as unit:
            ok = unit.mark_artifact_verified(
                attempt_id=attempt_id,
                artifact_id="art-01",
                verified_digest="sha256:abcdef",
                verified_length=1024,
                target_path_relative="output/art-01.dat",
                expected_revision=1,
                verified_at=200,
            )
            assert not ok, "mark_artifact_verified must reject DECLARED artifact"
            unit.commit()

    def test_rejects_stale_revision(self, store: SqliteStateStore) -> None:
        """mark_artifact_verified must reject a stale revision."""
        attempt_id = _seed_attempt(store)
        manifest = _make_manifest(attempt_id=attempt_id)
        art = _make_artifact(
            attempt_id=attempt_id,
            artifact_id="art-01",
            state=ArtifactState.DECLARED,
        )

        with store.unit_of_work() as unit:
            unit.add_artifact_manifest(manifest, (art,))
            unit.commit()

        # Stage first
        with store.unit_of_work() as unit:
            ok = unit.mark_artifact_staged(
                attempt_id=attempt_id,
                artifact_id="art-01",
                staging_path_relative="staging/art-01.tmp.uuid",
                expected_revision=1,
                staged_at=150,
            )
            assert ok
            unit.commit()

        # Try to verify with stale revision
        with store.unit_of_work() as unit:
            ok = unit.mark_artifact_verified(
                attempt_id=attempt_id,
                artifact_id="art-01",
                verified_digest="sha256:abcdef",
                verified_length=1024,
                target_path_relative="output/art-01.dat",
                expected_revision=1,  # stale, should be 2
                verified_at=200,
            )
            assert not ok, "mark_artifact_verified must reject stale revision"
            unit.commit()


class TestReclaimOrphanedArtifact:
    """reclaim_orphaned_artifact: ORPHANED → CLEANED gap-closing method."""

    def test_orphaned_to_cleaned_succeeds(self, store: SqliteStateStore) -> None:
        attempt_id = _seed_attempt(store)
        manifest = _make_manifest(attempt_id=attempt_id)
        art = _make_artifact(
            attempt_id=attempt_id,
            artifact_id="art-01",
            state=ArtifactState.DECLARED,
        )

        with store.unit_of_work() as unit:
            unit.add_artifact_manifest(manifest, (art,))
            unit.commit()

        # Orphan the artifact first
        with store.unit_of_work() as unit:
            ok = unit.mark_artifacts_orphaned(
                attempt_id=attempt_id,
                expected_manifest_revision=1,
                orphaned_at=200,
                failure_code="STAGING_FAILED",
            )
            assert ok
            unit.commit()

        # Now reclaim it
        with store.unit_of_work() as unit:
            meta = unit.get_artifact_metadata(attempt_id, "art-01")
            assert meta is not None
            assert meta.state == ArtifactState.ORPHANED

            ok = unit.reclaim_orphaned_artifact(meta, cleaned_at=300)
            assert ok, "reclaim_orphaned_artifact should succeed for ORPHANED artifact"
            unit.commit()

        with store.unit_of_work() as unit:
            meta = unit.get_artifact_metadata(attempt_id, "art-01")
            assert meta is not None
            assert meta.state == ArtifactState.CLEANED
            assert meta.cleaned_at == 300

    def test_rejects_non_orphaned_artifact(self, store: SqliteStateStore) -> None:
        """reclaim_orphaned_artifact must reject a non-ORPHANED artifact."""
        attempt_id = _seed_attempt(store)
        manifest = _make_manifest(attempt_id=attempt_id)
        art = _make_artifact(
            attempt_id=attempt_id,
            artifact_id="art-01",
            state=ArtifactState.VERIFIED,
            verified_sha256_hex="sha256:abc",
            verified_length=100,
            staged_at=110,
            verified_at=120,
        )

        with store.unit_of_work() as unit:
            unit.add_artifact_manifest(manifest, (art,))
            unit.commit()

        with store.unit_of_work() as unit:
            meta = unit.get_artifact_metadata(attempt_id, "art-01")
            assert meta is not None
            assert meta.state == ArtifactState.VERIFIED

            ok = unit.reclaim_orphaned_artifact(meta, cleaned_at=300)
            assert not ok, "reclaim_orphaned_artifact must reject non-ORPHANED artifact"
            unit.commit()

    def test_rejects_declared_artifact(self, store: SqliteStateStore) -> None:
        """reclaim_orphaned_artifact must reject a DECLARED artifact."""
        attempt_id = _seed_attempt(store)
        manifest = _make_manifest(attempt_id=attempt_id)
        art = _make_artifact(
            attempt_id=attempt_id,
            artifact_id="art-01",
            state=ArtifactState.DECLARED,
        )

        with store.unit_of_work() as unit:
            unit.add_artifact_manifest(manifest, (art,))
            unit.commit()

        with store.unit_of_work() as unit:
            meta = unit.get_artifact_metadata(attempt_id, "art-01")
            assert meta is not None
            assert meta.state == ArtifactState.DECLARED

            ok = unit.reclaim_orphaned_artifact(meta, cleaned_at=300)
            assert not ok, "reclaim_orphaned_artifact must reject DECLARED artifact"
            unit.commit()

    def test_rejects_consumed_artifact(self, store: SqliteStateStore) -> None:
        """reclaim_orphaned_artifact must reject a CONSUMED artifact
        (that's mark_artifact_cleaned's territory)."""
        attempt_id = _seed_attempt(store)
        manifest = _make_manifest(attempt_id=attempt_id)
        art = _make_artifact(
            attempt_id=attempt_id,
            artifact_id="art-01",
            state=ArtifactState.CONSUMED,
            verified_sha256_hex="sha256:abc",
            verified_length=100,
            staged_at=110,
            verified_at=120,
            consumed_at=200,
        )

        with store.unit_of_work() as unit:
            unit.add_artifact_manifest(manifest, (art,))
            unit.commit()

        with store.unit_of_work() as unit:
            meta = unit.get_artifact_metadata(attempt_id, "art-01")
            assert meta is not None

            ok = unit.reclaim_orphaned_artifact(meta, cleaned_at=300)
            assert not ok, "reclaim_orphaned_artifact must reject CONSUMED artifact"
            unit.commit()

    def test_rejects_stale_revision(self, store: SqliteStateStore) -> None:
        """reclaim_orphaned_artifact must reject a stale revision via CAS."""
        attempt_id = _seed_attempt(store)
        manifest = _make_manifest(attempt_id=attempt_id)
        art = _make_artifact(
            attempt_id=attempt_id,
            artifact_id="art-01",
            state=ArtifactState.DECLARED,
        )

        with store.unit_of_work() as unit:
            unit.add_artifact_manifest(manifest, (art,))
            unit.commit()

        # Orphan
        with store.unit_of_work() as unit:
            ok = unit.mark_artifacts_orphaned(
                attempt_id=attempt_id,
                expected_manifest_revision=1,
                orphaned_at=200,
                failure_code="STAGING_FAILED",
            )
            assert ok
            unit.commit()

        # Get the metadata but then mutate it to have a stale revision
        with store.unit_of_work() as unit:
            meta = unit.get_artifact_metadata(attempt_id, "art-01")
            assert meta is not None
            assert meta.state == ArtifactState.ORPHANED

            # Create a stale version by faking the revision
            import dataclasses
            stale_meta = dataclasses.replace(meta, revision=meta.revision - 1)

            ok = unit.reclaim_orphaned_artifact(stale_meta, cleaned_at=300)
            assert not ok, "reclaim_orphaned_artifact must reject stale revision"
            unit.commit()
