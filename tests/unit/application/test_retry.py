import pytest

from peerhub.application.retry import map_retry_disposition
from peerhub.core.protocol import ErrorCode, ErrorPhase, OperationalFailureCategory, RetryDisposition
from peerhub.dispatch.contract import AttemptFailureClassification, TerminalClassification
from peerhub.dispatch.model import _OPERATIONAL_KINDS, _TERMINAL_ROWS


def test_map_retry_disposition_matrix() -> None:
    """Test every row in the explicit specification matrix."""
    # Row 1: START_UNCERTAIN
    assert map_retry_disposition(
        AttemptFailureClassification(ErrorCode.START_UNCERTAIN, ErrorPhase.POST_SPAWN, None),
        terminal_classification=TerminalClassification.START_UNCERTAIN,
    ) is RetryDisposition.UNSAFE

    # Row 2: SILENCE_TIMEOUT
    assert map_retry_disposition(
        AttemptFailureClassification(ErrorCode.SILENCE_TIMEOUT, ErrorPhase.POST_SPAWN, None),
        terminal_classification=TerminalClassification.SILENCE_TIMEOUT,
    ) is RetryDisposition.UNSAFE

    # Row 3: PROCESS_TIMEOUT
    assert map_retry_disposition(
        AttemptFailureClassification(ErrorCode.PROCESS_TIMEOUT, ErrorPhase.POST_SPAWN, None),
        terminal_classification=TerminalClassification.PROCESS_TIMEOUT,
    ) is RetryDisposition.UNSAFE

    # Row 4: EXIT_NON_ZERO, INTERNAL_ERROR, None
    assert map_retry_disposition(
        AttemptFailureClassification(ErrorCode.INTERNAL_ERROR, ErrorPhase.POST_SPAWN, None),
        terminal_classification=TerminalClassification.EXIT_NON_ZERO,
    ) is RetryDisposition.UNSAFE

    # Row 5: EXIT_NON_ZERO, SESSION_INVALID, None
    assert map_retry_disposition(
        AttemptFailureClassification(ErrorCode.SESSION_INVALID, ErrorPhase.POST_SPAWN, None),
        terminal_classification=TerminalClassification.EXIT_NON_ZERO,
    ) is RetryDisposition.CONDITIONAL

    # Row 6: EXIT_NON_ZERO, INVOCATION_PLAN_REJECTED, None
    assert map_retry_disposition(
        AttemptFailureClassification(ErrorCode.INVOCATION_PLAN_REJECTED, ErrorPhase.POST_SPAWN, None),
        terminal_classification=TerminalClassification.EXIT_NON_ZERO,
    ) is RetryDisposition.NEVER

    # Row 7: EXIT_NON_ZERO, INTERNAL_ERROR, AUTH_UNAVAILABLE
    assert map_retry_disposition(
        AttemptFailureClassification(ErrorCode.INTERNAL_ERROR, ErrorPhase.POST_SPAWN, OperationalFailureCategory.AUTH_UNAVAILABLE),
        terminal_classification=TerminalClassification.EXIT_NON_ZERO,
    ) is RetryDisposition.CONDITIONAL

    # Row 8: EXIT_NON_ZERO, INTERNAL_ERROR, NETWORK_UNAVAILABLE
    assert map_retry_disposition(
        AttemptFailureClassification(ErrorCode.INTERNAL_ERROR, ErrorPhase.POST_SPAWN, OperationalFailureCategory.NETWORK_UNAVAILABLE),
        terminal_classification=TerminalClassification.EXIT_NON_ZERO,
    ) is RetryDisposition.CONDITIONAL

    # Row 9: EXIT_NON_ZERO, INTERNAL_ERROR, PROVIDER_UNAVAILABLE
    assert map_retry_disposition(
        AttemptFailureClassification(ErrorCode.INTERNAL_ERROR, ErrorPhase.POST_SPAWN, OperationalFailureCategory.PROVIDER_UNAVAILABLE),
        terminal_classification=TerminalClassification.EXIT_NON_ZERO,
    ) is RetryDisposition.CONDITIONAL

    # Row 10: EXIT_NON_ZERO, INTERNAL_ERROR, QUOTA_EXHAUSTED
    assert map_retry_disposition(
        AttemptFailureClassification(ErrorCode.INTERNAL_ERROR, ErrorPhase.POST_SPAWN, OperationalFailureCategory.QUOTA_EXHAUSTED),
        terminal_classification=TerminalClassification.EXIT_NON_ZERO,
    ) is RetryDisposition.CONDITIONAL

    # Row 11: EXIT_NON_ZERO, INTERNAL_ERROR, RATE_LIMITED
    assert map_retry_disposition(
        AttemptFailureClassification(ErrorCode.INTERNAL_ERROR, ErrorPhase.POST_SPAWN, OperationalFailureCategory.RATE_LIMITED),
        terminal_classification=TerminalClassification.EXIT_NON_ZERO,
    ) is RetryDisposition.CONDITIONAL

    # Row 12: OUTPUT_LIMIT_EXCEEDED, PROCESS_KILLED, None
    assert map_retry_disposition(
        AttemptFailureClassification(ErrorCode.PROCESS_KILLED, ErrorPhase.POST_SPAWN, None),
        terminal_classification=TerminalClassification.OUTPUT_LIMIT_EXCEEDED,
    ) is RetryDisposition.NEVER

    # Row 13: None, (whatever code), None
    for code in ErrorCode:
        assert map_retry_disposition(
            AttemptFailureClassification(code, ErrorPhase.ASSESSMENT, None),
            terminal_classification=None,
        ) is RetryDisposition.UNSAFE


