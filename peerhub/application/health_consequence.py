"""Health consequence after a dispatch failure (R4 section 7.1 / cases HC-01..HC-07).

Dispatch fails DEFINITIVELY (terminal FAILED, execution certainty TERMINAL):
* ``report_only``            -> create a quarantine-review target, never auto-quarantine;
* ``review_then_quarantine`` -> create a quarantine-review target (an operator ESCALATE later
                                quarantines via the Gap-3 path);
* ``evidence_circuit_only``  -> typed evidence (an ``error_code``) is the health service's own
                                auto-circuit input, nothing extra here; WITHOUT typed evidence a
                                review request is created (no fabrication, HC-04).
Success (HC-05) and uncertain outcomes (HC-06: "uncertainty forbids invented failure") create no
health consequence. Each definitive failure is its own review (threshold=1, HC-07).
"""

from __future__ import annotations

from typing import Any

from peerhub.core.execution import ExecutionCertainty
from peerhub.dispatch.contract import RequestState

REVIEW_MODES = frozenset({"report_only", "review_then_quarantine"})


def is_definitive_failure(request_state: Any, execution_certainty: Any) -> bool:
    return (
        request_state is RequestState.FAILED
        and execution_certainty is ExecutionCertainty.TERMINAL
    )


def needs_review(mode: str, *, request_state: Any, execution_certainty: Any, error_code: Any) -> bool:
    """Whether this ask outcome must create a quarantine-review target under ``mode``."""

    if not is_definitive_failure(request_state, execution_certainty):
        return False
    if mode in REVIEW_MODES:
        return True
    if mode == "evidence_circuit_only":
        return error_code is None
    return False


def record_dispatch_failure(
    service: Any,
    *,
    peer_key: str,
    profile_id: str,
    request_state: Any,
    error_code: Any,
    actor_id: str,
) -> Any:
    """Append the failure to the peer's operational-error series, requesting a review at once."""

    code = getattr(error_code, "value", error_code)
    state = getattr(request_state, "value", request_state)
    return service.report_error(
        peer_key=peer_key,
        pattern=f"dispatch-failure:{profile_id}:{code or state}",
        severity="error",
        detail=f"direct ask failed definitively: state={state} error_code={code}",
        actor_id=actor_id,
        threshold=1,
    )
