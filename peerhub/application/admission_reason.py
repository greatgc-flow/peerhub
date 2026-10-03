"""Human-readable admission denial reasons (T0): replaces the opaque "request was not admitted"."""

from __future__ import annotations

from typing import Any

_MAX_CANDIDATES = 6


def describe_admission_denial(result: Any) -> str:
    """Summarize why an AdmissionWorkflowResult carries no dispatch admission.

    Uses the route outcome (error code + per-candidate eligibility/exclusion reason). Never raises.
    """
    route = getattr(result, "route", None)
    if route is None:
        return "no route was evaluated (replayed or concurrent admission without a stored decision)"
    code = getattr(getattr(route, "error_code", None), "name", None) or "UNKNOWN"
    decision = getattr(route, "decision", None)
    cands = tuple(getattr(decision, "candidates", ()) or ())
    if not cands:
        return f"{code}: no candidate peers configured for the required capability tier"
    parts = []
    for c in cands[:_MAX_CANDIDATES]:
        elig = getattr(getattr(c, "eligibility", None), "name", str(getattr(c, "eligibility", "?")))
        why = getattr(c, "exclusion_reason", None) or "no reason recorded"
        parts.append(f"{getattr(c, 'candidate_id', '?')} {elig} ({why})")
    more = f"; +{len(cands) - _MAX_CANDIDATES} more" if len(cands) > _MAX_CANDIDATES else ""
    return f"{code}: " + "; ".join(parts) + more + " [stale health/telemetry? try `peerhub diag --fresh`]"