def _generate_reachable_failures(
    terminal_classification: TerminalClassification | None,
) -> list[AttemptFailureClassification]:
    """Simulate what classify_attempt_failure() can actually produce."""
    if terminal_classification is None:
        # Any error code is possible for a protocol failure
        return [AttemptFailureClassification(code, ErrorPhase.ASSESSMENT, None) for code in ErrorCode]

    base_code = _TERMINAL_ROWS[terminal_classification]

    if terminal_classification is TerminalClassification.EXIT_NON_ZERO:
        reachable = [
            AttemptFailureClassification(base_code, ErrorPhase.POST_SPAWN, None),
            AttemptFailureClassification(ErrorCode.SESSION_INVALID, ErrorPhase.POST_SPAWN, None),
            AttemptFailureClassification(ErrorCode.INVOCATION_PLAN_REJECTED, ErrorPhase.POST_SPAWN, None),
        ]
        # And any operational category returned by vendor mapping today
        for category in _OPERATIONAL_KINDS.values():
            reachable.append(AttemptFailureClassification(base_code, ErrorPhase.POST_SPAWN, category))
        return reachable
    else:
        return [AttemptFailureClassification(base_code, ErrorPhase.POST_SPAWN, None)]


def test_map_retry_disposition_is_total() -> None:
    """Test that all combinations classify_attempt_failure() can produce are handled."""
    terminals = list(TerminalClassification) + [None]

    for terminal in terminals:
        failures = _generate_reachable_failures(terminal)
        for failure in failures:
            # Should not raise any exception
            disposition = map_retry_disposition(failure, terminal_classification=terminal)
            assert isinstance(disposition, RetryDisposition)


def test_map_retry_disposition_invalid_inputs_raise() -> None:
    """Test that structurally impossible combinations fail loudly."""
    # (a) null terminal classification with non-null category
    with pytest.raises(ValueError):
        map_retry_disposition(
            AttemptFailureClassification(ErrorCode.INTERNAL_ERROR, ErrorPhase.ASSESSMENT, OperationalFailureCategory.NETWORK_UNAVAILABLE),
            terminal_classification=None,
        )

    # (b) invalid phases
    with pytest.raises(ValueError):
        map_retry_disposition(
            AttemptFailureClassification(ErrorCode.INTERNAL_ERROR, ErrorPhase.POST_SPAWN, None),
            terminal_classification=None,
        )

    with pytest.raises(ValueError):
        map_retry_disposition(
            AttemptFailureClassification(ErrorCode.INTERNAL_ERROR, ErrorPhase.ASSESSMENT, None),
            terminal_classification=TerminalClassification.EXIT_NON_ZERO,
        )
