"""Fault-boundary tests for real request/attempt SQLite transactions."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from peerhub.core.protocol import (
    PROTOCOL_MAJOR,
    PROTOCOL_MINOR,
    SCHEMA_VERSION,
    CommandEnvelope,
    ErrorCode,
)
from peerhub.dispatch.contract import (
    CompletionContract,
    CompletionContractKind,
    RequestState,
)
from peerhub.dispatch.capability import CapabilityTier
from peerhub.dispatch.service import (
    DispatchService,
    FaultInjector,
    FaultPoint,
)
from peerhub.governance.contract import OutboxState
from peerhub.persistence.sqlite import SqliteStateStore
from tests.fakes import DeterministicClock, SequentialIdSource


class RaisingFaultInjector(FaultInjector):
    """Raise at one exact dispatch transaction boundary."""

    def __init__(self, target: str) -> None:
        self._target = target

    def hit(self, point: str) -> None:
        if point == self._target:
            raise RuntimeError(f"injected fault at {point}")


@pytest.fixture
def store(tmp_path: Path) -> Iterator[SqliteStateStore]:
    state_store = SqliteStateStore(
        tmp_path / "request-attempt-faults.sqlite3",
        workspace_home_id="workspace-request-attempt-faults",
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
        client_request_id="client-request-fault",
        correlation_id="correlation-fault",
        client_id="client-fault",
        actor_id="actor-fault",
        scope={
            "workspace_id": "workspace-fault",
            "home_id": "home-fault",
        },
        method="peer.ask",
        params={"prompt": "fault-boundary"},
        idempotency_key="idempotency-fault",
        expected_policy_revision=7,
        expected_configuration_revision=11,
        client_timestamp=10,
    )


def _contract() -> CompletionContract:
    return CompletionContract(
        contract_id="completion-contract-fault",
        kind=CompletionContractKind.DELIVERY_ONLY,
        requirements=(),
        replay_safe=False,
    )


def _service(
    store: SqliteStateStore,
    *,
    fault_point: str | None = None,
    clock_start: int = 100,
    ids: SequentialIdSource | None = None,
) -> DispatchService:
    return DispatchService(
        store,
        clock=DeterministicClock(start=clock_start),
        ids=ids if ids is not None else SequentialIdSource(),
        fault_injector=(
            RaisingFaultInjector(fault_point)
            if fault_point is not None
            else None
        ),
    )


def _admit(service: DispatchService):
    return service.admit_request(
        _envelope(),
        authenticated_principal="principal-fault",
        actor_authorized=True,
        completion_contract=_contract(),
        policy_revision=7,
        configuration_revision=11,
        required_capability_tier=CapabilityTier.READ_ONLY,
        selected_peer_instance_id="instance-fault",
        selected_profile_id="profile-fault",
        route_decision_digest="c" * 64,
        session_id="session-fault",
        owner_principal_id="principal-fault",
        owner_instance_id="instance-fault",
        authority_epoch=9,
        heartbeat_timeout_ms=5_000,
        owner_peer_id="peer-fault",
    )


@pytest.mark.parametrize(
    "fault_point",
    (
        FaultPoint.AFTER_REQUEST_WRITE,
        FaultPoint.AFTER_LEASE_WRITE,
        FaultPoint.AFTER_ADMISSION_RECEIPT_WRITE,
        FaultPoint.AFTER_CLIENT_REQUEST_BINDING_WRITE,
        FaultPoint.AFTER_IDEMPOTENCY_BINDING_WRITE,
        FaultPoint.AFTER_OUTBOX_WRITE,
        FaultPoint.BEFORE_COMMIT,
    ),
)
def test_every_admission_write_boundary_rolls_back_all_state(
    store: SqliteStateStore,
    fault_point: str,
) -> None:
    service = _service(store, fault_point=fault_point)

    with pytest.raises(RuntimeError, match=fault_point):
        _admit(service)

    envelope = _envelope()
    with store.unit_of_work() as unit:
        assert unit.get_request("command-1") is None
        assert unit.get_lease("lease-1") is None
        assert (
            unit.get_admission_receipt("admission-receipt-1")
            is None
        )
        assert (
            unit.get_client_request_binding(
                envelope.client_id,
                envelope.client_request_id,
            )
            is None
        )
        assert (
            unit.get_command_idempotency_binding(
                envelope.client_id,
                envelope.method,
                envelope.idempotency_key or "",
            )
            is None
        )
        assert unit.list_outbox_events(
            (OutboxState.PENDING,),
            limit=100,
        ) == ()

    clean_service = _service(store, clock_start=500)
    request, _, lease = _admit(clean_service)
    assert str(request.command_id) == "command-1"
    assert lease.fence.fencing_token == 1


def test_after_commit_fault_leaves_complete_durable_admission(
    store: SqliteStateStore,
) -> None:
    service = _service(
        store,
        fault_point=FaultPoint.AFTER_COMMIT,
    )

    with pytest.raises(RuntimeError, match="AFTER_COMMIT"):
        _admit(service)

    with store.unit_of_work() as unit:
        request = unit.get_request("command-1")
        receipt = unit.get_admission_receipt(
            "admission-receipt-1"
        )
        lease = unit.get_lease("lease-1")
        events = unit.list_outbox_events(
            (OutboxState.PENDING,),
            limit=100,
        )

    assert request is not None
    assert receipt is not None
    assert lease is not None
    assert len(events) == 1
    assert events[0].event_kind == "ADMITTED"


def test_dispatch_bundle_fault_rolls_back_request_attempt_and_lease(
    store: SqliteStateStore,
) -> None:
    clean = _service(store)
    admitted, _, reserved = _admit(clean)
    prepared = clean.prepare_request(admitted.command_id)
    attempt = clean.create_attempt(admitted.command_id)

    faulting = _service(
        store,
        fault_point=FaultPoint.AFTER_DISPATCH_BUNDLE_CAS,
        clock_start=500,
    )
    with pytest.raises(
        RuntimeError,
        match="AFTER_DISPATCH_BUNDLE_CAS",
    ):
        faulting.record_dispatch_intent(
            admitted.command_id,
            attempt.attempt_id,
        )

    with store.unit_of_work() as unit:
        persisted_request = unit.get_request(
            admitted.command_id
        )
        persisted_attempt = unit.get_attempt(
            attempt.attempt_id
        )
        persisted_lease = unit.get_lease(reserved.lease_id)

    assert persisted_request == prepared
    assert persisted_attempt == attempt
    assert persisted_lease == reserved
    assert persisted_lease.fence.attempt_id is None


def test_terminal_outbox_fault_rolls_back_terminal_state(
    store: SqliteStateStore,
) -> None:
    shared_ids = SequentialIdSource()
    clean = _service(store, ids=shared_ids)
    admitted, _, _ = _admit(clean)
    prepared = clean.prepare_request(admitted.command_id)
    attempt = clean.create_attempt(admitted.command_id)

    faulting = _service(
        store,
        fault_point=FaultPoint.AFTER_OUTBOX_WRITE,
        clock_start=500,
        ids=shared_ids,
    )
    with pytest.raises(RuntimeError, match="AFTER_OUTBOX_WRITE"):
        faulting.fail_pre_dispatch(
            admitted.command_id,
            attempt.attempt_id,
            error_code=ErrorCode.SPAWN_FAILED,
            transport="pipe",
        )

    with store.unit_of_work() as unit:
        persisted_request = unit.get_request(
            admitted.command_id
        )
        persisted_attempt = unit.get_attempt(
            attempt.attempt_id
        )
        events = unit.list_outbox_events(
            (OutboxState.PENDING,),
            limit=100,
        )

    assert persisted_request == prepared
    assert persisted_attempt == attempt
    assert persisted_request.state is RequestState.PREPARED
    assert persisted_attempt.state is RequestState.PREPARED
    assert len(events) == 1
    assert events[0].event_kind == "ADMITTED"
