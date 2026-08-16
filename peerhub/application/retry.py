"""Pure retry policy types and disposition mapping."""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
from typing import TYPE_CHECKING, Protocol

from peerhub.adapters.contract import SessionAction
from peerhub.core.errors import (
    AttemptLimitReachedError,
    CapabilityAuthorizationDeniedError,
    InvalidMutationError,
    InvalidStateTransitionError,
    PolicyStaleError,
    RecordNotFoundError,
    RetryPolicyConflictError,
    RetryRouteUnavailableError,
    RouteExhaustedError,
    StaleRevisionError,
)
from peerhub.core.protocol import (
    CommandID,
    ErrorCode,
    ErrorPhase,
    OperationalFailureCategory,
    RetryDisposition,
    require_text,
)
from peerhub.dispatch.capability import CapabilityLeaseViolation
from peerhub.dispatch.contract import (
    AttemptFailureClassification,
    RequestState,
    TERMINAL_REQUEST_STATES,
    TerminalClassification,
)
from peerhub.dispatch.retry_authorization import RetryAuthorizationBundle
from peerhub.core.execution import ExecutionCertainty
from peerhub.routing.contract import canonical_route_decision_digest

if TYPE_CHECKING:
    from peerhub.adapters.contract import (
        AdapterRequest,
        PeerAdapter,
        ProfileDescriptor,
        SessionHint,
    )
    from peerhub.application.workflows import (
        ExecutionWorkflowResult,
        RetryWorkflowResult,
    )
    from peerhub.core.protocol import ErrorDetail
    from peerhub.dispatch.contract import AttemptSnapshot, RetryLoopState


class RetryAction(str, Enum):
    """Action selected after adjudicating one attempt."""

    STOP = "STOP"
    RETRY_SAME_TARGET = "RETRY_SAME_TARGET"
    FAILOVER = "FAILOVER"
    DEFER = "DEFER"


class RetryDecisionReason(str, Enum):
    """Stable reason vocabulary for a retry decision."""

    VERIFIED_SUCCESS = "VERIFIED_SUCCESS"
    DELIVERED_UNVERIFIED = "DELIVERED_UNVERIFIED"
    AUTHORITATIVE_CANCELLATION = "AUTHORITATIVE_CANCELLATION"
    LEGACY_CLASSIFICATION_UNKNOWN = "LEGACY_CLASSIFICATION_UNKNOWN"
    UNSAFE_NO_EVIDENCE = "UNSAFE_NO_EVIDENCE"
    NEVER_DISPOSITION = "NEVER_DISPOSITION"
    CONDITION_MET = "CONDITION_MET"
    CONDITION_UNMET = "CONDITION_UNMET"
    EXECUTION_NOT_STARTED = "EXECUTION_NOT_STARTED"
    RECONCILIATION_COMPLETE = "RECONCILIATION_COMPLETE"
    REPLAY_SAFE_COMPLETION_CONTRACT = "REPLAY_SAFE_COMPLETION_CONTRACT"
    ATTEMPT_LIMIT_REACHED = "ATTEMPT_LIMIT_REACHED"
    ROUTE_EXHAUSTED = "ROUTE_EXHAUSTED"
    CONCURRENT_TERMINAL_STATE = "CONCURRENT_TERMINAL_STATE"
    CONCURRENT_ATTEMPT_IN_PROGRESS = "CONCURRENT_ATTEMPT_IN_PROGRESS"
    FAILOVER_UNAVAILABLE = "FAILOVER_UNAVAILABLE"


class RetryCondition(str, Enum):
    """Evidence condition required by one current CONDITIONAL row.

    The names describe measured states. They do not authorize the retry loop
    to perform session, authentication, network, provider, or quota repair.
    """

    SESSION_REPLACED_OR_REMOVED = "SESSION_REPLACED_OR_REMOVED"
    AUTH_RESTORED = "AUTH_RESTORED"
    NETWORK_RECOVERED = "NETWORK_RECOVERED"
    PROVIDER_RECOVERED = "PROVIDER_RECOVERED"
    QUOTA_AVAILABLE = "QUOTA_AVAILABLE"
    RATE_LIMIT_BOUNDARY_ELAPSED = "RATE_LIMIT_BOUNDARY_ELAPSED"


