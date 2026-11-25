"""Quota bars, durations, and percent cells for CLI views."""
from __future__ import annotations

import math
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from peerhub.extensions.pool_health import PoolHealth


BAR_CELLS = 8
BAR_GLYPHS = {
    True: {"full": "█", "empty": "░", "cfull": "▬", "cempty": "╌", "none": "─"},
    False: {"full": "#", "empty": ".", "cfull": "=", "cempty": "-", "none": "-"},
}


def _bar_body(fraction: float, cells: int, full: str, empty: str) -> str:
    filled = int(fraction * cells + 0.5)
    return full * filled + empty * (cells - filled)


def fraction_bar(used: float | None, cells: int, unicode: bool) -> str:
    if used is None:
        return "?" * cells
    glyphs = BAR_GLYPHS[unicode]
    return _bar_body(min(1.0, max(0.0, used)), cells, glyphs["full"], glyphs["empty"])


def quota_bar(remaining: float | None, cells: int = BAR_CELLS, unicode: bool = True,
              even_burn: float | None = None, *, child: bool = False) -> str:
    """Draw remaining quota with an even-burn tick, including on the filled edge."""
    glyphs = BAR_GLYPHS[unicode]
    if remaining is None or not 0 <= remaining <= 1:
        return f"[{glyphs['none'] * cells}]"
    full, empty = (glyphs["cfull"], glyphs["cempty"]) if child else (glyphs["full"], glyphs["empty"])
    body = list(_bar_body(remaining, cells, full, empty))
    if remaining > 0 and cells > 0 and body[0] == empty:
        body[0] = full if child else ("▏" if unicode else ":")
    if even_burn is not None and cells > 0:
        tick = min(cells - 1, max(0, int(even_burn * cells)))
        body[tick] = "|"
    return f"[{''.join(body)}]"


def format_percent(fraction: float, *, show_tiny: bool = True) -> str:
    """Format a whole percent, with an optional marker for tiny nonzero quota."""
    return "<1%" if show_tiny and 0.0 < fraction <= 0.005 else f"{fraction * 100:.0f}%"


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


def forecast_duration(seconds: float) -> str:
    """Round forecasts to minute precision."""
    return human_duration(seconds) if seconds >= 120 else f"{int(seconds / 60 + 0.5)}m"


def missing_pool_fact(unicode: bool = True) -> str:
    return "—" if unicode else "?"


def format_pool_pace(pace: float | None, *, unicode: bool = True) -> str:
    if pace is None:
        return missing_pool_fact(unicode)
    return f"{pace:+.1f}" if round(pace, 1) else "0.0"


def format_pool_left(facts: PoolHealth, age: float | None, *, unicode: bool = True) -> str:
    left = facts["left_pct"]
    if left is None or facts["state"] == "reset":
        text = missing_pool_fact(unicode)
    else:
        text = format_percent(left / 100) + ("!" if facts["reserve"] == "low" else "")
    if facts["stale"] and age is not None:
        text += f" ~{int(age // 60)}m"
    return text


def pool_duration(seconds: float) -> str:
    return format_duration(seconds).removesuffix("00m")


def format_exhaust_reset(facts: PoolHealth, *, unicode: bool = True) -> str:
    reset, exhaust = facts["reset_in_seconds"], facts["exhaust_in_seconds"]
    missing = missing_pool_fact(unicode)
    reset_text = missing if reset is None else "passed" if reset <= 0 else pool_duration(reset)
    if facts["reserve"] == "out":
        exhaust_text = "OUT"
    elif exhaust is None:
        exhaust_text = missing
    else:
        exhaust_text = "past est." if exhaust < 0 else pool_duration(exhaust)
        if exhaust_text == reset_text and reset is not None and exhaust != reset:
            exhaust_text = format_duration(exhaust, extra_unit=True)
            reset_text = format_duration(reset, extra_unit=True)
    alert = "!" if facts["exhaust_before_reset"] else ""
    margin = facts["exhaust_margin_seconds"]
    if alert and exhaust_text == reset_text and margin is not None:
        early_seconds = math.floor(-margin)
        alert += f"{early_seconds}s" if early_seconds else "<1s"
    return f"{exhaust_text}/{reset_text}{alert}"
