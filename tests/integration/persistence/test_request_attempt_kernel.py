"""Integration tests for request, attempt, lease, and outbox kernels."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path

import pytest

from peerhub.core.errors import InvalidMutationError
from peerhub.core.execution import ExecutionCertainty
from peerhub.core.protocol import (
    ATTEMPT_TERMINAL_OBSERVED_EVENT_KIND,
    PROTOCOL_MAJOR,
    PROTOCOL_MINOR,
    SCHEMA_VERSION,
    AttemptTerminalObserved,
    CommandEnvelope,
    ErrorCode,
)
from peerhub.dispatch.contract import (
    ArtifactManifestRecord,
    ArtifactMetadata,
    ArtifactState,
    AskResult,
    CompletionAssessment,
    CompletionAssessmentState,
    CompletionContract,
    CompletionContractKind,
    ExecutionOutcome,
    LeaseState,
    OutboxCheckpoint,
    ProcessBirthIdentity,
    ProtocolAssessment,
    RequestState,
)
from peerhub.dispatch.capability import CapabilityTier
from peerhub.dispatch.model import (
    record_dispatch_intent as reduce_dispatch_intent,
)
from peerhub.dispatch.service import DispatchService
from peerhub.governance.contract import OutboxState
from peerhub.persistence.sqlite import SqliteStateStore
from tests.fakes import DeterministicClock, SequentialIdSource


@pytest.fixture
def store(tmp_path: Path) -> Iterator[SqliteStateStore]:
    state_store = SqliteStateStore(
        tmp_path / "request-attempt.sqlite3",
        workspace_home_id="workspace-request-attempt",
    )
    state_store.initialize()
    try:
        yield state_store
    finally:
        state_store.close()


def _envelope() -> CommandEnvelope:
    return CommandEnvelope(
        protocol_major=PROTOCOL_MAJOR,
        protocol_minor=PROTOCOL_MINOR,
        schema_version=SCHEMA_VERSION,
        client_request_id="client-request-01",
        correlation_id="correlation-01",
        client_id="client-01",
        actor_id="actor-01",
        scope={
            "workspace_id": "workspace-01",
            "home_id": "home-01",
        },
        method="peer.ask",
        params={"prompt": "hello"},
        idempotency_key="idempotency-01",
        expected_policy_revision=7,
        expected_configuration_revision=11,
        client_timestamp=10,
    )


def _contract() -> CompletionContract:
    return CompletionContract(
        contract_id="completion-contract-01",
        kind=CompletionContractKind.DELIVERY_ONLY,
        requirements=(),
        replay_safe=False,
    )


def _service(store: SqliteStateStore) -> DispatchService:
    return DispatchService(
        store,
        clock=DeterministicClock(start=100),
        ids=SequentialIdSource(),
    )


def _admit(service: DispatchService):
    return service.admit_request(
        _envelope(),
        authenticated_principal="principal-01",
        actor_authorized=True,
        completion_contract=_contract(),
        policy_revision=7,
        configuration_revision=11,
        required_capability_tier=CapabilityTier.READ_ONLY,
        selected_peer_instance_id="instance-01",
        selected_profile_id="profile-01",
        route_decision_digest="b" * 64,
        session_id="session-01",
        owner_principal_id="principal-01",
        owner_instance_id="instance-01",
        authority_epoch=5,
        heartbeat_timeout_ms=5_000,
        owner_peer_id="peer-01",
    )


def _verified_result() -> AskResult:
    return AskResult(
        execution=ExecutionOutcome(
            started=True,
            exit_code=0,
            timed_out=False,
            cancelled=False,
            execution_certainty=ExecutionCertainty.TERMINAL,
        ),
        protocol=ProtocolAssessment(
            parsed=True,
            response_present=True,
            vendor_completion_marker=True,
            suspected_truncation=False,
            protocol_failure=None,
        ),
        completion=CompletionAssessment(
            state=CompletionAssessmentState.VERIFIED,
            contract_kind=(
                CompletionContractKind.DELIVERY_ONLY
            ),
            evidence_refs=("terminal-receipt-01",),
        ),
        policy_revision=7,
    )


def test_full_request_attempt_lifecycle_round_trips(
    store: SqliteStateStore,
) -> None:
    service = _service(store)
    admitted, receipt, reserved = _admit(service)

    assert admitted.state is RequestState.ADMITTED
    assert reserved.state.value == "RESERVED"
    assert reserved.fence.attempt_id is None
    assert receipt.command_id == admitted.command_id

    prepared = service.prepare_request(admitted.command_id)
    attempt = service.create_attempt(admitted.command_id)
    intent_request, intent_attempt, intent_lease = (
        service.record_dispatch_intent(
            admitted.command_id,
            attempt.attempt_id,
        )
    )

    assert prepared.state is RequestState.PREPARED
    assert intent_request.state is RequestState.DISPATCH_INTENT
    assert intent_attempt.execution_certainty is (
        ExecutionCertainty.MAY_HAVE_STARTED
    )
    assert intent_lease.fence.attempt_id == attempt.attempt_id
    assert (
        intent_lease.fence.owner_process_birth_identity
        is None
    )

    process_identity = ProcessBirthIdentity(
        pid=4321,
        process_creation_time=9876,
    )
    running_request, running_attempt, active_lease = (
        service.record_running(
            admitted.command_id,
            attempt.attempt_id,
            process_identity=process_identity,
        )
    )
    assert running_request.state is RequestState.RUNNING
    assert running_attempt.execution_certainty is (
        ExecutionCertainty.STARTED
    )
    assert (
        active_lease.fence.owner_process_birth_identity
        == process_identity
    )

    assessing_request, assessing_attempt = (
        service.begin_assessment(
            admitted.command_id,
            attempt.attempt_id,
        )
    )
    assert assessing_request.state is RequestState.ASSESSING
    assert assessing_attempt.execution_certainty is (
        ExecutionCertainty.TERMINAL
    )

    terminal_request, terminal_attempt = (
        service.complete_attempt(
            admitted.command_id,
            attempt.attempt_id,
            result=_verified_result(),
            transport="pipe",
            started_at=running_attempt.updated_at,
            process_integrity=True,
        )
    )
    assert terminal_request.state is (
        RequestState.SUCCEEDED_VERIFIED
    )
    assert terminal_attempt.state is (
        RequestState.SUCCEEDED_VERIFIED
    )
    assert terminal_attempt.result == _verified_result()

    with store.unit_of_work() as unit:
        persisted_request = unit.get_request(
            admitted.command_id
        )
        persisted_attempt = unit.get_attempt(
            attempt.attempt_id
        )
        persisted_lease = unit.get_lease(reserved.lease_id)
        events = unit.list_outbox_events(
            (OutboxState.PENDING,),
            limit=100,
        )

    assert persisted_request == terminal_request
    assert persisted_attempt == terminal_attempt
    assert persisted_lease == active_lease
    assert [event.event_kind for event in events] == [
        "ADMITTED",
        "DISPATCH_INTENT",
        "RUNNING",
        "SUCCEEDED_VERIFIED",
        "AttemptTerminalObserved",
    ]
    assert [
        event.outbox_position for event in events
    ] == [1, 2, 3, 4, 5]


def test_request_attempt_and_lease_cas_reject_stale_snapshots(
    store: SqliteStateStore,
) -> None:
    service = _service(store)
    admitted, _, reserved = _admit(service)
    prepared = service.prepare_request(admitted.command_id)
    attempt = service.create_attempt(admitted.command_id)

    with store.unit_of_work() as unit:
        current_request = unit.get_request(admitted.command_id)
        current_attempt = unit.get_attempt(attempt.attempt_id)
        assert current_request == prepared
        assert current_attempt == attempt

        updated_request = replace(
            current_request,
            state=RequestState.FAILED_PRE_DISPATCH,
            revision=current_request.revision + 1,
            updated_at=500,
            terminal_error_code=ErrorCode.SPAWN_FAILED,
        )
        updated_attempt = replace(
            current_attempt,
            state=RequestState.FAILED_PRE_DISPATCH,
            execution_certainty=ExecutionCertainty.NOT_STARTED,
            revision=current_attempt.revision + 1,
            updated_at=500,
            terminal_error_code=ErrorCode.SPAWN_FAILED,
        )

        assert unit.cas_update_request(
            current_request,
            updated_request,
        )
        assert unit.cas_update_attempt(
            current_attempt,
            updated_attempt,
        )
        unit.commit()

    with store.unit_of_work() as unit:
        assert not unit.cas_update_request(
            prepared,
            replace(
                prepared,
                revision=prepared.revision + 1,
                updated_at=501,
            ),
        )
        assert not unit.cas_update_attempt(
            attempt,
            replace(
                attempt,
                revision=attempt.revision + 1,
                updated_at=501,
            ),
        )

    updated_lease = replace(
        reserved,
        fence=replace(
            reserved.fence,
            revision=reserved.fence.revision + 1,
        ),
        updated_at=502,
    )
    with store.unit_of_work() as unit:
        assert unit.cas_update_lease(
            reserved,
            updated_lease,
        )
        unit.commit()

    with store.unit_of_work() as unit:
        assert not unit.cas_update_lease(
            reserved,
            replace(
                reserved,
                fence=replace(
                    reserved.fence,
                    revision=reserved.fence.revision + 1,
                ),
                updated_at=503,
            ),
        )


def test_active_attempt_uniqueness_and_monotonic_numbering(
    store: SqliteStateStore,
) -> None:
    service = _service(store)
    admitted, _, first_lease = _admit(service)
    service.prepare_request(admitted.command_id)
    first_attempt = service.create_attempt(
        admitted.command_id
    )

    with pytest.raises(sqlite3.IntegrityError):
        service.create_attempt(admitted.command_id)

    failed_request, failed_attempt = (
        service.fail_pre_dispatch(
            admitted.command_id,
            first_attempt.attempt_id,
            error_code=ErrorCode.SPAWN_FAILED,
            transport="pipe",
        )
    )

    (
        retried_request,
        retried_attempt,
        retry_lease,
    ) = service.authorize_retry(
        failed_request.command_id,
        failed_attempt.attempt_id,
        reconciliation_complete=False,
        heartbeat_timeout_ms=5_000,
    )

    assert retried_attempt == failed_attempt
    assert retried_request.state is RequestState.PREPARED
    assert retry_lease.lease_id != first_lease.lease_id
    assert retried_request.lease_id == retry_lease.lease_id
    assert retry_lease.fence.attempt_id is None

    second_attempt = service.create_attempt(
        admitted.command_id
    )
    assert second_attempt.attempt_number == 2
    assert second_attempt.lease_id == retry_lease.lease_id

    with store.unit_of_work() as unit:
        attempts = unit.list_attempts(admitted.command_id)
        persisted_request = unit.get_request(
            admitted.command_id
        )
        persisted_retry_lease = unit.get_lease(
            retry_lease.lease_id
        )

    assert [item.attempt_number for item in attempts] == [1, 2]
    assert persisted_request == retried_request
    assert persisted_retry_lease == retry_lease


def test_reconciled_start_uncertain_retry_rotates_lease(
    store: SqliteStateStore,
) -> None:
    service = _service(store)
    admitted, _, original_lease = _admit(service)
    service.prepare_request(admitted.command_id)
    first_attempt = service.create_attempt(
        admitted.command_id
    )
    _, _, bound_original_lease = (
        service.record_dispatch_intent(
            admitted.command_id,
            first_attempt.attempt_id,
        )
    )
    uncertain_request, uncertain_attempt = (
        service.record_start_uncertain(
            admitted.command_id,
            first_attempt.attempt_id,
        )
    )

    with store.unit_of_work() as unit:
        events = unit.list_outbox_events(
            (OutboxState.PENDING,),
            limit=100,
        )
    assert [event.event_kind for event in events] == [
        "ADMITTED",
        "DISPATCH_INTENT",
        "START_UNCERTAIN",
    ]

    (
        retried_request,
        interrupted_attempt,
        retry_lease,
    ) = service.authorize_retry(
        admitted.command_id,
        first_attempt.attempt_id,
        reconciliation_complete=True,
        heartbeat_timeout_ms=5_000,
    )

    assert uncertain_request.state is RequestState.START_UNCERTAIN
    assert retried_request.state is RequestState.PREPARED
    assert interrupted_attempt.state is RequestState.INTERRUPTED
    assert interrupted_attempt.reconciliation_complete
    assert retry_lease.lease_id != original_lease.lease_id
    assert retry_lease.fence.fencing_token > (
        bound_original_lease.fence.fencing_token
    )
    assert retried_request.lease_id == retry_lease.lease_id
    assert retry_lease.fence.attempt_id is None

    second_attempt = service.create_attempt(
        admitted.command_id
    )
    assert second_attempt.attempt_number == 2
    assert second_attempt.lease_id == retry_lease.lease_id

    (
        second_intent_request,
        second_intent_attempt,
        second_intent_lease,
    ) = service.record_dispatch_intent(
        admitted.command_id,
        second_attempt.attempt_id,
    )
    assert (
        second_intent_request.state
        is RequestState.DISPATCH_INTENT
    )
    assert (
        second_intent_attempt.state
        is RequestState.DISPATCH_INTENT
    )
    assert (
        second_intent_lease.fence.attempt_id
        == second_attempt.attempt_id
    )


def test_dispatch_bundle_cas_rejects_null_attempt_id(
    store: SqliteStateStore,
) -> None:
    service = _service(store)
    admitted, _, reserved = _admit(service)
    prepared = service.prepare_request(admitted.command_id)
    attempt = service.create_attempt(admitted.command_id)

    (
        intent_request,
        intent_attempt,
        intent_lease,
    ) = reduce_dispatch_intent(
        prepared,
        attempt,
        reserved,
        updated_at=500,
    )
    invalid_lease = replace(
        intent_lease,
        fence=replace(
            intent_lease.fence,
            attempt_id=None,
        ),
    )

    with pytest.raises(
        InvalidMutationError,
        match="requires attempt_id",
    ):
        with store.unit_of_work() as unit:
            unit.cas_update_dispatch_bundle(
                prepared,
                intent_request,
                attempt,
                intent_attempt,
                reserved,
                invalid_lease,
            )

    with store.unit_of_work() as unit:
        assert unit.get_request(admitted.command_id) == prepared
        assert unit.get_attempt(attempt.attempt_id) == attempt
        assert unit.get_lease(reserved.lease_id) == reserved


def test_outbox_checkpoint_uses_revision_guarded_cas(
    store: SqliteStateStore,
) -> None:
    service = _service(store)
    admitted, _, _ = _admit(service)
    service.prepare_request(admitted.command_id)
    attempt = service.create_attempt(admitted.command_id)
    service.fail_pre_dispatch(
        admitted.command_id,
        attempt.attempt_id,
        error_code=ErrorCode.SPAWN_FAILED,
        transport="pipe",
    )

    with store.unit_of_work() as unit:
        events = unit.list_outbox_events(
            (OutboxState.PENDING,),
            limit=100,
        )

    assert len(events) == 3
    first_position = events[0].outbox_position
    second_position = events[1].outbox_position
    assert first_position is not None
    assert second_position is not None

    initial = OutboxCheckpoint(
        consumer_id="consumer-01",
        outbox_position=first_position,
        event_id=events[0].event_id,
        revision=1,
    )
    updated = OutboxCheckpoint(
        consumer_id="consumer-01",
        outbox_position=second_position,
        event_id=events[1].event_id,
        revision=2,
    )

    with store.unit_of_work() as unit:
        unit.add_outbox_checkpoint(initial)
        unit.commit()

    with store.unit_of_work() as unit:
        current = unit.get_outbox_checkpoint("consumer-01")
        assert current == initial
        assert unit.cas_update_outbox_checkpoint(
            current,
            updated,
        )
        unit.commit()

    with store.unit_of_work() as unit:
        assert not unit.cas_update_outbox_checkpoint(
            initial,
            updated,
        )
        assert (
            unit.get_outbox_checkpoint("consumer-01")
            == updated
        )


def _make_manifest_and_artifacts(
    attempt_id: str,
    item_count: int = 2,
    manifest_digest: str = "digest-composite-01",
    states: tuple[ArtifactState, ...] = (ArtifactState.VERIFIED, ArtifactState.VERIFIED),
) -> tuple[ArtifactManifestRecord, tuple[ArtifactMetadata, ...]]:
    manifest = ArtifactManifestRecord(
        attempt_id=attempt_id,
        workspace_scope_id="ws-01",
        staging_root_ref=".artifacts/staging",
        manifest_digest=manifest_digest,
        item_count=item_count,
        created_at=100,
        revision=1,
    )
    artifacts = tuple(
        ArtifactMetadata(
            attempt_id=attempt_id,
            artifact_id=f"art-0{i+1}",
            placeholder=f"__ART_0{i+1}__",
            workspace_scope_id="ws-01",
            staging_ref=f"rel/staging/art-0{i+1}.dat",
            access_mode="READ_WRITE",
            declared_lifecycle="EPHEMERAL",
            state=states[i],
            declared_at=100,
            revision=1,
            expected_sha256_hex="abc123",
            expected_length=1024,
            verified_sha256_hex="abc123" if states[i] in (ArtifactState.VERIFIED, ArtifactState.RESERVED, ArtifactState.CONSUMED) else None,
            verified_length=1024 if states[i] in (ArtifactState.VERIFIED, ArtifactState.RESERVED, ArtifactState.CONSUMED) else None,
            verified_object_identity_json='{"inode": 12345}',
            staged_at=105,
            verified_at=110 if states[i] in (ArtifactState.VERIFIED, ArtifactState.RESERVED, ArtifactState.CONSUMED) else None,
        )
        for i in range(item_count)
    )
    return manifest, artifacts


def test_record_dispatch_intent_and_reserve_artifacts_happy_path(
    store: SqliteStateStore,
) -> None:
    service = _service(store)
    admitted, _, lease = _admit(service)
    service.prepare_request(admitted.command_id)
    attempt = service.create_attempt(admitted.command_id)

    manifest, artifacts = _make_manifest_and_artifacts(attempt.attempt_id)
    with store.unit_of_work() as unit:
        unit.add_artifact_manifest(manifest, artifacts)
        unit.commit()

    req_snap, att_snap, lease_snap = service.record_dispatch_intent_and_reserve_artifacts(
        admitted.command_id,
        attempt.attempt_id,
        expected_manifest_digest=manifest.manifest_digest,
    )

    assert req_snap.state == RequestState.DISPATCH_INTENT
    assert att_snap.attempt_id == attempt.attempt_id
    assert lease_snap.fence.attempt_id == attempt.attempt_id

    with store.unit_of_work() as unit:
        fetched_manifest = unit.get_artifact_manifest(attempt.attempt_id)
        assert fetched_manifest is not None
        assert fetched_manifest.intent_event_id is not None

        art1 = unit.get_artifact_metadata(attempt.attempt_id, "art-01")
        art2 = unit.get_artifact_metadata(attempt.attempt_id, "art-02")
        assert art1 is not None and art1.state == ArtifactState.RESERVED
        assert art2 is not None and art2.state == ArtifactState.RESERVED

        events = unit.list_outbox_events((OutboxState.PENDING,), limit=100)
        assert any(e.event_kind == "DISPATCH_INTENT" or e.event_id == fetched_manifest.intent_event_id for e in events)


def test_record_dispatch_intent_and_reserve_artifacts_failure_rolls_back(
    store: SqliteStateStore,
) -> None:
    service = _service(store)
    admitted, _, lease = _admit(service)
    service.prepare_request(admitted.command_id)
    attempt = service.create_attempt(admitted.command_id)

    # art2 is STAGED, not VERIFIED -- reservation must fail
    manifest, artifacts = _make_manifest_and_artifacts(
        attempt.attempt_id,
        states=(ArtifactState.VERIFIED, ArtifactState.STAGED),
    )
    with store.unit_of_work() as unit:
        unit.add_artifact_manifest(manifest, artifacts)
        unit.commit()

    with pytest.raises(InvalidMutationError, match="Artifact reservation failed"):
        service.record_dispatch_intent_and_reserve_artifacts(
            admitted.command_id,
            attempt.attempt_id,
            expected_manifest_digest=manifest.manifest_digest,
        )

    # Prove complete rollback: request is NOT in DISPATCH_INTENT, artifacts unchanged
    with store.unit_of_work() as unit:
        req_in_store = unit.get_request(admitted.command_id)
        assert req_in_store is not None
        assert req_in_store.state == RequestState.PREPARED

        fetched_manifest = unit.get_artifact_manifest(attempt.attempt_id)
        assert fetched_manifest is not None
        assert fetched_manifest.intent_event_id is None

        art1 = unit.get_artifact_metadata(attempt.attempt_id, "art-01")
        art2 = unit.get_artifact_metadata(attempt.attempt_id, "art-02")
        assert art1 is not None and art1.state == ArtifactState.VERIFIED
        assert art2 is not None and art2.state == ArtifactState.STAGED


def test_complete_attempt_with_artifacts_and_lease_happy_path(
    store: SqliteStateStore,
) -> None:
    service = _service(store)
    admitted, _, lease = _admit(service)
    service.prepare_request(admitted.command_id)
    attempt = service.create_attempt(admitted.command_id)

    manifest, artifacts = _make_manifest_and_artifacts(attempt.attempt_id)
    with store.unit_of_work() as unit:
        unit.add_artifact_manifest(manifest, artifacts)
        unit.commit()

    req_snap, att_snap, lease_snap = service.record_dispatch_intent_and_reserve_artifacts(
        admitted.command_id,
        attempt.attempt_id,
        expected_manifest_digest=manifest.manifest_digest,
    )

    process_identity = ProcessBirthIdentity(pid=1234, process_creation_time=50)
    req_snap, att_snap, lease_snap = service.record_running(
        admitted.command_id,
        attempt.attempt_id,
        process_identity=process_identity,
    )
    service.begin_assessment(admitted.command_id, attempt.attempt_id)

    ask_result = AskResult(
        execution=ExecutionOutcome(
            started=True,
            exit_code=0,
            timed_out=False,
            cancelled=False,
            execution_certainty=ExecutionCertainty.TERMINAL,
        ),
        protocol=ProtocolAssessment(
            parsed=True,
            response_present=True,
            vendor_completion_marker=True,
            suspected_truncation=False,
            protocol_failure=None,
        ),
        completion=CompletionAssessment(
            state=CompletionAssessmentState.VERIFIED,
            contract_kind=CompletionContractKind.DELIVERY_ONLY,
        ),
        policy_revision=7,
    )

    updated_req, updated_att = service.complete_attempt_with_artifacts_and_lease(
        admitted.command_id,
        attempt.attempt_id,
        result=ask_result,
        transport="pipe",
        started_at=100,
        final_fence=lease_snap.fence,
    )

    assert updated_req.state == RequestState.SUCCEEDED_VERIFIED
    assert updated_att.attempt_id == attempt.attempt_id

    with store.unit_of_work() as unit:
        # Verify artifact consumption
        fetched_manifest = unit.get_artifact_manifest(attempt.attempt_id)
        assert fetched_manifest is not None
        assert fetched_manifest.consumed_at is not None

        art1 = unit.get_artifact_metadata(attempt.attempt_id, "art-01")
        art2 = unit.get_artifact_metadata(attempt.attempt_id, "art-02")
        assert art1 is not None and art1.state == ArtifactState.CONSUMED
        assert art2 is not None and art2.state == ArtifactState.CONSUMED

        # Verify lease is actually closed
        closed_lease = unit.get_lease(lease.lease_id)
        assert closed_lease is not None
        assert closed_lease.state == LeaseState.RELEASED

        # Verify terminal outcome event matches minted outbox event
        events = unit.list_outbox_events((OutboxState.PENDING,), limit=100)
        terminal_events = [e for e in events if e.event_kind == ATTEMPT_TERMINAL_OBSERVED_EVENT_KIND]
        assert len(terminal_events) == 1
        terminal_evt = terminal_events[0]
        assert terminal_evt.event_id is not None


def test_complete_attempt_with_artifacts_and_lease_without_artifacts(
    store: SqliteStateStore,
) -> None:
    service = _service(store)
    admitted, _, lease = _admit(service)
    service.prepare_request(admitted.command_id)
    attempt = service.create_attempt(admitted.command_id)
    req_snap, att_snap, lease_snap = service.record_dispatch_intent(
        admitted.command_id,
        attempt.attempt_id,
    )
    process_identity = ProcessBirthIdentity(pid=1234, process_creation_time=50)
    req_snap, att_snap, lease_snap = service.record_running(
        admitted.command_id,
        attempt.attempt_id,
        process_identity=process_identity,
    )
    service.begin_assessment(admitted.command_id, attempt.attempt_id)

    ask_result = AskResult(
        execution=ExecutionOutcome(
            started=True,
            exit_code=0,
            timed_out=False,
            cancelled=False,
            execution_certainty=ExecutionCertainty.TERMINAL,
        ),
        protocol=ProtocolAssessment(
            parsed=True,
            response_present=True,
            vendor_completion_marker=True,
            suspected_truncation=False,
            protocol_failure=None,
        ),
        completion=CompletionAssessment(
            state=CompletionAssessmentState.VERIFIED,
            contract_kind=CompletionContractKind.DELIVERY_ONLY,
        ),
        policy_revision=7,
    )

    updated_req, updated_att = service.complete_attempt_with_artifacts_and_lease(
        admitted.command_id,
        attempt.attempt_id,
        result=ask_result,
        transport="pipe",
        started_at=100,
        final_fence=lease_snap.fence,
    )

    assert updated_req.state == RequestState.SUCCEEDED_VERIFIED
    with store.unit_of_work() as unit:
        closed_lease = unit.get_lease(lease.lease_id)
        assert closed_lease is not None
        assert closed_lease.state == LeaseState.RELEASED