class RetryLoopStopReason(str, Enum):
    """Why the outer loop returned instead of dispatching another attempt."""

    VERIFIED_SUCCESS = "VERIFIED_SUCCESS"
    DELIVERED_UNVERIFIED = "DELIVERED_UNVERIFIED"
    AUTHORITATIVE_CANCELLATION = "AUTHORITATIVE_CANCELLATION"
    NEVER_DISPOSITION = "NEVER_DISPOSITION"
    UNSAFE_NO_EVIDENCE = "UNSAFE_NO_EVIDENCE"
    CONDITION_DEFERRED = "CONDITION_DEFERRED"
    ATTEMPT_LIMIT_REACHED = "ATTEMPT_LIMIT_REACHED"
    ROUTE_EXHAUSTED = "ROUTE_EXHAUSTED"
    CONCURRENT_TERMINAL_STATE = "CONCURRENT_TERMINAL_STATE"
    CONCURRENT_ATTEMPT_IN_PROGRESS = "CONCURRENT_ATTEMPT_IN_PROGRESS"
    FAILOVER_UNAVAILABLE = "FAILOVER_UNAVAILABLE"
    LEGACY_CLASSIFICATION_UNKNOWN = "LEGACY_CLASSIFICATION_UNKNOWN"
    AUTHORIZATION_DENIED = "AUTHORIZATION_DENIED"


class ConcurrentClaimOutcome(str, Enum):
    """Outcomes from the pure concurrent claim classifier."""
    
    TERMINAL_STATE = "CONCURRENT_TERMINAL_STATE"
    ATTEMPT_IN_PROGRESS = "CONCURRENT_ATTEMPT_IN_PROGRESS"
    ATTEMPT_TERMINAL_REBUILD = "CONCURRENT_ATTEMPT_TERMINAL_REBUILD"
    NO_ADVANCEMENT_READJUDICATE = "CONCURRENT_NO_ADVANCEMENT_READJUDICATE"


MAX_CONCURRENT_READJUDICATE_SPIN_LIMIT = 3


@dataclass(frozen=True)
class ConcurrentClaimResolution:
    """Resolution of a concurrent attempt claim or stale revision."""

    outcome: ConcurrentClaimOutcome
    rebuild_attempt_number: int | None = None
    readjudicate_retry_limit: int | None = None

    def __post_init__(self) -> None:
        if self.outcome is ConcurrentClaimOutcome.ATTEMPT_TERMINAL_REBUILD and self.rebuild_attempt_number is None:
            raise ValueError("ATTEMPT_TERMINAL_REBUILD requires rebuild_attempt_number")
        if self.outcome is ConcurrentClaimOutcome.NO_ADVANCEMENT_READJUDICATE and self.readjudicate_retry_limit is None:
            raise ValueError("NO_ADVANCEMENT_READJUDICATE requires readjudicate_retry_limit")


class AuthorizationErrorSignal(str, Enum):
    """Nonterminal control signals emitted by the 5B error boundary."""

    RELOAD_DURABLE_STATE = "RELOAD_DURABLE_STATE"
    PREPARE_FAILOVER = "PREPARE_FAILOVER"


def _require_nonnegative_optional_int(value: int | None, name: str) -> None:
    if value is not None and (type(value) is not int or value < 0):
        raise ValueError(f"{name} must be a nonnegative integer or null")


@dataclass(frozen=True)
class RetryConditionEvidence:
    """Authoritative observation that one retry condition is met or unmet."""

    condition: RetryCondition
    satisfied: bool
    evidence_source: str
    observed_at: int
    not_before: int | None

    def __post_init__(self) -> None:
        if not isinstance(self.condition, RetryCondition):  # pyright: ignore[reportUnnecessaryIsInstance]
            raise ValueError("condition must be RetryCondition")
        if type(self.satisfied) is not bool:
            raise ValueError("satisfied must be a boolean")
        object.__setattr__(
            self,
            "evidence_source",
            require_text(self.evidence_source, "evidence_source"),
        )
        if type(self.observed_at) is not int or self.observed_at < 0:
            raise ValueError("observed_at must be a nonnegative integer")
        _require_nonnegative_optional_int(self.not_before, "not_before")


class RetryConditionEvidenceProvider(Protocol):
    """Fetch fresh authoritative condition evidence after a durable reload."""

    def __call__(
        self,
        *,
        latest_attempt: AttemptSnapshot,
        condition: RetryCondition,
    ) -> RetryConditionEvidence | None: ...


@dataclass(frozen=True)
class RetryConditionResolution:
    """Whether fresh evidence permits adjudication or requires deferral."""

    evidence_for_adjudication: RetryConditionEvidence | None
    stop_reason: RetryLoopStopReason | None
    not_before: int | None


