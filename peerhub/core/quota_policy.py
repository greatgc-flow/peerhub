"""Quota freshness vocabulary shared by the CLI (composition root) and the observation extension.

A neutral, dependency-free core module: the monitor's refresh scheduling and the freshness policy must read ONE number, and
it must stay importable when the first-party extensions are disabled (core-only mode).
"""

from __future__ import annotations

import math

LOW_QUOTA_FRACTION = 0.10  # a pool with this fraction left or less is LOW in the monitor and diag (one source)
QUOTA_TTL_SECONDS = 300  # a quota reading is STALE (gray) at this age; the monitor refreshes before it, see MONITOR_REFRESH_LEAD_SECONDS
PLAN_SPEND_PACE_PT = 10.0  # spend at or below the negative threshold
PLAN_REST_PACE_PT = 20.0  # rest strictly above the positive threshold
PACE_BAND_ROUNDING_DIGITS = 9  # suppress floating-point noise in band comparisons only
PACE_DECIMALS = 2  # displayed PACE digits: one step is ~10 min of a 7-day window (one digit was ~1h41m)


def format_pace(pace: float) -> str:
    """Signed PACE text in percentage points; exact zero after rounding reads unsigned."""
    return f"{pace:+.{PACE_DECIMALS}f}" if round(pace, PACE_DECIMALS) else f"{0:.{PACE_DECIMALS}f}"


def format_duration(seconds: float, *, extra_unit: bool = False) -> str:
    total_minutes, secs = divmod(math.floor(seconds), 60)
    hours, minutes = divmod(total_minutes, 60)
    if hours >= 24:
        days, hours = divmod(hours, 24)
        return f"{days}d{hours:02d}h" + (f"{minutes:02d}m" if extra_unit else "")
    text = f"{hours}h{minutes:02d}m" if hours > 0 else f"{total_minutes}m"
    return text + (f"{secs:02d}s" if extra_unit else "")


def human_duration(seconds: float | None) -> str:
    """Round to minutes from two minutes onward; missing values stay explicit."""
    if seconds is None or seconds < 0:
        return "?"
    whole = int(seconds) if seconds < 120 else int(seconds / 60 + 0.5) * 60
    if whole >= 3600:
        return format_duration(whole)
    minutes, secs = divmod(whole, 60)
    if not minutes:
        return f"{secs}s"
    return f"{minutes}m{secs:02d}s" if secs else f"{minutes}m"
