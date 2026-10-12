"""Pure quota policy and conditional window-average projection; no I/O or clock reads."""

from __future__ import annotations

import math
from typing import Literal, TypeGuard, TypedDict

ProjectionState = Literal["unknown", "stale", "expired", "exhausted", "insufficient", "on_pace", "threat"]


class ExhaustionProjection(TypedDict):
    pace_pt: float | None
    projected_at_reset: float | None
    state: ProjectionState
    exhaustion_at: float | None
    measured_at: float | None
    anchor_basis: str | None
    reason: str | None


def _is_finite_num(val: object) -> TypeGuard[int | float]:
    if not isinstance(val, (int, float)) or isinstance(val, bool):
        return False
    try:
        return math.isfinite(val)
    except OverflowError:
        return False


def project_exhaustion(
    used_fraction: object,
    window_start: object,
    measured_at: object,
    resets_at: object,
    now: float | int | None,
    *,
    evidence_state: object = "MEASURED",
    anchor_basis: object = None,
    anchor_validated: object = False,
) -> ExhaustionProjection:
    """Derive one projection at a frame timestamp from evaluated evidence.

    Callers supply read-time freshness (MEASURED/STALE/etc.), never stored state.
    Anchors require explicit validation and a nonempty provenance basis;
    timestamps alone do not establish provider window semantics. Fresh measured
    exhaustion precedes support/anchor gates because it is observed, not forecast.
    Supported ETAs are conditional window averages, not demonstrated accuracy.
    """
    result: ExhaustionProjection = {
        "pace_pt": None, "projected_at_reset": None, "state": "unknown",
        "exhaustion_at": None,
        "measured_at": float(measured_at) if _is_finite_num(measured_at) else None,
        "anchor_basis": anchor_basis if isinstance(anchor_basis, str) and anchor_basis.strip() else None,
        "reason": None,
    }

    def suppress(state: ProjectionState, reason: str) -> ExhaustionProjection:
        result["state"], result["reason"] = state, reason
        return result

    if not _is_finite_num(now) or not _is_finite_num(measured_at):
        return suppress("unknown", "frame and measurement timestamps must be finite numbers")
    frame, meas = float(now), float(measured_at)
    if meas > frame:
        return suppress("unknown", "measurement timestamp is after the frame timestamp")
    if _is_finite_num(window_start) and window_start > meas:
        return suppress("unknown", "window start is after the measurement timestamp")
    if _is_finite_num(resets_at) and meas >= resets_at:
        return suppress("unknown", "measurement must precede reset")
    if _is_finite_num(resets_at) and frame >= resets_at:
        return suppress("expired", "quota window has reset")
    if evidence_state == "STALE":
        return suppress("stale", "measurement is stale at the frame timestamp")
    if evidence_state != "MEASURED":
        return suppress("unknown", "fresh measured evidence is required")
    if not _is_finite_num(used_fraction) or not 0.0 <= used_fraction <= 1.0:
        return suppress("unknown", "used_fraction must be a finite number in [0, 1]")
    u = float(used_fraction)
    if any(v is not None and not _is_finite_num(v) for v in (window_start, resets_at)):
        return suppress("unknown", "provided window anchors must be finite numbers")
    if u == 1.0:
        result.update(state="exhausted", exhaustion_at=meas)
        return result
    if anchor_validated is not True or result["anchor_basis"] is None:
        return suppress("unknown", "window anchor is unknown or unvalidated")
    if not _is_finite_num(window_start) or not _is_finite_num(resets_at):
        return suppress("unknown", "window anchors must be finite numbers")
    start, reset = float(window_start), float(resets_at)
    duration, elapsed = reset - start, meas - start
    if not math.isfinite(duration) or not math.isfinite(elapsed) or not start <= meas < reset:
        return suppress("unknown", "window ordering must satisfy start <= measurement < reset")
    e = elapsed / duration
    if e < 0.10 or u < 0.05:
        return suppress("insufficient", "not enough data: requires >=10% elapsed and >=5 percentage points used")
    pace, projected, exhaustion = 100.0 * u - 100.0 * e, 100.0 * u / e, start + elapsed / u
    if not all(math.isfinite(v) for v in (pace, projected, exhaustion)):
        return suppress("unknown", "projection is not finite")
    result.update(pace_pt=pace, projected_at_reset=projected,
                  state="threat" if projected >= 105.0 else "on_pace", exhaustion_at=exhaustion)
    return result