@dataclass(frozen=True)
class RetryDecision:
    disposition: RetryDisposition | None
    action: RetryAction
    reason: RetryDecisionReason
    required_conditions: tuple[RetryCondition, ...]
    not_before: int | None

    def __post_init__(self) -> None:
        if self.disposition is not None and not isinstance(  # pyright: ignore[reportUnnecessaryIsInstance]
            self.disposition,
            RetryDisposition,
        ):
            raise ValueError("disposition must be RetryDisposition or null")
        if not isinstance(self.action, RetryAction):  # pyright: ignore[reportUnnecessaryIsInstance]
            raise ValueError("action must be RetryAction")
        if not isinstance(self.reason, RetryDecisionReason):  # pyright: ignore[reportUnnecessaryIsInstance]
            raise ValueError("reason must be RetryDecisionReason")

        conditions = tuple(self.required_conditions)
        if any(
            not isinstance(condition, RetryCondition)  # pyright: ignore[reportUnnecessaryIsInstance]
            for condition in conditions
        ):
            raise ValueError("required_conditions must contain RetryCondition values")
        if len(set(conditions)) != len(conditions):
            raise ValueError("required_conditions must not contain duplicates")
        object.__setattr__(self, "required_conditions", conditions)

        if self.disposition is RetryDisposition.CONDITIONAL:
            if not conditions:
                raise ValueError(
                    "CONDITIONAL disposition requires at least one condition"
                )
        elif conditions:
            raise ValueError(
                "required_conditions must be empty unless disposition is CONDITIONAL"
            )

        if self.disposition is None and self.action is not RetryAction.STOP:
            raise ValueError("an action other than STOP requires a disposition")

        _require_nonnegative_optional_int(self.not_before, "not_before")
        if self.not_before is not None and self.action is not RetryAction.DEFER:
            raise ValueError("not_before is valid only for a DEFER action")


@dataclass(frozen=True)
class AttemptExecutionRecord:
    execution: ExecutionWorkflowResult
    error_detail: ErrorDetail | None
    retry_decision: RetryDecision
    retry_authorization: RetryWorkflowResult | None


@dataclass(frozen=True)
class MultiAttemptExecutionResult:
    command_id: CommandID
    attempts: tuple[AttemptExecutionRecord, ...]
    stop_reason: RetryLoopStopReason

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "command_id",
            CommandID(require_text(str(self.command_id), "command_id")),
        )
        attempts = tuple(self.attempts)
        if not attempts:
            raise ValueError("attempts must contain at least one attempt")
        if any(
            not isinstance(attempt, AttemptExecutionRecord)  # pyright: ignore[reportUnnecessaryIsInstance]
            for attempt in attempts
        ):
            raise ValueError("attempts must contain AttemptExecutionRecord values")
        object.__setattr__(self, "attempts", attempts)
        if not isinstance(self.stop_reason, RetryLoopStopReason):  # pyright: ignore[reportUnnecessaryIsInstance]
            raise ValueError("stop_reason must be RetryLoopStopReason")


@dataclass(frozen=True)
class AttemptDispatchPlan:
    route_decision_id: str
    capability_lease_id: str
    peer_instance_id: str
    adapter_request: AdapterRequest
    peer_adapter: PeerAdapter
    profile: ProfileDescriptor
    session: SessionHint | None

    def __post_init__(self) -> None:
        for name in (
            "route_decision_id",
            "capability_lease_id",
            "peer_instance_id",
        ):
            object.__setattr__(
                self,
                name,
                require_text(getattr(self, name), name),
            )
        if self.adapter_request.profile_id != self.profile.profile_id:
            raise ValueError(
                "adapter request profile_id must match the dispatch profile"
            )
        session_action = self.adapter_request.requested_session_action
        if session_action is SessionAction.RESUME and self.session is None:
            raise ValueError("SessionAction.RESUME requires a session")
        if session_action is not SessionAction.RESUME and self.session is not None:
            raise ValueError("a session requires SessionAction.RESUME")


@dataclass(frozen=True)
class RetryRouteTransition:
    """One bounded next step for retry-route authorization."""

    decision: RetryDecision
    next_action: RetryAction | None
    reload_required: bool

    def __post_init__(self) -> None:
        if self.reload_required:
            if self.next_action is not None:
                raise ValueError(
                    "a durable reload cannot also authorize a route action"
                )
        elif self.next_action not in {
            RetryAction.RETRY_SAME_TARGET,
            RetryAction.FAILOVER,
        }:
            raise ValueError(
                "a route transition must choose same-target or failover"
            )


