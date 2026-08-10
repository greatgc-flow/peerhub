"""Integration tests for the telemetry operational projector."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from peerhub.core.execution import ExecutionCertainty
from peerhub.core.identity import AuthenticatedSubject
from peerhub.core.protocol import (
    PROTOCOL_MAJOR,
    PROTOCOL_MINOR,
    SCHEMA_VERSION,
    CommandEnvelope,
    ErrorCode,
    OperationalFailureCategory,
)
from peerhub.dispatch.contract import (
    AskResult,
    CompletionAssessment,
    CompletionAssessmentState,
    CompletionContract,
    CompletionContractKind,
    ExecutionOutcome,
    ProcessBirthIdentity,
    ProtocolAssessment,
)
from peerhub.dispatch.capability import CapabilityTier
from peerhub.dispatch.service import DispatchService
from peerhub.governance.contract import OutboxState
from peerhub.persistence.sqlite import SqliteStateStore
from peerhub.telemetry.projections import TelemetryProjector
from tests.fakes import DeterministicClock, SequentialIdSource


@pytest.fixture
def store(tmp_path: Path) -> Iterator[SqliteStateStore]:
    state_store = SqliteStateStore(
        tmp_path / "telemetry.sqlite3",
        workspace_home_id="workspace-telemetry",
    )
    state_store.initialize()
    try:
        yield state_store
    finally:
        state_store.close()


def _envelope(
    *,
    client_request_id: str = "client-request-01",
    idempotency_key: str = "idempotency-01",
) -> CommandEnvelope:
    return CommandEnvelope(
        protocol_major=PROTOCOL_MAJOR,
        protocol_minor=PROTOCOL_MINOR,
        schema_version=SCHEMA_VERSION,
        client_request_id=client_request_id,
        correlation_id="correlation-01",
        client_id="client-01",
        actor_id="actor-01",
        scope={
            "workspace_id": "workspace-01",
            "home_id": "home-01",
        },
        method="peer.ask",
        params={"prompt": "hello"},
        idempotency_key=idempotency_key,
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


def _admit(service: DispatchService, envelope: CommandEnvelope):
    # Increment 4: admit_request also returns the issued capability lease;
    # these fixtures assert on the original three records only.
    return service.admit_request(
        envelope,
        authenticated_subject=AuthenticatedSubject(
            "principal-01",
            "test",
        ),
        completion_contract=_contract(),
        policy_revision=7,
        configuration_revision=11,
        required_capability_tier=CapabilityTier.READ_ONLY,
        selected_peer_instance_id="ag",
        selected_profile_id="ag.deepthink",
        route_decision_digest="a" * 64,
        session_id="session-01",
        owner_principal_id="principal-01",
        owner_instance_id="ag",
        authority_epoch=3,
        heartbeat_timeout_ms=5_000,
        owner_peer_id="peer-01",
    )[:3]


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
            evidence_refs=(),
        ),
        policy_revision=7,
    )


def _run_to_completion(
    service: DispatchService,
    envelope: CommandEnvelope,
) -> None:
    admitted, _, _ = _admit(service, envelope)
    service.prepare_request(admitted.command_id)
    attempt = service.create_attempt(admitted.command_id)
    service.record_dispatch_intent(
        admitted.command_id,
        attempt.attempt_id,
    )
    _, running_attempt, _ = service.record_running(
        admitted.command_id,
        attempt.attempt_id,
        process_identity=ProcessBirthIdentity(
            pid=1,
            process_creation_time=1,
        ),
    )
    service.begin_assessment(
        admitted.command_id,
        attempt.attempt_id,
    )
    service.complete_attempt(
        admitted.command_id,
        attempt.attempt_id,
        result=_verified_result(),
        transport="pipe",
        started_at=running_attempt.updated_at,
        process_integrity=True,
    )


def test_projector_builds_projection_from_terminal_event(
    store: SqliteStateStore,
) -> None:
    service = _service(store)
    _run_to_completion(service, _envelope())

    projector = TelemetryProjector(
        store,
        ids=SequentialIdSource(),
        freshness_ttl=3600,
    )
    projected = projector.project_pending()
    assert projected == 5

    projection = projector.get("ag", "ag.deepthink")
    assert projection.failure_category.value is None
    assert projection.process_integrity.value is True
    assert projection.failure_streak == 0
    assert projection.latency.value is not None

    with store.unit_of_work() as unit:
        checkpoint = unit.events.get_consumer_offset(
            "telemetry.operational.v1"
        )
    assert checkpoint is not None
    assert checkpoint.revision == 5


def test_projector_advances_checkpoint_over_unrelated_events(
    store: SqliteStateStore,
) -> None:
    service = _service(store)
    _run_to_completion(service, _envelope())

    projector = TelemetryProjector(
        store,
        ids=SequentialIdSource(),
        freshness_ttl=3600,
    )
    first_pass = projector.project_pending()
    assert first_pass == 5

    second_pass = projector.project_pending()
    assert second_pass == 0


def test_projector_increments_and_resets_failure_streak(
    store: SqliteStateStore,
) -> None:
    service = _service(store)
    admitted, _, _ = _admit(service, _envelope())
    service.prepare_request(admitted.command_id)
    attempt = service.create_attempt(admitted.command_id)
    service.fail_pre_dispatch(
        admitted.command_id,
        attempt.attempt_id,
        error_code=ErrorCode.SPAWN_FAILED,
        transport="pipe",
        operational_failure_category=(
            OperationalFailureCategory.EXECUTABLE_UNAVAILABLE
        ),
    )

    projector = TelemetryProjector(
        store,
        ids=SequentialIdSource(),
        freshness_ttl=3600,
    )
    projector.project_pending()
    failed_projection = projector.get("ag", "ag.deepthink")
    assert failed_projection.failure_streak == 1
    assert (
        failed_projection.failure_category.value
        is OperationalFailureCategory.EXECUTABLE_UNAVAILABLE
    )

    retried_request, _, retry_lease = service.authorize_retry(
        admitted.command_id,
        attempt.attempt_id,
        reconciliation_complete=False,
        heartbeat_timeout_ms=5_000,
    )
    second_attempt = service.create_attempt(
        admitted.command_id
    )
    service.record_dispatch_intent(
        admitted.command_id,
        second_attempt.attempt_id,
    )
    _, running_attempt, _ = service.record_running(
        admitted.command_id,
        second_attempt.attempt_id,
        process_identity=ProcessBirthIdentity(
            pid=2,
            process_creation_time=2,
        ),
    )
    service.begin_assessment(
        admitted.command_id,
        second_attempt.attempt_id,
    )
    service.complete_attempt(
        admitted.command_id,
        second_attempt.attempt_id,
        result=_verified_result(),
        transport="pipe",
        started_at=running_attempt.updated_at,
        process_integrity=True,
    )

    projector.project_pending()
    healed_projection = projector.get("ag", "ag.deepthink")
    assert healed_projection.failure_streak == 0
    assert healed_projection.failure_category.value is None


def test_projector_checkpoint_and_projection_commit_atomically(
    store: SqliteStateStore,
) -> None:
    service = _service(store)
    _run_to_completion(service, _envelope())

    projector = TelemetryProjector(
        store,
        ids=SequentialIdSource(),
        freshness_ttl=3600,
    )
    projector.project_pending()

    with store.unit_of_work() as unit:
        projection = unit.get_operational_projection(
            "ag",
            "ag.deepthink",
        )
        checkpoint = unit.events.get_consumer_offset(
            "telemetry.operational.v1"
        )
        events = unit.events.list(
            limit=100,
        )

    assert projection is not None
    assert checkpoint is not None
    assert checkpoint.outbox_position == events[-1].outbox_position
    assert projection.revision == 1
