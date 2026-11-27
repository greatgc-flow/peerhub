"""Quota freshness vocabulary shared by the CLI (composition root) and the observation extension.

A neutral, dependency-free core module: the monitor's refresh scheduling and the freshness policy must read ONE number, and
it must stay importable when the first-party extensions are disabled (core-only mode).
"""

from __future__ import annotations

LOW_QUOTA_FRACTION = 0.10  # a pool with this fraction left or less is LOW in the monitor and diag (one source)
QUOTA_TTL_SECONDS = 300  # a quota reading is STALE (gray) at this age; the monitor refreshes before it, see MONITOR_REFRESH_LEAD_SECONDS
PLAN_SPEND_PACE_PT = 10.0  # spend at or below the negative threshold
PLAN_REST_PACE_PT = 20.0  # rest strictly above the positive threshold
PACE_BAND_ROUNDING_DIGITS = 9  # suppress floating-point noise in band comparisons only
PACE_DECIMALS = 2  # displayed PACE digits: one step is ~10 min of a 7-day window (one digit was ~1h41m)


def format_pace(pace: float) -> str:
    """Signed PACE text in percentage points; exact zero after rounding reads unsigned."""
    return f"{pace:+.{PACE_DECIMALS}f}" if round(pace, PACE_DECIMALS) else f"{0:.{PACE_DECIMALS}f}"
