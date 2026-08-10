"""Integration tests for dispatch artifact metadata persistence contract."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest

from peerhub.core.identity import AuthenticatedSubject
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
from peerhub.governance.contract import OutboxEvent, OutboxState
from peerhub.persistence.sqlite import SqliteStateStore
from tests.fakes import DeterministicClock, SequentialIdSource


@pytest.fixture
def store(tmp_path: Path) -> Iterator[SqliteStateStore]:
    state_store = SqliteStateStore(
        tmp_path / "artifact-metadata.sqlite3",
        workspace_home_id="workspace-artifact-test",
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
        contract_id="contract-01",
        kind=CompletionContractKind.DELIVERY_ONLY,
        requirements=(),
        replay_safe=False,
    )
    admitted, receipt, reserved, _capability = service.admit_request(
        envelope,
        authenticated_subject=AuthenticatedSubject(
            "principal-01",
            "test",
        ),
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
    item_count: int = 2,
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
    state: ArtifactState = ArtifactState.VERIFIED,
    declared_at: int = 100,
    revision: int = 1,
    expected_sha256_hex: str | None = "abc123",
    expected_length: int | None = 1024,
    verified_sha256_hex: str | None = "abc123",
    verified_length: int | None = 1024,
    verified_object_identity_json: str | None = '{"inode": 12345}',
    failure_code: str | None = None,
    staged_at: int | None = 105,
    verified_at: int | None = 110,
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


def _make_outbox_event(
    event_id: str | None = None,
    event_kind: str = "DISPATCH_INTENT",
    manifest_digest: str = "digest-01",
) -> OutboxEvent:
    if event_id is None:
        event_id = str(uuid.uuid4())
    return OutboxEvent(
        event_id=event_id,
        protocol_major=1,
        protocol_minor=0,
        schema_version="1",
        correlation_id="corr-01",
        occurred_at=100,
        event_kind=event_kind,
        payload={"manifest_digest": manifest_digest},
        state=OutboxState.PENDING,
        created_at=100,
        request_id="req-01",
        topic="dispatch",
    )


class TestArtifactMetadataPersistence:
    def test_round_trip_manifest_and_metadata(
        self, store: SqliteStateStore
    ) -> None:
        attempt_id = _seed_attempt(store)
        manifest = _make_manifest(attempt_id=attempt_id)
        art1 = _make_artifact(
            attempt_id=attempt_id, artifact_id="art-01", placeholder="__ART_01__"
        )
        art2 = _make_artifact(
            attempt_id=attempt_id,
            artifact_id="art-02",
            placeholder="__ART_02__",
            staging_ref="rel/staging/art-02.dat",
        )

        with store.unit_of_work() as unit:
            unit.add_artifact_manifest(manifest, (art1, art2))
            unit.commit()

        with store.unit_of_work() as unit:
            fetched_manifest = unit.get_artifact_manifest(attempt_id)
            assert fetched_manifest is not None
            assert fetched_manifest.attempt_id == attempt_id
            assert fetched_manifest.manifest_digest == "digest-01"
            assert fetched_manifest.item_count == 2

            fetched_art1 = unit.get_artifact_metadata(attempt_id, "art-01")
            assert fetched_art1 is not None
            assert fetched_art1.placeholder == "__ART_01__"
            assert fetched_art1.state == ArtifactState.VERIFIED

            all_arts = unit.list_artifact_metadata(attempt_id)
            assert len(all_arts) == 2
            assert tuple(a.artifact_id for a in all_arts) == (
                "art-01",
                "art-02",
            )

    def test_cas_update_artifact_metadata_stale_revision_rejected(
        self, store: SqliteStateStore
    ) -> None:
        """CRITICAL SEMANTIC RULE 4: cas_update_artifact_metadata rejects a stale revision."""
        attempt_id = _seed_attempt(store)
        manifest = _make_manifest(attempt_id=attempt_id)
        art1 = _make_artifact(
            attempt_id=attempt_id, artifact_id="art-01", revision=1
        )

        with store.unit_of_work() as unit:
            unit.add_artifact_manifest(manifest, (art1,))
            unit.commit()

        with store.unit_of_work() as unit:
            updated_stale = _make_artifact(
                attempt_id=attempt_id,
                artifact_id="art-01",
                revision=2,
                staged_at=120,
            )
            current_stale = _make_artifact(
                attempt_id=attempt_id, artifact_id="art-01", revision=999
            )
            ok = unit.cas_update_artifact_metadata(
                current_stale, updated_stale
            )
            assert not ok, "CAS update must reject stale revision"

            ok_valid = unit.cas_update_artifact_metadata(art1, updated_stale)
            assert ok_valid, "CAS update must succeed with matching revision"
            unit.commit()

        with store.unit_of_work() as unit:
            fetched = unit.get_artifact_metadata(attempt_id, "art-01")
            assert fetched is not None
            assert fetched.revision == 2
            assert fetched.staged_at == 120

    def test_reserve_verified_artifacts_all_or_nothing(
        self, store: SqliteStateStore
    ) -> None:
        """CRITICAL SEMANTIC RULE 1: reserve_verified_artifacts_for_dispatch is all-or-nothing:

        if any item isn't VERIFIED, the whole reservation fails, zero partial
        state change.
        """
        attempt_id = _seed_attempt(store)
        manifest = _make_manifest(attempt_id=attempt_id, item_count=2)
        art1 = _make_artifact(
            attempt_id=attempt_id,
            artifact_id="art-01",
            state=ArtifactState.VERIFIED,
        )
        art2 = _make_artifact(
            attempt_id=attempt_id,
            artifact_id="art-02",
            placeholder="__ART_02__",
            staging_ref="rel/staging/art-02.dat",
            state=ArtifactState.STAGED,
        )

        with store.unit_of_work() as unit:
            unit.add_artifact_manifest(manifest, (art1, art2))
            unit.commit()

        intent_evt_id = str(uuid.uuid4())
        intent_event = _make_outbox_event(event_id=intent_evt_id)
        with store.unit_of_work() as unit:
            unit.add_outbox_event(intent_event)
            reserved_ok = unit.reserve_verified_artifacts_for_dispatch(
                attempt_id=attempt_id,
                expected_manifest_digest="digest-01",
                intent_event_id=intent_evt_id,
                reserved_at=200,
            )
            assert (
                not reserved_ok
            ), "Reservation must fail if any item is not VERIFIED"
            unit.commit()

        with store.unit_of_work() as unit:
            fetched_art1 = unit.get_artifact_metadata(attempt_id, "art-01")
            fetched_art2 = unit.get_artifact_metadata(attempt_id, "art-02")
            assert (
                fetched_art1 is not None
                and fetched_art1.state == ArtifactState.VERIFIED
            )
            assert (
                fetched_art2 is not None
                and fetched_art2.state == ArtifactState.STAGED
            )

            fetched_manifest = unit.get_artifact_manifest(attempt_id)
            assert (
                fetched_manifest is not None
                and fetched_manifest.intent_event_id is None
            )

    def test_reserve_and_consume_lifecycle_flow(
        self, store: SqliteStateStore
    ) -> None:
        attempt_id = _seed_attempt(store)
        manifest = _make_manifest(attempt_id=attempt_id, item_count=2)
        art1 = _make_artifact(
            attempt_id=attempt_id,
            artifact_id="art-01",
            state=ArtifactState.VERIFIED,
        )
        art2 = _make_artifact(
            attempt_id=attempt_id,
            artifact_id="art-02",
            placeholder="__ART_02__",
            staging_ref="rel/staging/art-02.dat",
            state=ArtifactState.VERIFIED,
        )
        intent_evt_id = str(uuid.uuid4())
        intent_event = _make_outbox_event(event_id=intent_evt_id)

        with store.unit_of_work() as unit:
            unit.add_artifact_manifest(manifest, (art1, art2))
            unit.add_outbox_event(intent_event)
            unit.commit()

        with store.unit_of_work() as unit:
            reserved_ok = unit.reserve_verified_artifacts_for_dispatch(
                attempt_id=attempt_id,
                expected_manifest_digest="digest-01",
                intent_event_id=intent_evt_id,
                reserved_at=200,
            )
            assert (
                reserved_ok
            ), "Reservation should succeed when all items are VERIFIED"
            unit.commit()

        with store.unit_of_work() as unit:
            arts = unit.list_artifact_metadata(attempt_id)
            assert all(a.state == ArtifactState.RESERVED for a in arts)
            m = unit.get_artifact_manifest(attempt_id)
            assert m is not None and m.intent_event_id == intent_evt_id

        term_evt_id = str(uuid.uuid4())
        with store.unit_of_work() as unit:
            consumed_ok = unit.consume_reserved_artifacts(
                attempt_id=attempt_id,
                terminal_outcome_event_id=term_evt_id,
                consumed_at=300,
            )
            assert (
                consumed_ok
            ), "Consumption should succeed when items are RESERVED"
            unit.commit()

        with store.unit_of_work() as unit:
            arts = unit.list_artifact_metadata(attempt_id)
            assert all(a.state == ArtifactState.CONSUMED for a in arts)
            m = unit.get_artifact_manifest(attempt_id)
            assert m is not None and m.consumed_at == 300

    def test_crashed_reserved_artifact_never_auto_reverts_to_verified(
        self, store: SqliteStateStore
    ) -> None:
        """CRITICAL SEMANTIC RULE 2: A crashed RESERVED artifact must NEVER auto-revert to VERIFIED.

        Proves recovery digest/lookup does not silently un-reserve anything.
        """
        attempt_id = _seed_attempt(store)
        manifest = _make_manifest(attempt_id=attempt_id, item_count=1)
        art = _make_artifact(
            attempt_id=attempt_id,
            artifact_id="art-01",
            state=ArtifactState.VERIFIED,
        )
        intent_evt_id = str(uuid.uuid4())
        intent_event = _make_outbox_event(event_id=intent_evt_id)

        with store.unit_of_work() as unit:
            unit.add_artifact_manifest(manifest, (art,))
            unit.add_outbox_event(intent_event)
            unit.reserve_verified_artifacts_for_dispatch(
                attempt_id=attempt_id,
                expected_manifest_digest="digest-01",
                intent_event_id=intent_evt_id,
                reserved_at=200,
            )
            unit.commit()

        with store.unit_of_work() as unit:
            digest = unit.get_artifact_recovery_digest(attempt_id)
            assert digest is not None
            assert digest.intent_event_verified is True
            assert digest.artifacts[0].state == ArtifactState.RESERVED

            art_after = unit.get_artifact_metadata(attempt_id, "art-01")
            assert art_after is not None
            assert (
                art_after.state == ArtifactState.RESERVED
            ), "Recovery lookup must NEVER silently un-reserve a RESERVED artifact"

    def test_mark_artifact_cleaned_rejects_non_consumed_artifact(
        self, store: SqliteStateStore
    ) -> None:
        """CRITICAL SEMANTIC RULE 3: mark_artifact_cleaned must reject a non-CONSUMED artifact (physical cleanup safety)."""
        attempt_id = _seed_attempt(store)
        manifest = _make_manifest(attempt_id=attempt_id, item_count=1)
        art_verified = _make_artifact(
            attempt_id=attempt_id,
            artifact_id="art-01",
            state=ArtifactState.VERIFIED,
        )

        with store.unit_of_work() as unit:
            unit.add_artifact_manifest(manifest, (art_verified,))
            unit.commit()

        with store.unit_of_work() as unit:
            clean_ok = unit.mark_artifact_cleaned(
                art_verified, cleaned_at=400
            )
            assert (
                not clean_ok
            ), "mark_artifact_cleaned must reject non-CONSUMED artifact"
            unit.commit()

        intent_evt_id = str(uuid.uuid4())
        intent_event = _make_outbox_event(event_id=intent_evt_id)
        term_evt_id = str(uuid.uuid4())
        with store.unit_of_work() as unit:
            unit.add_outbox_event(intent_event)
            unit.reserve_verified_artifacts_for_dispatch(
                attempt_id=attempt_id,
                expected_manifest_digest="digest-01",
                intent_event_id=intent_evt_id,
                reserved_at=200,
            )
            unit.consume_reserved_artifacts(
                attempt_id=attempt_id,
                terminal_outcome_event_id=term_evt_id,
                consumed_at=300,
            )
            unit.commit()

        with store.unit_of_work() as unit:
            art_consumed = unit.get_artifact_metadata(attempt_id, "art-01")
            assert (
                art_consumed is not None
                and art_consumed.state == ArtifactState.CONSUMED
            )

            clean_ok = unit.mark_artifact_cleaned(art_consumed, cleaned_at=400)
            assert (
                clean_ok
            ), "mark_artifact_cleaned must succeed on CONSUMED artifact"
            unit.commit()

        with store.unit_of_work() as unit:
            art_cleaned = unit.get_artifact_metadata(attempt_id, "art-01")
            assert art_cleaned is not None
            assert art_cleaned.state == ArtifactState.CLEANED
            assert art_cleaned.cleaned_at == 400

    def test_mark_artifacts_orphaned(self, store: SqliteStateStore) -> None:
        attempt_id = _seed_attempt(store)
        manifest = _make_manifest(
            attempt_id=attempt_id, item_count=2, revision=1
        )
        art1 = _make_artifact(
            attempt_id=attempt_id,
            artifact_id="art-01",
            state=ArtifactState.VERIFIED,
        )
        art2 = _make_artifact(
            attempt_id=attempt_id,
            artifact_id="art-02",
            placeholder="__ART_02__",
            staging_ref="rel/staging/art-02.dat",
            state=ArtifactState.STAGED,
        )

        with store.unit_of_work() as unit:
            unit.add_artifact_manifest(manifest, (art1, art2))
            unit.commit()

        with store.unit_of_work() as unit:
            orphaned_ok = unit.mark_artifacts_orphaned(
                attempt_id=attempt_id,
                expected_manifest_revision=1,
                orphaned_at=250,
                failure_code="SPAWN_FAILED",
            )
            assert (
                orphaned_ok
            ), "Orphaning non-terminal artifacts should succeed"
            unit.commit()

        with store.unit_of_work() as unit:
            arts = unit.list_artifact_metadata(attempt_id)
            for a in arts:
                assert a.state == ArtifactState.ORPHANED
                assert a.failure_code == "SPAWN_FAILED"
                assert a.orphaned_at == 250
