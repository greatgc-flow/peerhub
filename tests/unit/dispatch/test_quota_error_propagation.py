"""
Regression tests for the two-layer quota classification bug.

Layer 1 (fixed in 5053bcf): ClaudeOutputDecoder correctly identifies
rate_limit_event / api_error_status=429 as ``quota_exhausted`` VENDOR_ERROR.

Layer 2 (this fix): classify_attempt_failure emits ErrorCode.QUOTA_EXHAUSTED
(not INTERNAL_ERROR) for EXIT_NON_ZERO+quota_exhausted, and
_terminal_error_for_result propagates that code rather than falling back to
the generic PROTOCOL_ASSESSMENT_FAILED sentinel.
"""
import dataclasses

from peerhub.adapters.claude_adapter import ClaudeOutputDecoder
from peerhub.adapters.contract import ProtocolAssessment
from peerhub.core.execution import ExecutionCertainty, ProcessTerminalEvidence
from peerhub.core.protocol import ErrorCode, OperationalFailureCategory
from peerhub.dispatch.completion import assess_completion
from peerhub.dispatch.contract import (
    AskResult,
    CompletionAssessment,
    CompletionAssessmentState,
    CompletionContractKind,
    ExecutionOutcome,
    TerminalClassification,
)
from peerhub.dispatch.model import classify_attempt_failure, _terminal_error_for_result


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _decoded_output_for_quota_bytes():
    """Produce a DecodedOutput from the real weekly-limit stream-json payload."""
    decoder = ClaudeOutputDecoder()
    decoder.feed(
        b'{"type":"rate_limit_event","rate_limit_info":{"status":"rejected",'
        b'"rateLimitType":"seven_day"}}\n'
        b'{"type":"result","subtype":"success","is_error":true,'
        b'"api_error_status":429,"error":"rate_limit",'
        b'"result":"You have hit your weekly limit"}\n'
    )
    return decoder.finalize()


def _make_failed_execution():
    return ExecutionOutcome(
        started=True,
        exit_code=1,
        timed_out=False,
        cancelled=False,
        execution_certainty=ExecutionCertainty.TERMINAL,
    )


def _make_no_failure_protocol():
    return ProtocolAssessment(
        parsed=True,
        response_present=False,
        vendor_completion_marker=None,
        suspected_truncation=False,
        protocol_failure=None,
    )


def _make_failed_ask_result(failure_classification):
    completion = CompletionAssessment(
        state=CompletionAssessmentState.NOT_APPLICABLE,
        contract_kind=CompletionContractKind.DELIVERY_ONLY,
    )
    return AskResult(
        execution=_make_failed_execution(),
        protocol=_make_no_failure_protocol(),
        completion=completion,
        policy_revision=1,
        terminal_classification=TerminalClassification.EXIT_NON_ZERO,
        failure_classification=failure_classification,
    )


# ---------------------------------------------------------------------------
# Layer 2a: classify_attempt_failure
# ---------------------------------------------------------------------------

def test_classify_attempt_failure_quota_exhausted_yields_quota_error_code():
    """EXIT_NON_ZERO + quota_exhausted vendor event -> ErrorCode.QUOTA_EXHAUSTED."""
    decoded = _decoded_output_for_quota_bytes()

    failure = classify_attempt_failure(
        terminal_classification=TerminalClassification.EXIT_NON_ZERO,
        execution=_make_failed_execution(),
        protocol=_make_no_failure_protocol(),
        decoded_output=decoded,
    )

    assert failure is not None
    assert failure.code is ErrorCode.QUOTA_EXHAUSTED, (
        f"Expected QUOTA_EXHAUSTED but got {failure.code!r}"
    )
    assert failure.operational_failure_category is OperationalFailureCategory.QUOTA_EXHAUSTED


def test_classify_attempt_failure_rate_limit_event_only_yields_quota_error_code():
    """rate_limit_event alone (no is_error line) also maps to QUOTA_EXHAUSTED."""
    decoder = ClaudeOutputDecoder()
    decoder.feed(
        b'{"type":"rate_limit_event","rate_limit_info":{"status":"rejected"}}\n'
    )
    decoded = decoder.finalize()

    failure = classify_attempt_failure(
        terminal_classification=TerminalClassification.EXIT_NON_ZERO,
        execution=_make_failed_execution(),
        protocol=_make_no_failure_protocol(),
        decoded_output=decoded,
    )

    assert failure is not None
    assert failure.code is ErrorCode.QUOTA_EXHAUSTED


# ---------------------------------------------------------------------------
# Layer 2b: _terminal_error_for_result
# ---------------------------------------------------------------------------

def test_terminal_error_for_result_quota_is_not_protocol_assessment_failed():
    """Quota failure must NOT surface as the generic PROTOCOL_ASSESSMENT_FAILED."""
    decoded = _decoded_output_for_quota_bytes()

    failure_cls = classify_attempt_failure(
        terminal_classification=TerminalClassification.EXIT_NON_ZERO,
        execution=_make_failed_execution(),
        protocol=_make_no_failure_protocol(),
        decoded_output=decoded,
    )
    ask_result = _make_failed_ask_result(failure_cls)

    terminal_code = _terminal_error_for_result(ask_result)

    assert terminal_code is not ErrorCode.PROTOCOL_ASSESSMENT_FAILED, (
        "Quota exhaustion must not surface as PROTOCOL_ASSESSMENT_FAILED"
    )
    assert terminal_code is ErrorCode.QUOTA_EXHAUSTED


def test_terminal_error_for_result_no_classification_stays_protocol_assessment_failed():
    """When no classification exists at all, PROTOCOL_ASSESSMENT_FAILED is the correct fallback."""
    ask_result = _make_failed_ask_result(failure_classification=None)

    terminal_code = _terminal_error_for_result(ask_result)

    assert terminal_code is ErrorCode.PROTOCOL_ASSESSMENT_FAILED
