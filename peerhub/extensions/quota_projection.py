"""Pure quota policy and conditional window-average projection; no I/O or clock reads."""

from __future__ import annotations

import math
from typing import Literal, TypeGuard, TypedDict

ProjectionState = Literal["unknown", "stale", "expired", "exhausted", "insufficient", "on_pace", "threat"]
LONG_WINDOW_SECONDS = 86400.0
MIN_ELAPSED_SHORT = 0.10
MIN_ELAPSED_LONG = 0.20
MIN_USED_FRACTION = 0.05
THREAT_PROJECTED_PERCENT = 105.0


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
) -> ExhaustionProjection:
    """Derive one projection at a frame timestamp from evaluated evidence.

    Callers supply read-time freshness (MEASURED/STALE/etc.), never stored state.
    Anchor consistency is derived here; inferred anchors remain conditional,
    without asserting validated provider window semantics. Fresh measured
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
    no_usage = evidence_state == "MEASURED" and _is_finite_num(used_fraction) and used_fraction == 0
    if not no_usage and _is_finite_num(window_start) and window_start > meas:
        return suppress("unknown", "window start is after the measurement timestamp")
    if _is_finite_num(resets_at) and meas >= resets_at:
        return suppress("unknown", "measurement must precede reset")
    expired = _is_finite_num(resets_at) and frame >= resets_at
    if evidence_state not in ("MEASURED", "STALE"):
        if expired:
            return suppress("expired", "quota window has reset")
        return suppress("unknown", "fresh measured evidence is required")
    if not _is_finite_num(used_fraction) or not 0.0 <= used_fraction <= 1.0:
        if expired:
            return suppress("expired", "quota window has reset")
        return suppress("unknown", "used_fraction must be a finite number in [0, 1]")
    u = float(used_fraction)
    if any(v is not None and not _is_finite_num(v) for v in (window_start, resets_at)):
        return suppress("unknown", "provided window anchors must be finite numbers")
    consistent = False
    if _is_finite_num(window_start) and _is_finite_num(resets_at):
        start, reset = float(window_start), float(resets_at)
        duration, elapsed = reset - start, meas - start
        consistent = math.isfinite(duration) and math.isfinite(elapsed) and duration > 0 and start <= meas < reset
        if consistent:
            result["anchor_basis"] = result["anchor_basis"] or "reset_minus_duration_inferred"
            result["pace_pt"] = 100.0 * u - 100.0 * (elapsed / duration)
    if expired:
        return suppress("expired", "quota window has reset")
    if evidence_state == "STALE":
        return suppress("stale", "measurement is stale at the frame timestamp")
    if evidence_state != "MEASURED":
        return suppress("unknown", "fresh measured evidence is required")
    if u == 0.0:
        return suppress("insufficient", "no usage yet")
    if u == 1.0:
        result.update(state="exhausted", exhaustion_at=meas)
        return result
    if not _is_finite_num(window_start) or not _is_finite_num(resets_at):
        return suppress("unknown", "window anchors must be finite numbers")
    start, reset = float(window_start), float(resets_at)
    duration, elapsed = reset - start, meas - start
    if not consistent:
        return suppress("unknown", "window ordering must satisfy start <= measurement < reset")
    e = elapsed / duration
    minimum_elapsed = MIN_ELAPSED_LONG if duration >= LONG_WINDOW_SECONDS else MIN_ELAPSED_SHORT
    # Compare in seconds: division can put an exact 10% boundary one ULP below it.
    if elapsed < minimum_elapsed * duration or u < MIN_USED_FRACTION:
        return suppress("insufficient", f"not enough data: requires >={minimum_elapsed:.0%} elapsed and >=5 percentage points used")
    pace, projected, exhaustion = 100.0 * u - 100.0 * e, 100.0 * u / e, start + elapsed / u
    if not all(math.isfinite(v) for v in (pace, projected, exhaustion)):
        return suppress("unknown", "projection is not finite")
    result.update(pace_pt=pace, projected_at_reset=projected,
                  state="threat" if projected >= THREAT_PROJECTED_PERCENT else "on_pace", exhaustion_at=exhaustion)
    return result