@dataclass(frozen=True)
class ResolvedRetryTarget:
    """Adapter and profile resolved from machine-owned failover identity."""

    peer_adapter: PeerAdapter
    profile: ProfileDescriptor


class RetryTargetResolver(Protocol):
    """Resolve one replacement target without assuming instance equals kind."""

    def __call__(
        self,
        peer_kind: str,
        instance_id: str,
        profile_id: str,
    ) -> ResolvedRetryTarget | None: ...


def classify_authorization_error(
    error: BaseException,
) -> RetryLoopStopReason | AuthorizationErrorSignal:
    """Classify only the ratified 5B control outcomes; propagate everything else."""

    if isinstance(error, StaleRevisionError):
        return AuthorizationErrorSignal.RELOAD_DURABLE_STATE
    if isinstance(error, AttemptLimitReachedError):
        return RetryLoopStopReason.ATTEMPT_LIMIT_REACHED
    if isinstance(error, RouteExhaustedError):
        return RetryLoopStopReason.ROUTE_EXHAUSTED
    if isinstance(error, CapabilityAuthorizationDeniedError):
        return RetryLoopStopReason.AUTHORIZATION_DENIED
    if isinstance(error, RetryRouteUnavailableError):
        return AuthorizationErrorSignal.PREPARE_FAILOVER
    if isinstance(
        error,
        (
            RetryPolicyConflictError,
            PolicyStaleError,
            RecordNotFoundError,
            CapabilityLeaseViolation,
            InvalidStateTransitionError,
            InvalidMutationError,
        ),
    ):
        raise error
    raise error


def transition_retry_route(
    decision: RetryDecision,
    *,
    attempted_action: RetryAction | None = None,
    error: BaseException | None = None,
) -> RetryRouteTransition:
    """Select same-target, one failover, or a durable reload without spinning."""

    if attempted_action is None:
        if error is not None:
            raise InvalidMutationError(
                "an initial retry route transition cannot carry an error"
            )
        if decision.action is not RetryAction.RETRY_SAME_TARGET:
            raise InvalidMutationError(
                "retry route authorization must begin with RETRY_SAME_TARGET"
            )
        return RetryRouteTransition(
            decision=decision,
            next_action=RetryAction.RETRY_SAME_TARGET,
            reload_required=False,
        )

    if error is None:
        raise InvalidMutationError(
            "a completed route attempt requires an error transition"
        )
    if isinstance(error, StaleRevisionError):
        return RetryRouteTransition(
            decision=decision,
            next_action=None,
            reload_required=True,
        )
    if isinstance(error, RetryRouteUnavailableError):
        if (
            attempted_action is not RetryAction.RETRY_SAME_TARGET
            or decision.action is not RetryAction.RETRY_SAME_TARGET
        ):
            raise InvalidMutationError(
                "retry routing permits exactly one failover transition"
            )
        failover_decision = replace(
            decision,
            action=RetryAction.FAILOVER,
        )
        return RetryRouteTransition(
            decision=failover_decision,
            next_action=RetryAction.FAILOVER,
            reload_required=False,
        )
    raise error


