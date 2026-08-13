"""Pure disposition mapping for retry loop."""

from peerhub.core.protocol import ErrorCode, ErrorPhase, OperationalFailureCategory, RetryDisposition
from peerhub.dispatch.contract import AttemptFailureClassification, TerminalClassification


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
