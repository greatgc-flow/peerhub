"""Validation-only prototype for the Phase 3 attempt-failure mapper.

This module is deliberately outside ``peerhub/``.  It proves the draft-3
mapping can be expressed against today's contracts without becoming the
production classification-plumbing increment.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TypeAlias

from peerhub.adapters.contract import (
    DecodedOutput,
    DecoderEventKind,
    ProtocolAssessment,
)
from peerhub.core.protocol import (
    ErrorCode,
    ErrorPhase,
    OperationalFailureCategory,
)
from peerhub.dispatch.contract import ExecutionOutcome
from peerhub.dispatch.process import TerminalClassification


class ProposedErrorCode(str, Enum):
    """The two stable codes proposed by the draft-3 contract."""

    SESSION_INVALID = "SESSION_INVALID"
    INVOCATION_PLAN_REJECTED = "INVOCATION_PLAN_REJECTED"


StableErrorCode: TypeAlias = ErrorCode | ProposedErrorCode


@dataclass(frozen=True)
class AttemptFailureClassification:
    code: StableErrorCode
    phase: ErrorPhase
    operational_failure_category: OperationalFailureCategory | None


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

    code: StableErrorCode = _TERMINAL_ROWS[terminal_classification]
    category = None
    if terminal_classification is TerminalClassification.EXIT_NON_ZERO:
        vendor_kind = _normalized_vendor_kind(decoded_output)
        if vendor_kind == "session_invalid":
            code = ProposedErrorCode.SESSION_INVALID
        elif vendor_kind == "invocation_plan_rejected":
            code = ProposedErrorCode.INVOCATION_PLAN_REJECTED
        else:
            category = _OPERATIONAL_KINDS.get(vendor_kind or "")

    return AttemptFailureClassification(code, ErrorPhase.POST_SPAWN, category)
