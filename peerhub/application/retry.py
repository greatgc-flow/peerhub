"""Pure retry policy types and disposition mapping."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

from peerhub.core.protocol import (
    CommandID,
    ErrorCode,
    ErrorPhase,
    OperationalFailureCategory,
    RetryDisposition,
    require_text,
)
from peerhub.dispatch.contract import (
    AttemptFailureClassification,
    TerminalClassification,
)

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
