"""Pure reducers for request, attempt, session, and lease state.

Slice 3 consumes only injected observations. It performs no persistence,
clock, filesystem, process, provider, or network work. Output limits,
deadlines, cancellation execution, spawning, and active orphan sweeps
remain Phase 2 or later.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace

from peerhub.core.errors import (
    ConfigurationStaleError,
    InvalidMutationError,
    InvalidStateTransitionError,
    MissingIdempotencyKeyError,
    PolicyStaleError,
    ProtocolVersionMismatchError,
    SchemaVersionUnsupportedError,
    StaleRevisionError,
)
from peerhub.core.execution import ExecutionCertainty
from peerhub.core.protocol import (
    PROTOCOL_MAJOR,
    PROTOCOL_MINOR,
    SCHEMA_VERSION,
    CommandEnvelope,
    CommandID,
    ErrorCode,
    ErrorPhase,
    OperationalFailureCategory,
    RevisionValue,
    canonical_json_bytes,
)
from peerhub.adapters.contract import (
    DecodedOutput,
    DecoderEventKind,
    ProtocolAssessment,
)
from .capability import CapabilityTier

from .contract import (
    AdmissionReceipt,
    AskResult,
    AttemptFailureClassification,
    AttemptSnapshot,
    ClientRequestBinding,
    CommandIdempotencyBinding,
    CompletionContract,
    ExecutionOutcome,
    LeaseAttemptBindRequest,
    LeaseAuthorityCertainty,
    LeaseCloseRequest,
    LeaseCreateRequest,
    LeaseFenceTuple,
    LeaseProcessBindRequest,
    LeaseRenewRequest,
    LeaseReservationRequest,
    LeaseSnapshot,
    LeaseState,
    ProcessBirthIdentity,
    RecoveryDecision,
    RecoveryReceipt,
    RecoveryTrigger,
    RequestSnapshot,
    RequestState,
    SessionBindingKey,
    SessionBindingSnapshot,
    SessionBindingState,
    SessionResumeRequest,
    TERMINAL_REQUEST_STATES,
    TerminalClassification,
    ValidatedSubmission,
)


def canonical_payload_digest(
    envelope: CommandEnvelope,
    *,
    authenticated_principal: str,
    completion_contract: CompletionContract,
) -> str:
    """Hash the frozen Protocol v1 idempotency projection."""

    projection = {
        "protocol_major": envelope.protocol_major,
        "schema_version": envelope.schema_version,
        "authenticated_principal": authenticated_principal,
        "scope": envelope.scope,
        "method": envelope.method,
        "params": envelope.params,
        "expected_revisions": {
            "policy": envelope.expected_policy_revision,
            "configuration": (
                envelope.expected_configuration_revision
            ),
        },
        "completion_contract": (
            completion_contract.canonical_projection()
        ),
    }
    return hashlib.sha256(
        canonical_json_bytes(projection)
    ).hexdigest()


def validate_submission(
    envelope: CommandEnvelope,
    *,
    authenticated_principal: str,
    completion_contract: CompletionContract,
    state_changing: bool,
) -> ValidatedSubmission:
    """Validate versions and compute the immutable intent digest."""

    if (
        envelope.protocol_major != PROTOCOL_MAJOR
        or envelope.protocol_minor != PROTOCOL_MINOR
    ):
        raise ProtocolVersionMismatchError(
            envelope.protocol_major,
            envelope.protocol_minor,
            PROTOCOL_MAJOR,
            PROTOCOL_MINOR,
        )
    if envelope.schema_version != SCHEMA_VERSION:
        raise SchemaVersionUnsupportedError(
            envelope.schema_version,
            SCHEMA_VERSION,
        )
    if state_changing and envelope.idempotency_key is None:
        raise MissingIdempotencyKeyError(envelope.method)
    if not authenticated_principal.strip():
        raise InvalidMutationError(
            "authenticated_principal must be non-empty"
        )

    return ValidatedSubmission(
        envelope=envelope,
        authenticated_principal=authenticated_principal,
        payload_digest=canonical_payload_digest(
            envelope,
            authenticated_principal=authenticated_principal,
            completion_contract=completion_contract,
        ),
        completion_contract=completion_contract,
    )


def _check_expected_revisions(
    envelope: CommandEnvelope,
    *,
    policy_revision: RevisionValue,
    configuration_revision: RevisionValue,
) -> None:
    if (
        envelope.expected_configuration_revision is not None
        and envelope.expected_configuration_revision
        != configuration_revision
    ):
        raise ConfigurationStaleError(
            envelope.expected_configuration_revision,
            configuration_revision,
        )
    if (
        envelope.expected_policy_revision is not None
        and envelope.expected_policy_revision != policy_revision
    ):
        raise PolicyStaleError(
            envelope.expected_policy_revision,
            policy_revision,
        )


def admit_request(
    submission: ValidatedSubmission,
    *,
    command_id: CommandID,
    admission_receipt_id: str,
    lease_id: str,
    policy_revision: RevisionValue,
    configuration_revision: RevisionValue,
    required_capability_tier: CapabilityTier,
    selected_peer_instance_id: str,
    selected_profile_id: str,
    route_decision_digest: str,
    admitted_at: int,
) -> tuple[
    RequestSnapshot,
    ClientRequestBinding,
    CommandIdempotencyBinding,
    AdmissionReceipt,
]:
    """Create all immutable admission records after authorization."""

    envelope = submission.envelope
    if envelope.idempotency_key is None:
        raise MissingIdempotencyKeyError(envelope.method)

    _check_expected_revisions(
        envelope,
        policy_revision=policy_revision,
        configuration_revision=configuration_revision,
    )

    request = RequestSnapshot(
        command_id=command_id,
        client_id=envelope.client_id,
        client_request_id=envelope.client_request_id,
        correlation_id=envelope.correlation_id,
        authenticated_principal=(
            submission.authenticated_principal
        ),
        command_type=envelope.method,
        idempotency_key=envelope.idempotency_key,
        payload_digest=submission.payload_digest,
        scope=envelope.scope,
        params=envelope.params,
        expected_policy_revision=(
            envelope.expected_policy_revision
        ),
        expected_configuration_revision=(
            envelope.expected_configuration_revision
        ),
        policy_revision=policy_revision,
        configuration_revision=configuration_revision,
        completion_contract=submission.completion_contract,
        required_capability_tier=required_capability_tier,
        selected_peer_instance_id=selected_peer_instance_id,
        selected_profile_id=selected_profile_id,
        route_decision_digest=route_decision_digest,
        lease_id=lease_id,
        state=RequestState.ADMITTED,
        revision=1,
        created_at=admitted_at,
        updated_at=admitted_at,
    )
    client_binding = ClientRequestBinding(
        client_id=envelope.client_id,
        client_request_id=envelope.client_request_id,
        payload_digest=submission.payload_digest,
        command_id=command_id,
        admission_receipt_id=admission_receipt_id,
        created_at=admitted_at,
    )
    idempotency_binding = CommandIdempotencyBinding(
        client_id=envelope.client_id,
        command_type=envelope.method,
        idempotency_key=envelope.idempotency_key,
        payload_digest=submission.payload_digest,
        command_id=command_id,
        admission_receipt_id=admission_receipt_id,
        created_at=admitted_at,
    )
    receipt = AdmissionReceipt(
        admission_receipt_id=admission_receipt_id,
        command_id=command_id,
        client_id=envelope.client_id,
        client_request_id=envelope.client_request_id,
        command_type=envelope.method,
        idempotency_key=envelope.idempotency_key,
        payload_digest=submission.payload_digest,
        completion_contract_id=(
            submission.completion_contract.contract_id
        ),
        lease_id=lease_id,
        policy_revision=policy_revision,
        configuration_revision=configuration_revision,
        admitted_at=admitted_at,
    )
    return (
        request,
        client_binding,
        idempotency_binding,
        receipt,
    )


def _transition_request(
    current: RequestSnapshot,
    *,
    expected_states: tuple[RequestState, ...],
    target_state: RequestState,
    updated_at: int,
    terminal_error_code: ErrorCode | None = None,
) -> RequestSnapshot:
    if current.state not in expected_states:
        raise InvalidStateTransitionError(
            "request",
            str(current.command_id),
            current.state.value,
            target_state.value,
        )
    return replace(
        current,
        state=target_state,
        revision=current.revision + 1,
        updated_at=updated_at,
        terminal_error_code=terminal_error_code,
    )


def _transition_attempt(
    current: AttemptSnapshot,
    *,
    expected_states: tuple[RequestState, ...],
    target_state: RequestState,
    certainty: ExecutionCertainty,
    updated_at: int,
    result: AskResult | None = None,
    terminal_error_code: ErrorCode | None = None,
    reconciliation_complete: bool | None = None,
) -> AttemptSnapshot:
    if current.state not in expected_states:
        raise InvalidStateTransitionError(
            "attempt",
            current.attempt_id,
            current.state.value,
            target_state.value,
        )

    changes: dict[str, object] = {
        "state": target_state,
        "execution_certainty": certainty,
        "revision": current.revision + 1,
        "updated_at": updated_at,
        "result": result,
        "terminal_error_code": terminal_error_code,
    }
    if reconciliation_complete is not None:
        changes["reconciliation_complete"] = (
            reconciliation_complete
        )
    return replace(current, **changes)


def _require_same_command(
    request: RequestSnapshot,
    attempt: AttemptSnapshot,
) -> None:
    if attempt.command_id != request.command_id:
        raise InvalidMutationError(
            "request and attempt command IDs do not match"
        )


def reject_request_policy(
    current: RequestSnapshot,
    *,
    error_code: ErrorCode,
    updated_at: int,
) -> RequestSnapshot:
    """Reject an admitted request without dispatch."""

    if error_code not in {
        ErrorCode.ACTOR_UNAUTHORIZED,
        ErrorCode.SCOPE_UNAUTHORIZED,
        ErrorCode.PEER_UNAVAILABLE,
        ErrorCode.PROFILE_UNAVAILABLE,
        ErrorCode.ROUTE_EXHAUSTED,
        ErrorCode.ADMISSION_CLOSED,
        ErrorCode.CONFIGURATION_STALE,
        ErrorCode.POLICY_STALE,
    }:
        raise InvalidMutationError(
            "unsupported policy rejection error code"
        )
    return _transition_request(
        current,
        expected_states=(RequestState.ADMITTED,),
        target_state=RequestState.REJECTED_POLICY,
        updated_at=updated_at,
        terminal_error_code=error_code,
    )


def prepare_request(
    current: RequestSnapshot,
    *,
    session_binding: SessionBindingSnapshot | None,
    lease: LeaseSnapshot,
    updated_at: int,
) -> RequestSnapshot:
    """Validate the frozen binding and enter PREPARED."""

    if lease.lease_id != current.lease_id:
        raise InvalidMutationError(
            "request and lease IDs do not match"
        )
    if lease.fence.command_id != current.command_id:
        raise InvalidMutationError(
            "request and lease command IDs do not match"
        )
    if lease.state is not LeaseState.RESERVED:
        raise InvalidMutationError(
            "request preparation requires a RESERVED lease"
        )
    if lease.fence.attempt_id is not None:
        raise InvalidMutationError(
            "unprepared lease is already bound to an attempt"
        )
    if lease.fence.owner_process_birth_identity is not None:
        raise InvalidMutationError(
            "pre-spawn lease already has process identity"
        )

    if session_binding is not None:
        if (
            session_binding.current_lease_id
            != lease.lease_id
        ):
            raise InvalidMutationError(
                "session binding does not own the request lease"
            )
        if (
            session_binding.key.instance_id
            != current.selected_peer_instance_id
        ):
            raise InvalidMutationError(
                "session instance does not match frozen route"
            )
        if (
            session_binding.key.profile_id
            != current.selected_profile_id
        ):
            raise InvalidMutationError(
                "session profile does not match frozen route"
            )

    return _transition_request(
        current,
        expected_states=(RequestState.ADMITTED,),
        target_state=RequestState.PREPARED,
        updated_at=updated_at,
    )


def create_attempt(
    request: RequestSnapshot,
    lease: LeaseSnapshot,
    *,
    attempt_id: str,
    attempt_number: int,
    created_at: int,
) -> AttemptSnapshot:
    """Create one attempt under a prepared request."""

    if request.state is not RequestState.PREPARED:
        raise InvalidStateTransitionError(
            "request",
            str(request.command_id),
            request.state.value,
            RequestState.PREPARED.value,
        )
    if lease.lease_id != request.lease_id:
        raise InvalidMutationError(
            "attempt lease does not match request lease"
        )
    if lease.fence.command_id != request.command_id:
        raise InvalidMutationError(
            "attempt lease command does not match request"
        )
    if lease.state is not LeaseState.RESERVED:
        raise InvalidMutationError(
            "attempt creation requires a RESERVED lease"
        )
    if lease.fence.attempt_id is not None:
        raise InvalidMutationError(
            "lease is already bound to an attempt"
        )

    return AttemptSnapshot(
        attempt_id=attempt_id,
        command_id=request.command_id,
        attempt_number=attempt_number,
        lease_id=lease.lease_id,
        state=RequestState.PREPARED,
        execution_certainty=ExecutionCertainty.NOT_STARTED,
        revision=1,
        created_at=created_at,
        updated_at=created_at,
    )


def fail_pre_dispatch(
    request: RequestSnapshot,
    attempt: AttemptSnapshot,
    *,
    error_code: ErrorCode,
    updated_at: int,
) -> tuple[RequestSnapshot, AttemptSnapshot]:
    """Record a proven pre-dispatch failure."""

    _require_same_command(request, attempt)
    updated_request = _transition_request(
        request,
        expected_states=(RequestState.PREPARED,),
        target_state=RequestState.FAILED_PRE_DISPATCH,
        updated_at=updated_at,
        terminal_error_code=error_code,
    )
    updated_attempt = _transition_attempt(
        attempt,
        expected_states=(RequestState.PREPARED,),
        target_state=RequestState.FAILED_PRE_DISPATCH,
        certainty=ExecutionCertainty.NOT_STARTED,
        updated_at=updated_at,
        terminal_error_code=error_code,
    )
    return (updated_request, updated_attempt)


def record_dispatch_intent(
    request: RequestSnapshot,
    attempt: AttemptSnapshot,
    lease: LeaseSnapshot,
    *,
    updated_at: int,
) -> tuple[
    RequestSnapshot,
    AttemptSnapshot,
    LeaseSnapshot,
]:
    """Commit the replay-safety boundary and bind the attempt."""

    _require_same_command(request, attempt)
    if lease.lease_id != attempt.lease_id:
        raise InvalidMutationError(
            "attempt and lease IDs do not match"
        )
    if lease.fence.command_id != request.command_id:
        raise InvalidMutationError(
            "lease command ID does not match request"
        )
    if lease.state is not LeaseState.RESERVED:
        raise InvalidMutationError(
            "dispatch intent requires a RESERVED lease"
        )
    if lease.fence.attempt_id is not None:
        raise InvalidMutationError(
            "lease is already bound to an attempt"
        )
    if lease.fence.owner_process_birth_identity is not None:
        raise InvalidMutationError(
            "dispatch intent cannot have process identity"
        )

    updated_request = _transition_request(
        request,
        expected_states=(RequestState.PREPARED,),
        target_state=RequestState.DISPATCH_INTENT,
        updated_at=updated_at,
    )
    updated_attempt = _transition_attempt(
        attempt,
        expected_states=(RequestState.PREPARED,),
        target_state=RequestState.DISPATCH_INTENT,
        certainty=ExecutionCertainty.MAY_HAVE_STARTED,
        updated_at=updated_at,
    )
    updated_fence = replace(
        lease.fence,
        attempt_id=attempt.attempt_id,
        revision=lease.fence.revision + 1,
    )
    updated_lease = replace(
        lease,
        fence=updated_fence,
        updated_at=updated_at,
    )
    return (
        updated_request,
        updated_attempt,
        updated_lease,
    )


def record_start_uncertain(
    request: RequestSnapshot,
    attempt: AttemptSnapshot,
    *,
    updated_at: int,
) -> tuple[RequestSnapshot, AttemptSnapshot]:
    """Record a crash after intent but before process identity."""

    _require_same_command(request, attempt)
    updated_request = _transition_request(
        request,
        expected_states=(RequestState.DISPATCH_INTENT,),
        target_state=RequestState.START_UNCERTAIN,
        updated_at=updated_at,
        terminal_error_code=ErrorCode.START_UNCERTAIN,
    )
    updated_attempt = _transition_attempt(
        attempt,
        expected_states=(RequestState.DISPATCH_INTENT,),
        target_state=RequestState.START_UNCERTAIN,
        certainty=ExecutionCertainty.MAY_HAVE_STARTED,
        updated_at=updated_at,
        terminal_error_code=ErrorCode.START_UNCERTAIN,
    )
    return (updated_request, updated_attempt)


def record_running(
    request: RequestSnapshot,
    attempt: AttemptSnapshot,
    lease: LeaseSnapshot,
    *,
    process_identity: ProcessBirthIdentity,
    updated_at: int,
) -> tuple[
    RequestSnapshot,
    AttemptSnapshot,
    LeaseSnapshot,
]:
    """Bind observed process identity and enter RUNNING."""

    _require_same_command(request, attempt)
    if lease.state is not LeaseState.RESERVED:
        raise InvalidMutationError(
            "RUNNING requires a RESERVED lease"
        )
    if lease.fence.command_id != request.command_id:
        raise InvalidMutationError(
            "lease command ID does not match request"
        )
    if lease.fence.attempt_id != attempt.attempt_id:
        raise InvalidMutationError(
            "lease attempt ID does not match attempt"
        )
    if lease.fence.owner_process_birth_identity is not None:
        raise InvalidMutationError(
            "lease process identity is already bound"
        )

    updated_request = _transition_request(
        request,
        expected_states=(RequestState.DISPATCH_INTENT,),
        target_state=RequestState.RUNNING,
        updated_at=updated_at,
    )
    updated_attempt = _transition_attempt(
        attempt,
        expected_states=(RequestState.DISPATCH_INTENT,),
        target_state=RequestState.RUNNING,
        certainty=ExecutionCertainty.STARTED,
        updated_at=updated_at,
    )
    updated_fence = replace(
        lease.fence,
        owner_process_birth_identity=process_identity,
        revision=lease.fence.revision + 1,
    )
    updated_lease = replace(
        lease,
        fence=updated_fence,
        state=LeaseState.ACTIVE,
        updated_at=updated_at,
    )
    return (
        updated_request,
        updated_attempt,
        updated_lease,
    )


def begin_cancellation(
    request: RequestSnapshot,
    attempt: AttemptSnapshot,
    *,
    updated_at: int,
) -> tuple[RequestSnapshot, AttemptSnapshot]:
    """Enter CANCELLING without claiming process termination."""

    _require_same_command(request, attempt)
    return (
        _transition_request(
            request,
            expected_states=(RequestState.RUNNING,),
            target_state=RequestState.CANCELLING,
            updated_at=updated_at,
        ),
        _transition_attempt(
            attempt,
            expected_states=(RequestState.RUNNING,),
            target_state=RequestState.CANCELLING,
            certainty=ExecutionCertainty.STARTED,
            updated_at=updated_at,
        ),
    )


def begin_assessment(
    request: RequestSnapshot,
    attempt: AttemptSnapshot,
    *,
    updated_at: int,
) -> tuple[RequestSnapshot, AttemptSnapshot]:
    """Enter ASSESSING after terminal process evidence."""

    _require_same_command(request, attempt)
    expected = (
        RequestState.RUNNING,
        RequestState.CANCELLING,
    )
    return (
        _transition_request(
            request,
            expected_states=expected,
            target_state=RequestState.ASSESSING,
            updated_at=updated_at,
        ),
        _transition_attempt(
            attempt,
            expected_states=expected,
            target_state=RequestState.ASSESSING,
            certainty=ExecutionCertainty.TERMINAL,
            updated_at=updated_at,
        ),
    )


def _terminal_error_for_result(
    result: AskResult,
) -> ErrorCode | None:
    status = result.effective_status
    if status is RequestState.INCOMPLETE:
        return ErrorCode.COMPLETION_INCOMPLETE
    if status is RequestState.DELIVERED_UNVERIFIED:
        return ErrorCode.COMPLETION_UNVERIFIED
    if status is RequestState.INTERRUPTED:
        if result.execution.timed_out:
            return ErrorCode.PROCESS_TIMEOUT
        return ErrorCode.PROCESS_KILLED
    if status is RequestState.FAILED:
        if result.protocol.protocol_failure is not None:
            return result.protocol.protocol_failure
        return ErrorCode.PROTOCOL_ASSESSMENT_FAILED
    return None


def complete_attempt(
    request: RequestSnapshot,
    attempt: AttemptSnapshot,
    *,
    result: AskResult,
    updated_at: int,
) -> tuple[RequestSnapshot, AttemptSnapshot]:
    """Derive and commit the terminal lifecycle state."""

    _require_same_command(request, attempt)
    target_state = result.effective_status
    if target_state not in TERMINAL_REQUEST_STATES:
        raise InvalidMutationError(
            "AskResult did not derive a terminal state"
        )
    terminal_error = _terminal_error_for_result(result)

    updated_request = _transition_request(
        request,
        expected_states=(RequestState.ASSESSING,),
        target_state=target_state,
        updated_at=updated_at,
        terminal_error_code=terminal_error,
    )
    updated_attempt = _transition_attempt(
        attempt,
        expected_states=(RequestState.ASSESSING,),
        target_state=target_state,
        certainty=result.execution.execution_certainty,
        updated_at=updated_at,
        result=result,
        terminal_error_code=terminal_error,
    )
    return (updated_request, updated_attempt)


@dataclass(frozen=True)
class ValidatedRetryRouteBinding:
    """Route fields proven by the atomic retry authorization coordinator."""

    configuration_revision: RevisionValue
    selected_peer_instance_id: str
    selected_profile_id: str
    route_decision_digest: str


def validate_retry_authorizable(
    request: RequestSnapshot,
    previous_attempt: AttemptSnapshot,
    *,
    reconciliation_complete: bool,
) -> None:
    """Validate retry state and replay safety without allocating authority."""

    _require_same_command(request, previous_attempt)
    if previous_attempt.lease_id != request.lease_id:
        raise InvalidMutationError(
            "previous attempt does not use the request lease"
        )

    retryable_states = {
        RequestState.FAILED_PRE_DISPATCH,
        RequestState.START_UNCERTAIN,
        RequestState.INCOMPLETE,
        RequestState.FAILED,
        RequestState.INTERRUPTED,
    }
    if request.state not in retryable_states:
        raise InvalidStateTransitionError(
            "request",
            str(request.command_id),
            request.state.value,
            RequestState.PREPARED.value,
        )
    if previous_attempt.state not in retryable_states:
        raise InvalidStateTransitionError(
            "attempt",
            previous_attempt.attempt_id,
            previous_attempt.state.value,
            RequestState.PREPARED.value,
        )

    safe = (
        previous_attempt.execution_certainty
        is ExecutionCertainty.NOT_STARTED
        or reconciliation_complete
        or request.completion_contract.replay_safe
    )
    if not safe:
        raise InvalidMutationError(
            "retry requires NOT_STARTED evidence, reconciliation, "
            "or an explicitly replay-safe completion contract"
        )


def authorize_retry(
    request: RequestSnapshot,
    previous_attempt: AttemptSnapshot,
    new_lease: LeaseSnapshot,
    *,
    route_binding: ValidatedRetryRouteBinding,
    reconciliation_complete: bool,
    updated_at: int,
) -> tuple[RequestSnapshot, AttemptSnapshot]:
    """Authorize a new attempt only under a fresh RESERVED lease."""

    validate_retry_authorizable(
        request,
        previous_attempt,
        reconciliation_complete=reconciliation_complete,
    )
    if new_lease.lease_id == request.lease_id:
        raise InvalidMutationError(
            "retry requires a freshly reserved lease"
        )
    if new_lease.fence.command_id != request.command_id:
        raise InvalidMutationError(
            "retry lease command ID does not match request"
        )
    if new_lease.state is not LeaseState.RESERVED:
        raise InvalidMutationError(
            "retry requires a RESERVED lease"
        )
    if new_lease.fence.attempt_id is not None:
        raise InvalidMutationError(
            "retry lease is already bound to an attempt"
        )
    if new_lease.fence.owner_process_birth_identity is not None:
        raise InvalidMutationError(
            "retry lease already has process identity"
        )

    updated_attempt = previous_attempt
    if previous_attempt.state not in TERMINAL_REQUEST_STATES:
        updated_attempt = _transition_attempt(
            previous_attempt,
            expected_states=(
                RequestState.DISPATCH_INTENT,
                RequestState.START_UNCERTAIN,
                RequestState.RUNNING,
                RequestState.CANCELLING,
            ),
            target_state=RequestState.INTERRUPTED,
            certainty=previous_attempt.execution_certainty,
            updated_at=updated_at,
            terminal_error_code=ErrorCode.START_UNCERTAIN,
            reconciliation_complete=reconciliation_complete,
        )
    elif (
        reconciliation_complete
        and not previous_attempt.reconciliation_complete
    ):
        updated_attempt = replace(
            previous_attempt,
            reconciliation_complete=True,
            revision=previous_attempt.revision + 1,
            updated_at=updated_at,
        )

    updated_request = replace(
        request,
        lease_id=new_lease.lease_id,
        configuration_revision=route_binding.configuration_revision,
        selected_peer_instance_id=(
            route_binding.selected_peer_instance_id
        ),
        selected_profile_id=route_binding.selected_profile_id,
        route_decision_digest=route_binding.route_decision_digest,
        state=RequestState.PREPARED,
        revision=request.revision + 1,
        updated_at=updated_at,
        terminal_error_code=None,
    )
    return (updated_request, updated_attempt)


def validate_lease_fence(
    persisted: LeaseFenceTuple,
    requester: LeaseFenceTuple,
) -> tuple[bool, tuple[str, ...]]:
    """Compare every security-authoritative fence dimension."""

    mismatches: list[str] = []

    for name in (
        "session_id",
        "lease_id",
        "command_id",
        "attempt_id",
        "fencing_token",
        "authority_epoch",
        "revision",
        "owner_principal_id",
        "owner_instance_id",
    ):
        if getattr(requester, name) != getattr(persisted, name):
            mismatches.append(name)

    requester_process = (
        requester.owner_process_birth_identity
    )
    persisted_process = (
        persisted.owner_process_birth_identity
    )
    if requester_process is None or persisted_process is None:
        if requester_process != persisted_process:
            mismatches.append("owner_process_birth_identity")
    else:
        if requester_process.pid != persisted_process.pid:
            mismatches.append(
                "owner_process_birth_identity.pid"
            )
        if (
            requester_process.process_creation_time
            != persisted_process.process_creation_time
        ):
            mismatches.append(
                "owner_process_birth_identity.process_creation_time"
            )

    # owner_peer_id remains descriptive metadata.
    return (not mismatches, tuple(mismatches))


def create_lease(
    request: LeaseCreateRequest,
    *,
    lease_id: str,
    created_at: int,
    fencing_token: int = 1,
) -> LeaseSnapshot:
    """Create the existing immediately ACTIVE lease shape."""

    if request.heartbeat_timeout_ms < 1:
        raise InvalidMutationError(
            "heartbeat_timeout_ms must be positive"
        )

    fence = LeaseFenceTuple(
        session_id=request.session_id,
        lease_id=lease_id,
        fencing_token=fencing_token,
        revision=1,
        owner_principal_id=request.owner_principal_id,
        owner_instance_id=request.owner_instance_id,
        owner_process_birth_identity=(
            request.owner_process_birth_identity
        ),
        command_id=request.command_id,
        authority_epoch=request.authority_epoch,
        attempt_id=request.attempt_id,
        owner_peer_id=request.owner_peer_id,
    )
    return LeaseSnapshot(
        lease_id=lease_id,
        session_id=request.session_id,
        fence=fence,
        state=LeaseState.ACTIVE,
        heartbeat_expires_at=(
            created_at + request.heartbeat_timeout_ms
        ),
        created_at=created_at,
        updated_at=created_at,
    )


def reserve_lease(
    request: LeaseReservationRequest,
    *,
    lease_id: str,
    fencing_token: int,
    created_at: int,
) -> LeaseSnapshot:
    """Create a pre-spawn RESERVED lease."""

    if request.heartbeat_timeout_ms < 1:
        raise InvalidMutationError(
            "heartbeat_timeout_ms must be positive"
        )

    fence = LeaseFenceTuple(
        session_id=request.session_id,
        lease_id=lease_id,
        fencing_token=fencing_token,
        revision=1,
        owner_principal_id=request.owner_principal_id,
        owner_instance_id=request.owner_instance_id,
        owner_process_birth_identity=None,
        command_id=request.command_id,
        authority_epoch=request.authority_epoch,
        attempt_id=None,
        owner_peer_id=request.owner_peer_id,
    )
    return LeaseSnapshot(
        lease_id=lease_id,
        session_id=request.session_id,
        fence=fence,
        state=LeaseState.RESERVED,
        heartbeat_expires_at=(
            created_at + request.heartbeat_timeout_ms
        ),
        created_at=created_at,
        updated_at=created_at,
    )


def bind_lease_attempt(
    current: LeaseSnapshot,
    request: LeaseAttemptBindRequest,
    *,
    updated_at: int,
) -> LeaseSnapshot:
    """Bind a RESERVED lease to an attempt."""

    if current.state is not LeaseState.RESERVED:
        raise InvalidMutationError(
            "attempt binding requires a RESERVED lease"
        )
    is_match, mismatches = validate_lease_fence(
        current.fence,
        request.fence,
    )
    if not is_match:
        raise InvalidMutationError(
            f"lease fence mismatch on fields: {', '.join(mismatches)}"
        )
    if current.fence.attempt_id is not None:
        raise InvalidMutationError(
            "lease already has an attempt"
        )

    return replace(
        current,
        fence=replace(
            current.fence,
            attempt_id=request.attempt_id,
            revision=current.fence.revision + 1,
        ),
        updated_at=updated_at,
    )


def bind_lease_process(
    current: LeaseSnapshot,
    request: LeaseProcessBindRequest,
    *,
    updated_at: int,
) -> LeaseSnapshot:
    """Bind process identity and activate a RESERVED lease."""

    if current.state is not LeaseState.RESERVED:
        raise InvalidMutationError(
            "process binding requires a RESERVED lease"
        )
    is_match, mismatches = validate_lease_fence(
        current.fence,
        request.fence,
    )
    if not is_match:
        raise InvalidMutationError(
            f"lease fence mismatch on fields: {', '.join(mismatches)}"
        )
    if current.fence.attempt_id is None:
        raise InvalidMutationError(
            "process binding requires attempt_id"
        )
    if current.fence.owner_process_birth_identity is not None:
        raise InvalidMutationError(
            "lease already has process identity"
        )

    return replace(
        current,
        fence=replace(
            current.fence,
            owner_process_birth_identity=(
                request.owner_process_birth_identity
            ),
            revision=current.fence.revision + 1,
        ),
        state=LeaseState.ACTIVE,
        updated_at=updated_at,
    )


def renew_lease(
    current: LeaseSnapshot,
    request: LeaseRenewRequest,
    *,
    heartbeat_timeout_ms: int,
    updated_at: int,
) -> LeaseSnapshot:
    """Renew an active lease after validating its full fence."""

    if current.state not in (
        LeaseState.ACTIVE,
        LeaseState.RENEWED,
    ):
        raise InvalidMutationError(
            f"cannot renew lease in state {current.state.value}"
        )
    if request.fence.revision != current.fence.revision:
        raise StaleRevisionError(
            current.lease_id,
            request.fence.revision,
            current.fence.revision,
        )

    is_match, mismatches = validate_lease_fence(
        current.fence,
        request.fence,
    )
    if not is_match:
        raise InvalidMutationError(
            f"lease fence mismatch on fields: {', '.join(mismatches)}"
        )

    return replace(
        current,
        fence=replace(
            current.fence,
            fencing_token=current.fence.fencing_token + 1,
            revision=current.fence.revision + 1,
        ),
        state=LeaseState.RENEWED,
        heartbeat_expires_at=(
            updated_at + heartbeat_timeout_ms
        ),
        updated_at=updated_at,
    )


def close_lease(
    current: LeaseSnapshot,
    request: LeaseCloseRequest,
    *,
    updated_at: int,
) -> LeaseSnapshot:
    """Close a lease after validating its full fence."""

    if current.state in (
        LeaseState.RELEASED,
        LeaseState.FENCED,
    ):
        raise InvalidMutationError(
            f"cannot close lease in state {current.state.value}"
        )
    if request.fence.revision != current.fence.revision:
        raise StaleRevisionError(
            current.lease_id,
            request.fence.revision,
            current.fence.revision,
        )

    is_match, mismatches = validate_lease_fence(
        current.fence,
        request.fence,
    )
    if not is_match:
        raise InvalidMutationError(
            f"lease fence mismatch on fields: {', '.join(mismatches)}"
        )

    return replace(
        current,
        fence=replace(
            current.fence,
            fencing_token=current.fence.fencing_token + 1,
            revision=current.fence.revision + 1,
        ),
        state=LeaseState.RELEASED,
        updated_at=updated_at,
    )


def expire_and_recover_lease(
    current: LeaseSnapshot,
    *,
    recovery_receipt_id: str,
    recovery_actor_principal_id: str,
    trigger: RecoveryTrigger,
    evidence_digest: str,
    policy_id: str,
    policy_revision: int,
    detected_at: int,
    is_process_alive: bool,
    process_identity_matches: bool,
) -> tuple[LeaseSnapshot, RecoveryReceipt]:
    """Fence a lease and derive a recovery receipt."""

    pre_state = current.state
    pre_revision = current.fence.revision
    pre_fencing_token = current.fence.fencing_token

    mismatches: list[str] = []
    effect_certainty: ExecutionCertainty | None

    if current.fence.owner_process_birth_identity is None:
        # DP-06: a lease reserved but never reaching a process-identity-
        # bearing state (record_start_uncertain() leaves it RESERVED with
        # no identity) has no birth identity to mismatch against -- this is
        # the ABANDONED_PRE_SPAWN case, distinct from IDENTITY_MISMATCH
        # (which requires an identity that was recorded and then disagreed
        # with). No automatic replay is authorized; MAY_HAVE_STARTED per
        # DP-06's classification.
        post_state = LeaseState.ABANDONED_PRE_SPAWN
        decision = RecoveryDecision.MARK_INTERRUPTED
        certainty_after = (
            LeaseAuthorityCertainty.FENCED_FOR_FUTURE_WRITES
        )
        effect_certainty = ExecutionCertainty.MAY_HAVE_STARTED
    elif not process_identity_matches:
        mismatches.append("owner_process_birth_identity")
        post_state = LeaseState.IDENTITY_MISMATCH
        decision = RecoveryDecision.REJECT_AND_QUARANTINE
        certainty_after = (
            LeaseAuthorityCertainty.PRIOR_HOLDER_UNVERIFIED
        )
        effect_certainty = None
    elif is_process_alive:
        post_state = LeaseState.FENCED
        decision = RecoveryDecision.FENCE_AND_CLOSE
        certainty_after = (
            LeaseAuthorityCertainty.FENCED_FOR_FUTURE_WRITES
        )
        effect_certainty = ExecutionCertainty.MAY_HAVE_STARTED
    else:
        post_state = LeaseState.FENCED
        decision = RecoveryDecision.MARK_INTERRUPTED
        certainty_after = (
            LeaseAuthorityCertainty.FENCED_FOR_FUTURE_WRITES
        )
        effect_certainty = ExecutionCertainty.TERMINAL

    updated_lease = replace(
        current,
        fence=replace(
            current.fence,
            fencing_token=pre_fencing_token + 1,
            revision=pre_revision + 1,
        ),
        state=post_state,
        updated_at=detected_at,
    )

    receipt = RecoveryReceipt(
        recovery_receipt_id=recovery_receipt_id,
        session_id=current.session_id,
        lease_id=current.lease_id,
        detected_at=detected_at,
        recovery_actor_principal_id=(
            recovery_actor_principal_id
        ),
        trigger=trigger,
        mismatch_dimensions=tuple(mismatches),
        evidence_digest=evidence_digest,
        policy_id=policy_id,
        policy_revision=policy_revision,
        decision=decision,
        certainty_before_policy=(
            LeaseAuthorityCertainty.PRIOR_HOLDER_UNVERIFIED
        ),
        certainty_after_policy=certainty_after,
        external_effect_certainty=effect_certainty,
        pre_lifecycle_state=pre_state,
        pre_revision=pre_revision,
        pre_fencing_token=pre_fencing_token,
        post_lifecycle_state=post_state,
        post_revision=pre_revision + 1,
        post_fencing_token=pre_fencing_token + 1,
    )
    return (updated_lease, receipt)


def create_session_binding(
    key: SessionBindingKey,
    *,
    session_id: str,
    current_lease_id: str | None,
    adapter_fingerprint: str,
    readiness_binding: str,
    session_generation: int,
    created_at: int,
) -> SessionBindingSnapshot:
    """Create a new revision-one ACTIVE session binding."""

    return SessionBindingSnapshot(
        key=key,
        session_id=session_id,
        current_lease_id=current_lease_id,
        adapter_fingerprint=adapter_fingerprint,
        readiness_binding=readiness_binding,
        session_generation=session_generation,
        revision=1,
        state=SessionBindingState.ACTIVE,
        updated_at=created_at,
    )


def resume_session_binding(
    current: SessionBindingSnapshot,
    request: SessionResumeRequest,
    *,
    updated_at: int,
) -> tuple[bool, SessionBindingSnapshot]:
    """Validate compatible resume or mark the binding STALE."""

    if current.key != request.key:
        raise InvalidMutationError(
            "session binding key mismatch"
        )

    is_compatible = (
        current.session_id == request.requested_session_id
        and current.adapter_fingerprint
        == request.adapter_fingerprint
        and current.readiness_binding
        == request.readiness_binding
        and current.session_generation
        == request.session_generation
        and current.state is SessionBindingState.ACTIVE
    )
    if is_compatible:
        return (True, current)

    return (
        False,
        replace(
            current,
            revision=current.revision + 1,
            state=SessionBindingState.STALE,
            updated_at=updated_at,
        ),
    )


def mark_session_suspect(
    current: SessionBindingSnapshot,
    *,
    updated_at: int,
) -> SessionBindingSnapshot:
    """Transition a session binding to SUSPECT."""

    return replace(
        current,
        revision=current.revision + 1,
        state=SessionBindingState.SUSPECT,
        updated_at=updated_at,
    )


_TERMINAL_ROWS: dict[TerminalClassification, ErrorCode] = {
    TerminalClassification.START_UNCERTAIN: ErrorCode.START_UNCERTAIN,
    TerminalClassification.SILENCE_TIMEOUT: ErrorCode.SILENCE_TIMEOUT,
    TerminalClassification.PROCESS_TIMEOUT: ErrorCode.PROCESS_TIMEOUT,
    TerminalClassification.EXIT_NON_ZERO: ErrorCode.INTERNAL_ERROR,
    TerminalClassification.OUTPUT_LIMIT_EXCEEDED: ErrorCode.PROCESS_KILLED,
}

_OPERATIONAL_KINDS: dict[str, OperationalFailureCategory] = {
    "auth_unavailable": OperationalFailureCategory.AUTH_UNAVAILABLE,
    "network_unavailable": OperationalFailureCategory.NETWORK_UNAVAILABLE,
    "provider_unavailable": OperationalFailureCategory.PROVIDER_UNAVAILABLE,
    "quota_exhausted": OperationalFailureCategory.QUOTA_EXHAUSTED,
    "rate_limited": OperationalFailureCategory.RATE_LIMITED,
}

_EVIDENCE_SOURCES = {
    "structured_vendor_output",
    "known_terminal_pattern",
}


def _normalized_vendor_kind(decoded_output: DecodedOutput | None) -> str | None:
    if decoded_output is None:
        return None
    for event in decoded_output.events:
        if event.kind is not DecoderEventKind.VENDOR_ERROR:
            continue
        if event.payload.get("evidence_source") not in _EVIDENCE_SOURCES:
            continue
        kind = event.payload.get("normalized_kind")
        if isinstance(kind, str):
            return kind
    return None


def classify_attempt_failure(
    *,
    terminal_classification: TerminalClassification | None,
    execution: ExecutionOutcome,
    protocol: ProtocolAssessment,
    decoded_output: DecodedOutput | None,
) -> AttemptFailureClassification | None:
    """Map one attempt's measured evidence without deciding retry policy."""

    _ = execution  # Retained for signature fidelity and later correlation checks.
    if terminal_classification is None:
        if protocol.protocol_failure is None:
            return None
        return AttemptFailureClassification(
            protocol.protocol_failure, ErrorPhase.ASSESSMENT, None
        )

    code: ErrorCode = _TERMINAL_ROWS[terminal_classification]
    category = None
    if terminal_classification is TerminalClassification.EXIT_NON_ZERO:
        vendor_kind = _normalized_vendor_kind(decoded_output)
        if vendor_kind == "session_invalid":
            code = ErrorCode.SESSION_INVALID
        elif vendor_kind == "invocation_plan_rejected":
            code = ErrorCode.INVOCATION_PLAN_REJECTED
        else:
            category = _OPERATIONAL_KINDS.get(vendor_kind or "")

    return AttemptFailureClassification(code, ErrorPhase.POST_SPAWN, category)
