from __future__ import annotations


from peerhub.core.errors import (
    ActorUnauthorizedError,
    RecordNotFoundError,
    StaleRevisionError,
    InvalidMutationError,
)
from peerhub.core.protocol import (
    ATTEMPT_TERMINAL_OBSERVED_EVENT_KIND,
    PROTOCOL_MAJOR,
    PROTOCOL_MINOR,
    SCHEMA_VERSION,
    AttemptTerminalObserved,
    CommandID,
    OperationalFailureCategory,
)
from peerhub.governance.contract import OutboxEvent, OutboxState

from .contract import AttemptSnapshot, LeaseSnapshot, RequestSnapshot
from .unit_of_work import DispatchUnitOfWork, FaultPoint, FaultInjector


def require_actor_authorized(
    actor_authorized: bool,
    authenticated_principal: str,
) -> None:
    if type(actor_authorized) is not bool:
        raise ValueError("actor_authorized must be a boolean")
    if not actor_authorized:
        raise ActorUnauthorizedError(authenticated_principal)


def require_request(
    unit: DispatchUnitOfWork,
    command_id: CommandID | str,
) -> RequestSnapshot:
    request = unit.get_request(command_id)
    if request is None:
        raise RecordNotFoundError("dispatch_request", str(command_id))
    return request


def require_attempt(
    unit: DispatchUnitOfWork,
    attempt_id: str,
) -> AttemptSnapshot:
    attempt = unit.get_attempt(attempt_id)
    if attempt is None:
        raise RecordNotFoundError("dispatch_attempt", attempt_id)
    return attempt


def require_lease(
    unit: DispatchUnitOfWork,
    lease_id: str,
) -> LeaseSnapshot:
    lease = unit.get_lease(lease_id)
    if lease is None:
        raise RecordNotFoundError("lease", lease_id)
    return lease


def raise_request_cas(
    unit: DispatchUnitOfWork,
    current: RequestSnapshot,
) -> None:
    latest = unit.get_request(current.command_id)
    raise StaleRevisionError(
        str(current.command_id),
        current.revision,
        0 if latest is None else latest.revision,
    )


def raise_attempt_cas(
    unit: DispatchUnitOfWork,
    current: AttemptSnapshot,
) -> None:
    latest = unit.get_attempt(current.attempt_id)
    raise StaleRevisionError(
        current.attempt_id,
        current.revision,
        0 if latest is None else latest.revision,
    )


def cas_request_attempt(
    unit: DispatchUnitOfWork,
    faults: FaultInjector,
    current_request: RequestSnapshot,
    updated_request: RequestSnapshot,
    current_attempt: AttemptSnapshot,
    updated_attempt: AttemptSnapshot,
) -> None:
    if not unit.cas_update_request(current_request, updated_request):
        raise_request_cas(unit, current_request)
    faults.hit(FaultPoint.AFTER_REQUEST_CAS)

    if not unit.cas_update_attempt(current_attempt, updated_attempt):
        raise_attempt_cas(unit, current_attempt)
    faults.hit(FaultPoint.AFTER_ATTEMPT_CAS)


def dispatch_event(
    request: RequestSnapshot,
    *,
    event_id: str,
    occurred_at: int,
) -> OutboxEvent:
    return OutboxEvent(
        event_id=event_id,
        protocol_major=PROTOCOL_MAJOR,
        protocol_minor=PROTOCOL_MINOR,
        schema_version=SCHEMA_VERSION,
        correlation_id=request.correlation_id,
        occurred_at=occurred_at,
        event_kind=request.state.value,
        payload={
            "command_id": str(request.command_id),
            "state": request.state.value,
            "request_revision": request.revision,
            "lease_id": request.lease_id,
            "terminal_error_code": (
                request.terminal_error_code.value
                if request.terminal_error_code is not None
                else None
            ),
        },
        state=OutboxState.PENDING,
        created_at=occurred_at,
    )


def attempt_terminal_event(
    request: RequestSnapshot,
    attempt: AttemptSnapshot,
    *,
    event_id: str,
    terminal_at: int,
    transport: str,
    operational_failure_category: OperationalFailureCategory | None,
    process_integrity: bool,
    started_at: int | None,
    evidence_refs: tuple[str, ...],
) -> OutboxEvent:
    if request.command_id != attempt.command_id:
        raise InvalidMutationError("terminal observation request/attempt mismatch")

    latency = None if started_at is None else terminal_at - started_at
    terminal = AttemptTerminalObserved(
        instance_id=request.selected_peer_instance_id,
        profile_id=request.selected_profile_id,
        transport=transport,
        operational_failure_category=operational_failure_category,
        execution_certainty=attempt.execution_certainty,
        process_integrity=process_integrity,
        started_at=started_at,
        terminal_at=terminal_at,
        latency=latency,
        evidence_refs=evidence_refs,
    )
    return OutboxEvent(
        event_id=event_id,
        protocol_major=PROTOCOL_MAJOR,
        protocol_minor=PROTOCOL_MINOR,
        schema_version=SCHEMA_VERSION,
        correlation_id=request.correlation_id,
        occurred_at=terminal_at,
        event_kind=ATTEMPT_TERMINAL_OBSERVED_EVENT_KIND,
        payload={
            "instance_id": terminal.instance_id,
            "profile_id": terminal.profile_id,
            "transport": terminal.transport,
            "operational_failure_category": (
                terminal.operational_failure_category.value
                if terminal.operational_failure_category is not None
                else None
            ),
            "execution_certainty": terminal.execution_certainty.value,
            "process_integrity": terminal.process_integrity,
            "started_at": terminal.started_at,
            "terminal_at": terminal.terminal_at,
            "latency": terminal.latency,
            "evidence_refs": list(terminal.evidence_refs),  # pyright: ignore[reportArgumentType]
        },
        evidence_refs=terminal.evidence_refs,
        state=OutboxState.PENDING,
        created_at=terminal_at,
    )
