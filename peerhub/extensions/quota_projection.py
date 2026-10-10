"""PeerHub first-party extension: Quota exhaustion projection (pure calculation).

Pure, deterministic reader: no I/O, no database access, no clock read side-effects.
Calculates ETA to quota exhaustion from measurement timestamp, window start, reset time and usage fraction.
"""

from __future__ import annotations

import math
from typing import Literal, Mapping, Sequence, TypeGuard, TypedDict

MIN_ELAPSED_SECONDS = 60.0
RECENT_WINDOW_SECONDS = 30 * 60.0  # 30 minutes


class ExhaustionProjection(TypedDict):
    status: Literal["ok", "exhausted", "idle", "safe_until_reset", "unavailable"]
    basis: Literal["recent_rate", "window_average"]
    measured_at: float | None
    exhaustion_at: float | None
    exhausts_before_reset: bool | None
    reason: str | None


def _is_finite_num(val: object) -> TypeGuard[int | float]:
    return isinstance(val, (int, float)) and not isinstance(val, bool) and math.isfinite(val)


def project_exhaustion(
    used_fraction: object,
    window_start: object,
    measured_at: object,
    resets_at: object,
    now: float | int | None,
    recent_samples: Sequence[tuple[float, float] | Mapping[str, object]] | None = None,
) -> ExhaustionProjection:
    """Pure calculation projecting when quota will exhaust.

    Returns dict with keys:
      status: 'ok' | 'exhausted' | 'idle' | 'safe_until_reset' | 'unavailable'
      basis: 'recent_rate' | 'window_average'
      measured_at: UTC epoch float | int | None
      exhaustion_at: UTC epoch float | None
      exhausts_before_reset: bool | None
      reason: str | None (when unavailable)
    """
    meas_ret = float(measured_at) if _is_finite_num(measured_at) else None

    # Validate finite numeric values
    if not _is_finite_num(used_fraction):
        return {
            "status": "unavailable",
            "basis": "window_average",
            "measured_at": meas_ret,
            "exhaustion_at": None,
            "exhausts_before_reset": None,
            "reason": "used_fraction must be a finite number",
        }

    u = float(used_fraction)
    if u < 0.0 or u > 1.0:
        return {
            "status": "unavailable",
            "basis": "window_average",
            "measured_at": meas_ret,
            "exhaustion_at": None,
            "exhausts_before_reset": None,
            "reason": f"used_fraction out of bounds [0, 1]: {u}",
        }

    if not (_is_finite_num(window_start) and _is_finite_num(measured_at) and _is_finite_num(resets_at)):
        return {
            "status": "unavailable",
            "basis": "window_average",
            "measured_at": meas_ret,
            "exhaustion_at": None,
            "exhausts_before_reset": None,
            "reason": "window_start, measured_at, and resets_at must be finite numbers",
        }

    start = float(window_start)
    meas = float(measured_at)
    reset = float(resets_at)

    if not (start <= meas):
        return {
            "status": "unavailable",
            "basis": "window_average",
            "measured_at": meas_ret,
            "exhaustion_at": None,
            "exhausts_before_reset": None,
            "reason": f"window_start ({start}) must be <= measured_at ({meas})",
        }

    if not (meas < reset):
        return {
            "status": "unavailable",
            "basis": "window_average",
            "measured_at": meas_ret,
            "exhaustion_at": None,
            "exhausts_before_reset": None,
            "reason": f"measured_at ({meas}) must be < resets_at ({reset})",
        }

    elapsed = meas - start
    if elapsed < MIN_ELAPSED_SECONDS:
        return {
            "status": "unavailable",
            "basis": "window_average",
            "measured_at": meas_ret,
            "exhaustion_at": None,
            "exhausts_before_reset": None,
            "reason": f"elapsed window time {elapsed:.1f}s is less than {MIN_ELAPSED_SECONDS:.0f}s minimum",
        }

    # Idle (zero usage in window)
    if u == 0.0:
        return {
            "status": "idle",
            "basis": "window_average",
            "measured_at": meas_ret,
            "exhaustion_at": None,
            "exhausts_before_reset": False,
            "reason": None,
        }

    # Exhausted (100% or more used)
    if u >= 1.0:
        return {
            "status": "exhausted",
            "basis": "window_average",
            "measured_at": meas_ret,
            "exhaustion_at": meas,
            "exhausts_before_reset": True,
            "reason": None,
        }

    basis: Literal["recent_rate", "window_average"] = "window_average"
    exhaustion_at: float | None = None

    # Hybrid basis: check if >=2 recent samples exist within the current window and last 30 minutes
    if recent_samples:
        valid_samples: dict[float, float] = {}
        for s in recent_samples:
            t_val: object = None
            u_val: object = None
            if isinstance(s, Mapping):
                t_val = s.get("measured_at", s.get("timestamp"))
                u_val = s.get("used_fraction", s.get("used"))
            elif len(s) >= 2:
                t_val, u_val = s[0], s[1]

            if _is_finite_num(t_val) and _is_finite_num(u_val):
                t_flt, u_flt = float(t_val), float(u_val)
                if start <= t_flt <= meas and t_flt >= (meas - RECENT_WINDOW_SECONDS) and 0.0 <= u_flt <= 1.0:
                    valid_samples[t_flt] = u_flt

        # Ensure current measurement point is represented
        if meas not in valid_samples:
            valid_samples[meas] = u

        if len(valid_samples) >= 2:
            sorted_pts = sorted(valid_samples.items(), key=lambda pt: pt[0])
            t1, u1 = sorted_pts[0]
            t2, u2 = sorted_pts[-1]
            dt = t2 - t1
            du = u2 - u1
            if dt > 0 and du > 0:
                recent_rate = du / dt
                remaining = max(0.0, 1.0 - u)
                exhaustion_at = meas + (remaining / recent_rate)
                basis = "recent_rate"

    if basis == "window_average":
        exhaustion_at = start + (elapsed / u)

    assert exhaustion_at is not None
    if exhaustion_at >= reset:
        return {
            "status": "safe_until_reset",
            "basis": basis,
            "measured_at": meas_ret,
            "exhaustion_at": exhaustion_at,
            "exhausts_before_reset": False,
            "reason": None,
        }

    return {
        "status": "ok",
        "basis": basis,
        "measured_at": meas_ret,
        "exhaustion_at": exhaustion_at,
        "exhausts_before_reset": True,
        "reason": None,
    }
