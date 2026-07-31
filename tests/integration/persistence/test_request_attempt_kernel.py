"""Integration tests for request, attempt, lease, and outbox kernels."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path

import pytest

from peerhub.core.execution import ExecutionCertainty
from peerhub.core.protocol import (
    PROTOCOL_MAJOR,
    PROTOCOL_MINOR,
    SCHEMA_VERSION,
    CommandEnvelope,
    ErrorCode,
)
from peerhub.dispatch.contract import (
    AskResult,
    CompletionAssessment,
    CompletionAssessmentState,
    CompletionContract,
    CompletionContractKind,
    ExecutionOutcome,
    OutboxCheckpoint,
    ProcessBirthIdentity,
    ProtocolAssessment,
    RequestState,
)
from peerhub.dispatch.model import authorize_retry
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
        "SUCCEEDED_VERIFIED",
    ]
    assert [
        event.outbox_position for event in events
    ] == [1, 2]


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
    admitted, _, _ = _admit(service)
    service.prepare_request(admitted.command_id)
    first_attempt = service.create_attempt(
        admitted.command_id
    )

    with pytest.raises(sqlite3.IntegrityError):
        service.create_attempt(admitted.command_id)

    with store.unit_of_work() as unit:
        assert unit.list_attempts(admitted.command_id) == (
            first_attempt,
        )

    failed_request, failed_attempt = (
        service.fail_pre_dispatch(
            admitted.command_id,
            first_attempt.attempt_id,
            error_code=ErrorCode.SPAWN_FAILED,
        )
    )

    retried_request, retried_attempt = authorize_retry(
        failed_request,
        failed_attempt,
        reconciliation_complete=False,
        updated_at=600,
    )
    assert retried_attempt == failed_attempt
    assert retried_request.state is RequestState.PREPARED

    with store.unit_of_work() as unit:
        current = unit.get_request(admitted.command_id)
        assert current == failed_request
        assert unit.cas_update_request(
            current,
            retried_request,
        )
        unit.commit()

    second_attempt = service.create_attempt(
        admitted.command_id
    )
    assert second_attempt.attempt_number == 2

    with store.unit_of_work() as unit:
        attempts = unit.list_attempts(admitted.command_id)

    assert [item.attempt_number for item in attempts] == [1, 2]


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
    )

    with store.unit_of_work() as unit:
        events = unit.list_outbox_events(
            (OutboxState.PENDING,),
            limit=100,
        )

    assert len(events) == 2
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