def build_retry_dispatch_plan(
    *,
    bundle: RetryAuthorizationBundle,
    route_action: RetryAction,
    current_plan: AttemptDispatchPlan,
    resolver: RetryTargetResolver,
) -> AttemptDispatchPlan | RetryLoopStopReason:
    """Build the next plan only from one cross-checked atomic authority bundle."""

    request = bundle.request
    capability = bundle.capability_lease
    route_decision = bundle.route_decision
    route_digest = canonical_route_decision_digest(route_decision)
    selected = tuple(
        candidate
        for candidate in route_decision.candidates
        if candidate.candidate_id == route_decision.selected_candidate_id
    )
    bundle_matches = (
        len(selected) == 1
        and request.command_id == capability.command_id
        and request.lease_id == bundle.session_lease.lease_id
        and capability.session_lease_id == bundle.session_lease.lease_id
        and request.client_request_id == route_decision.client_request_id
        and request.route_decision_digest == route_digest
        and capability.route_decision_digest == route_digest
        and request.selected_peer_instance_id == selected[0].instance_id
        and capability.selected_peer_instance_id == selected[0].instance_id
        and request.selected_profile_id
        == selected[0].representative_profile_id
        and capability.selected_profile_id
        == selected[0].representative_profile_id
    )
    if not bundle_matches:
        raise CapabilityLeaseViolation(
            "retry authorization bundle target binding must agree"
        )

    if route_action is RetryAction.RETRY_SAME_TARGET:
        if (
            current_plan.peer_instance_id
            != request.selected_peer_instance_id
            or current_plan.profile.profile_id
            != request.selected_profile_id
            or current_plan.peer_adapter.descriptor.peer_kind
            != capability.selected_peer_kind
        ):
            raise CapabilityLeaseViolation(
                "same-target retry must retain the bound adapter target"
            )
        return AttemptDispatchPlan(
            route_decision_id=route_decision.decision_id,
            capability_lease_id=capability.capability_lease_id,
            peer_instance_id=request.selected_peer_instance_id,
            adapter_request=current_plan.adapter_request,
            peer_adapter=current_plan.peer_adapter,
            profile=current_plan.profile,
            session=current_plan.session,
        )

    if route_action is not RetryAction.FAILOVER:
        raise InvalidMutationError(
            "retry plan construction requires same-target or failover"
        )
    if request.selected_peer_instance_id == current_plan.peer_instance_id:
        raise CapabilityLeaseViolation(
            "failover retry must select a replacement peer instance"
        )

    resolved = resolver(
        capability.selected_peer_kind,
        request.selected_peer_instance_id,
        request.selected_profile_id,
    )
    if resolved is None:
        return RetryLoopStopReason.FAILOVER_UNAVAILABLE
    if (
        resolved.peer_adapter.descriptor.peer_kind
        != capability.selected_peer_kind
        or resolved.profile.profile_id != request.selected_profile_id
        or not any(
            profile.profile_id == resolved.profile.profile_id
            for profile in resolved.peer_adapter.descriptor.profiles
        )
    ):
        raise CapabilityLeaseViolation(
            "resolved failover target must match committed capability identity"
        )

    adapter_request = replace(
        current_plan.adapter_request,
        profile_id=resolved.profile.profile_id,
        requested_session_action=SessionAction.NONE,
    )
    return AttemptDispatchPlan(
        route_decision_id=route_decision.decision_id,
        capability_lease_id=capability.capability_lease_id,
        peer_instance_id=request.selected_peer_instance_id,
        adapter_request=adapter_request,
        peer_adapter=resolved.peer_adapter,
        profile=resolved.profile,
        session=None,
    )


def read_fresh_retry_condition_evidence(
    provider: RetryConditionEvidenceProvider,
    *,
    latest_attempt: AttemptSnapshot,
    condition: RetryCondition,
) -> RetryConditionEvidence | None:
    """Call the provider every time; callers must invoke this after each reload."""

    return provider(
        latest_attempt=latest_attempt,
        condition=condition,
    )


def evaluate_retry_condition_evidence(
    condition: RetryCondition,
    evidence: RetryConditionEvidence | None,
) -> RetryConditionResolution:
    """Fail closed on absent evidence and on session remediation in 5C."""

    if condition is RetryCondition.SESSION_REPLACED_OR_REMOVED:
        return RetryConditionResolution(
            evidence_for_adjudication=None,
            stop_reason=RetryLoopStopReason.CONDITION_DEFERRED,
            not_before=None,
        )
    if (
        evidence is None
        or evidence.condition is not condition
        or not evidence.satisfied
    ):
        return RetryConditionResolution(
            evidence_for_adjudication=None,
            stop_reason=RetryLoopStopReason.CONDITION_DEFERRED,
            not_before=(
                evidence.not_before
                if evidence is not None
                and evidence.condition is condition
                else None
            ),
        )
    return RetryConditionResolution(
        evidence_for_adjudication=evidence,
        stop_reason=None,
        not_before=None,
    )


