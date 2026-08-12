"""Focused checks for the validation-only Phase 3 classifier prototype."""

from __future__ import annotations

from pathlib import Path

import pytest

from peerhub.adapters.contract import (
    DecodedOutput,
    DecoderEvent,
    DecoderEventKind,
    ProtocolAssessment,
)
from peerhub.core.execution import ExecutionCertainty
from peerhub.core.protocol import (
    ErrorCode,
    ErrorPhase,
    OperationalFailureCategory,
)
from peerhub.dispatch.contract import ExecutionOutcome
from peerhub.dispatch.process import TerminalClassification

# ``tests/unit/tools`` is a package and can shadow the repository's real
# ``tools`` package during pytest collection.  Extend the discovered package
# path using the same approach as tests/unit/tools/test_peerhub_facts.py.
import tools as tools_test_package

project_tools_path = str(Path(__file__).resolve().parents[2] / "tools")
if project_tools_path not in tools_test_package.__path__:
    tools_test_package.__path__.append(project_tools_path)

from tools.phase3_failure_classifier_prototype import (
    ProposedErrorCode,
    classify_attempt_failure,
)


def _execution(exit_code: int | None = 1) -> ExecutionOutcome:
    return ExecutionOutcome(
        started=True,
        exit_code=exit_code,
        timed_out=False,
        cancelled=False,
        execution_certainty=ExecutionCertainty.TERMINAL,
    )


def _protocol(failure: ErrorCode | None = None) -> ProtocolAssessment:
    return ProtocolAssessment(
        parsed=failure is None,
        response_present=failure is None,
        vendor_completion_marker=None,
        suspected_truncation=False,
        protocol_failure=failure,
    )


def _decoded_event(
    kind: DecoderEventKind,
    payload: dict[str, object],
) -> DecodedOutput:
    return DecodedOutput(
        canonical_text="",
        canonical_lines=(),
        events=(DecoderEvent(kind=kind, payload=payload),),
    )


@pytest.mark.parametrize(
    ("terminal", "expected_code"),
    [
        (TerminalClassification.START_UNCERTAIN, ErrorCode.START_UNCERTAIN),
        (TerminalClassification.SILENCE_TIMEOUT, ErrorCode.SILENCE_TIMEOUT),
        (TerminalClassification.PROCESS_TIMEOUT, ErrorCode.PROCESS_TIMEOUT),
        (TerminalClassification.EXIT_NON_ZERO, ErrorCode.INTERNAL_ERROR),
        (
            TerminalClassification.OUTPUT_LIMIT_EXCEEDED,
            ErrorCode.PROCESS_KILLED,
        ),
    ],
)
def test_all_five_terminal_rows_are_total(
    terminal: TerminalClassification,
    expected_code: ErrorCode,
) -> None:
    result = classify_attempt_failure(
        terminal_classification=terminal,
        execution=_execution(),
        protocol=_protocol(),
        decoded_output=None,
    )

    assert result is not None
    assert result.code is expected_code
    assert result.phase is ErrorPhase.POST_SPAWN
    assert result.operational_failure_category is None


def test_none_with_protocol_failure_maps_to_assessment() -> None:
    result = classify_attempt_failure(
        terminal_classification=None,
        execution=_execution(exit_code=0),
        protocol=_protocol(ErrorCode.PROTOCOL_ASSESSMENT_FAILED),
        decoded_output=None,
    )

    assert result is not None
    assert result.code is ErrorCode.PROTOCOL_ASSESSMENT_FAILED
    assert result.phase is ErrorPhase.ASSESSMENT


def test_none_without_protocol_failure_returns_none() -> None:
    assert (
        classify_attempt_failure(
            terminal_classification=None,
            execution=_execution(exit_code=0),
            protocol=_protocol(),
            decoded_output=None,
        )
        is None
    )


@pytest.mark.parametrize(
    ("normalized_kind", "expected_code"),
    [
        ("session_invalid", ProposedErrorCode.SESSION_INVALID),
        (
            "invocation_plan_rejected",
            ProposedErrorCode.INVOCATION_PLAN_REJECTED,
        ),
    ],
)
def test_normalized_vendor_error_reaches_proposed_codes(
    normalized_kind: str,
    expected_code: ProposedErrorCode,
) -> None:
    decoded = _decoded_event(
        DecoderEventKind.VENDOR_ERROR,
        {
            "normalized_kind": normalized_kind,
            "evidence_source": "known_terminal_pattern",
        },
    )

    result = classify_attempt_failure(
        terminal_classification=TerminalClassification.EXIT_NON_ZERO,
        execution=_execution(),
        protocol=_protocol(),
        decoded_output=decoded,
    )

    assert result is not None
    assert result.code is expected_code


def test_normalized_operational_error_refines_category_only() -> None:
    decoded = _decoded_event(
        DecoderEventKind.VENDOR_ERROR,
        {
            "normalized_kind": "auth_unavailable",
            "evidence_source": "structured_vendor_output",
        },
    )

    result = classify_attempt_failure(
        terminal_classification=TerminalClassification.EXIT_NON_ZERO,
        execution=_execution(),
        protocol=_protocol(),
        decoded_output=decoded,
    )

    assert result is not None
    assert result.code is ErrorCode.INTERNAL_ERROR
    assert (
        result.operational_failure_category
        is OperationalFailureCategory.AUTH_UNAVAILABLE
    )


@pytest.mark.parametrize(
    "decoded",
    [
        _decoded_event(
            DecoderEventKind.ASSISTANT_TEXT,
            {"text": "invalid model operand"},
        ),
        _decoded_event(
            DecoderEventKind.VENDOR_ERROR,
            {"text": "invalid model operand"},
        ),
        _decoded_event(
            DecoderEventKind.VENDOR_ERROR,
            {"normalized_kind": "invocation_plan_rejected"},
        ),
    ],
)
def test_unnormalized_text_cannot_trigger_stable_refinement(
    decoded: DecodedOutput,
) -> None:
    result = classify_attempt_failure(
        terminal_classification=TerminalClassification.EXIT_NON_ZERO,
        execution=_execution(),
        protocol=_protocol(),
        decoded_output=decoded,
    )

    assert result is not None
    assert result.code is ErrorCode.INTERNAL_ERROR
    assert result.operational_failure_category is None