def map_retry_disposition(
    failure: AttemptFailureClassification,
    *,
    terminal_classification: TerminalClassification | None,
) -> RetryDisposition:
    """Map an attempt failure and terminal classification to a retry disposition.

    The caller must ensure that a failure actually occurred (e.g., checking
    that `failure` is not None) before calling this function.
    """
    key = (terminal_classification, failure.code, failure.operational_failure_category, failure.phase)

    match key:
        case (None, _, None, ErrorPhase.ASSESSMENT):
            return RetryDisposition.UNSAFE
        case (TerminalClassification.START_UNCERTAIN, ErrorCode.START_UNCERTAIN, None, ErrorPhase.POST_SPAWN):
            return RetryDisposition.UNSAFE
        case (TerminalClassification.SILENCE_TIMEOUT, ErrorCode.SILENCE_TIMEOUT, None, ErrorPhase.POST_SPAWN):
            return RetryDisposition.UNSAFE
        case (TerminalClassification.PROCESS_TIMEOUT, ErrorCode.PROCESS_TIMEOUT, None, ErrorPhase.POST_SPAWN):
            return RetryDisposition.UNSAFE
        case (TerminalClassification.EXIT_NON_ZERO, ErrorCode.INTERNAL_ERROR, None, ErrorPhase.POST_SPAWN):
            return RetryDisposition.UNSAFE
        case (TerminalClassification.EXIT_NON_ZERO, ErrorCode.SESSION_INVALID, None, ErrorPhase.POST_SPAWN):
            return RetryDisposition.CONDITIONAL
        case (TerminalClassification.EXIT_NON_ZERO, ErrorCode.INVOCATION_PLAN_REJECTED, None, ErrorPhase.POST_SPAWN):
            return RetryDisposition.NEVER
        case (
            TerminalClassification.EXIT_NON_ZERO,
            ErrorCode.INTERNAL_ERROR,
            OperationalFailureCategory.AUTH_UNAVAILABLE,
            ErrorPhase.POST_SPAWN,
        ):
            return RetryDisposition.CONDITIONAL
        case (
            TerminalClassification.EXIT_NON_ZERO,
            ErrorCode.INTERNAL_ERROR,
            OperationalFailureCategory.NETWORK_UNAVAILABLE,
            ErrorPhase.POST_SPAWN,
        ):
            return RetryDisposition.CONDITIONAL
        case (
            TerminalClassification.EXIT_NON_ZERO,
            ErrorCode.INTERNAL_ERROR,
            OperationalFailureCategory.PROVIDER_UNAVAILABLE,
            ErrorPhase.POST_SPAWN,
        ):
            return RetryDisposition.CONDITIONAL
        case (
            TerminalClassification.EXIT_NON_ZERO,
            ErrorCode.INTERNAL_ERROR,
            OperationalFailureCategory.QUOTA_EXHAUSTED,
            ErrorPhase.POST_SPAWN,
        ):
            return RetryDisposition.CONDITIONAL
        case (
            TerminalClassification.EXIT_NON_ZERO,
            ErrorCode.INTERNAL_ERROR,
            OperationalFailureCategory.RATE_LIMITED,
            ErrorPhase.POST_SPAWN,
        ):
            return RetryDisposition.CONDITIONAL
        case (TerminalClassification.OUTPUT_LIMIT_EXCEEDED, ErrorCode.PROCESS_KILLED, None, ErrorPhase.POST_SPAWN):
            return RetryDisposition.NEVER
        case _:
            raise ValueError(f"Unhandled failure combination: {key}")


@dataclass(frozen=True)
class RetryPolicyRecord:
    command_id: CommandID
    max_attempts: int

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "command_id",
            CommandID(require_text(str(self.command_id), "command_id")),
        )
        if type(self.max_attempts) is not int or self.max_attempts < 1:
            raise ValueError("max_attempts must be an integer >= 1")


def validate_attempt_history(attempts: tuple[AttemptSnapshot, ...]) -> int:
    """Validate monotonic numbering and return the highest attempt_number.

    Gaps or duplicate numbers are invariant failures.
    """
    if not attempts:
        raise ValueError("attempts must contain at least one attempt")

    highest = 0
    for i, attempt in enumerate(attempts, start=1):
        if attempt.attempt_number != i:
            raise ValueError(
                f"Attempt history invalid: expected attempt_number {i}, "
                f"got {attempt.attempt_number}"
            )
        highest = attempt.attempt_number
    return highest


def _map_condition(failure: AttemptFailureClassification) -> RetryCondition:
    if failure.code == ErrorCode.SESSION_INVALID:
        return RetryCondition.SESSION_REPLACED_OR_REMOVED
    if failure.operational_failure_category == OperationalFailureCategory.AUTH_UNAVAILABLE:
        return RetryCondition.AUTH_RESTORED
    if failure.operational_failure_category == OperationalFailureCategory.NETWORK_UNAVAILABLE:
        return RetryCondition.NETWORK_RECOVERED
    if failure.operational_failure_category == OperationalFailureCategory.PROVIDER_UNAVAILABLE:
        return RetryCondition.PROVIDER_RECOVERED
    if failure.operational_failure_category == OperationalFailureCategory.QUOTA_EXHAUSTED:
        return RetryCondition.QUOTA_AVAILABLE
    if failure.operational_failure_category == OperationalFailureCategory.RATE_LIMITED:
        return RetryCondition.RATE_LIMIT_BOUNDARY_ELAPSED
    raise ValueError(f"No condition mapping for {failure}")


def adjudicate_retry(
    execution_result: ExecutionWorkflowResult,
    *,
    durable_attempt_number: int,
    max_attempts: int,
    reconciliation_complete: bool,
    condition_evidence: RetryConditionEvidence | None = None,
) -> RetryDecision:
    """Adjudicate retry disposition and conditions to determine the next loop action.

    `condition_evidence` may be None because not all failure dispositions require evidence
    (e.g., UNSAFE, NEVER, and STOP decisions are processed entirely without evidence).
    It is only an error to omit it if the failure maps to a CONDITIONAL disposition and
    you expect it to be retried (otherwise it will safely DEFER).
    """
    req_state = execution_result.request.state
    ask_result = execution_result.attempt.result

    if req_state is RequestState.SUCCEEDED_VERIFIED:
        return RetryDecision(
            disposition=None,
            action=RetryAction.STOP,
            reason=RetryDecisionReason.VERIFIED_SUCCESS,
            required_conditions=(),
            not_before=None,
        )
    if req_state is RequestState.DELIVERED_UNVERIFIED:
        return RetryDecision(
            disposition=None,
            action=RetryAction.STOP,
            reason=RetryDecisionReason.DELIVERED_UNVERIFIED,
            required_conditions=(),
            not_before=None,
        )
    if req_state is RequestState.CANCELLED:
        return RetryDecision(
            disposition=None,
            action=RetryAction.STOP,
            reason=RetryDecisionReason.AUTHORITATIVE_CANCELLATION,
            required_conditions=(),
            not_before=None,
        )

    failure_classification = ask_result.failure_classification if ask_result else None
    terminal_classification = ask_result.terminal_classification if ask_result else None

    disposition = None
    if failure_classification is not None:
        disposition = map_retry_disposition(
            failure_classification,
            terminal_classification=terminal_classification,
        )
    else:
        if req_state is RequestState.INCOMPLETE:
            disposition = RetryDisposition.UNSAFE
        elif req_state in (RequestState.FAILED_PRE_DISPATCH, RequestState.START_UNCERTAIN) and ask_result is None:
            attempt_term = execution_result.attempt.terminal_error_code
            req_term = execution_result.request.terminal_error_code
            # Safe to fallback to request-level code ONLY if this attempt hasn't produced its own code.
            # A request-level error might have blocked dispatch entirely (e.g. FAILED_PRE_DISPATCH),
            # meaning the attempt inherits the request's fatal reason.
            terminal_error_code = attempt_term or req_term

            # Deterministic-under-replay -> NEVER. Retrying with identical input will deterministically re-fail.
            if terminal_error_code in (
                ErrorCode.INVOCATION_PLAN_REJECTED,
                ErrorCode.ARTIFACT_IDENTITY_UNPROVABLE,
                ErrorCode.ARTIFACT_RESERVATION_FAILED,
                ErrorCode.IDEMPOTENCY_PAYLOAD_MISMATCH,
                ErrorCode.ACTOR_UNAUTHORIZED,
                ErrorCode.SCOPE_UNAUTHORIZED,
                ErrorCode.INVALID_PARAMS,
                ErrorCode.UNKNOWN_COMMAND,
                ErrorCode.SCHEMA_VERSION_UNSUPPORTED,
                ErrorCode.PROTOCOL_VERSION_MISMATCH,
                ErrorCode.ROUTE_EXHAUSTED,
                ErrorCode.WORKSPACE_IDENTITY_MISMATCH,
            ):
                disposition = RetryDisposition.NEVER
            else:
                disposition = RetryDisposition.UNSAFE
        else:
            return RetryDecision(
                disposition=None,
                action=RetryAction.STOP,
                reason=RetryDecisionReason.LEGACY_CLASSIFICATION_UNKNOWN,
                required_conditions=(),
                not_before=None,
            )

    if disposition is RetryDisposition.NEVER:
        return RetryDecision(
            disposition=disposition,
            action=RetryAction.STOP,
            reason=RetryDecisionReason.NEVER_DISPOSITION,
            required_conditions=(),
            not_before=None,
        )

    if durable_attempt_number + 1 > max_attempts:
        return RetryDecision(
            disposition=None,
            action=RetryAction.STOP,
            reason=RetryDecisionReason.ATTEMPT_LIMIT_REACHED,
            required_conditions=(),
            not_before=None,
        )
    elif disposition is RetryDisposition.UNSAFE:
        is_not_started = execution_result.attempt.execution_certainty is ExecutionCertainty.NOT_STARTED
        is_replay_safe = execution_result.request.completion_contract.replay_safe
        if not (is_not_started or reconciliation_complete or is_replay_safe):
            return RetryDecision(
                disposition=disposition,
                action=RetryAction.STOP,
                reason=RetryDecisionReason.UNSAFE_NO_EVIDENCE,
                required_conditions=(),
                not_before=None,
            )
        return RetryDecision(
            disposition=disposition,
            action=RetryAction.RETRY_SAME_TARGET,
            reason=RetryDecisionReason.EXECUTION_NOT_STARTED if is_not_started else
                   (RetryDecisionReason.RECONCILIATION_COMPLETE if reconciliation_complete else
                    RetryDecisionReason.REPLAY_SAFE_COMPLETION_CONTRACT),
            required_conditions=(),
            not_before=None,
        )
    elif disposition is RetryDisposition.CONDITIONAL:
        assert failure_classification is not None
        condition = _map_condition(failure_classification)

        is_not_started = execution_result.attempt.execution_certainty is ExecutionCertainty.NOT_STARTED
        is_replay_safe = execution_result.request.completion_contract.replay_safe
        if not (is_not_started or reconciliation_complete or is_replay_safe):
            return RetryDecision(
                disposition=disposition,
                action=RetryAction.STOP,
                reason=RetryDecisionReason.UNSAFE_NO_EVIDENCE,
                required_conditions=(condition,),
                not_before=None,
            )

        if condition_evidence and condition_evidence.condition == condition and condition_evidence.satisfied:
            return RetryDecision(
                disposition=disposition,
                action=RetryAction.RETRY_SAME_TARGET,
                reason=RetryDecisionReason.CONDITION_MET,
                required_conditions=(condition,),
                not_before=None,
            )
        else:
            return RetryDecision(
                disposition=disposition,
                action=RetryAction.DEFER,
                reason=RetryDecisionReason.CONDITION_UNMET,
                required_conditions=(condition,),
                not_before=condition_evidence.not_before if (condition_evidence and condition_evidence.condition == condition) else None,
            )
    else:
        raise ValueError(f"Unhandled disposition: {disposition}")


def classify_concurrent_claim(
    fresh_state: RetryLoopState,
    target_attempt_number: int,
) -> ConcurrentClaimResolution:
    """Classify the outcome of a concurrent claim or stale revision error."""

    # retryability depends on map_retry_disposition()'s NEVER/UNSAFE split,
    # already handled correctly by the ATTEMPT_TERMINAL_REBUILD branch below
    if fresh_state.request.state in (
        RequestState.CANCELLED,
        RequestState.SUCCEEDED_VERIFIED,
        RequestState.DELIVERED_UNVERIFIED,
    ):
        return ConcurrentClaimResolution(ConcurrentClaimOutcome.TERMINAL_STATE)

    highest_durable = validate_attempt_history(fresh_state.attempts) if fresh_state.attempts else 0
    authorized = fresh_state.current_capability.authorized_attempt_number

    if authorized == target_attempt_number and highest_durable < target_attempt_number:
        return ConcurrentClaimResolution(ConcurrentClaimOutcome.ATTEMPT_IN_PROGRESS)

    if highest_durable >= target_attempt_number:
        attempt = next(a for a in fresh_state.attempts if a.attempt_number == target_attempt_number)
        if attempt.state not in TERMINAL_REQUEST_STATES:
            return ConcurrentClaimResolution(ConcurrentClaimOutcome.ATTEMPT_IN_PROGRESS)
        else:
            return ConcurrentClaimResolution(
                outcome=ConcurrentClaimOutcome.ATTEMPT_TERMINAL_REBUILD,
                rebuild_attempt_number=target_attempt_number,
            )

    return ConcurrentClaimResolution(
        outcome=ConcurrentClaimOutcome.NO_ADVANCEMENT_READJUDICATE,
        readjudicate_retry_limit=MAX_CONCURRENT_READJUDICATE_SPIN_LIMIT,
    )
