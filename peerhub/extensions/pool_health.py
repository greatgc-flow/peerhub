"""Quota health and pace comparisons from stored evidence at a supplied frame time."""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Literal, TypedDict, cast

from peerhub.core.quota_policy import LOW_QUOTA_FRACTION, PACE_BAND_ROUNDING_DIGITS, PLAN_REST_PACE_PT, PLAN_SPEND_PACE_PT
from peerhub.extensions.observation_model import ABSENT_FACT_REASONS
from peerhub.extensions.quota_projection import ProjectionState, limit_by_parent, observation_projection
from peerhub.extensions.quota_windows import quota_reset_at

PoolState = Literal["n/a", "?", "reset", "out", "blocked", "low", "fast", "aged", "ok"]
PaceBand = Literal["spend", "ok", "rest"]
Reserve = Literal["out", "low", "ok"]
Reading = Mapping[str, object]

PACE_GAP_BASELINE = "X-7D"
PACE_GAP_POOLS = ("C-7D", "G-7D", "A-7D", "3P-7D")
_STATE_SEVERITY = {"ok": 0, "fast": 1, "low": 2, "out": 3}
_FAST_MIN_SAMPLES = 2


class PoolHealth(TypedDict):
    state: PoolState
    pace_band: PaceBand | None
    reserve: Reserve | None
    even_left_pct: float | None
    exhaust_in_seconds: float | None
    exhaust_margin_seconds: float | None
    exhaust_before_reset: bool
    forecast: ProjectionState
    stale: bool
    limited_by: str | None
    own_left_pct: float | None
    left_pct: float | None
    pace_pt: float | None
    reset_in_seconds: float | None
    measured: bool


class PaceGaps(TypedDict):
    baseline: str | None
    pools: dict[str, float | None]


def _num(value: object) -> float | None:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except OverflowError:
        return None
    return number if math.isfinite(number) else None


def pace_band(pace: float | None) -> PaceBand | None:
    """Classify pace at policy precision while keeping the measured pace intact."""
    if pace is None:
        return None
    pace = round(pace, PACE_BAND_ROUNDING_DIGITS)
    if pace <= -PLAN_SPEND_PACE_PT:
        return "spend"
    return "rest" if pace > PLAN_REST_PACE_PT else "ok"


def _exhaust_margin(exhaust: float | None, reset: float | None) -> float | None:
    return None if exhaust is None or reset is None else exhaust - reset


def measured_pool_state(remaining: float, *, stale: bool, forecast: object, samples: float) -> PoolState:
    """Grade a usable fraction after the caller has checked its reset."""
    if remaining == 0:
        return "aged" if stale else "out"
    if remaining <= LOW_QUOTA_FRACTION:
        return "low"
    if samples >= _FAST_MIN_SAMPLES and forecast == "threat":
        return "fast"
    return "aged" if stale else "ok"


def _reading_health(reading: Reading, frame_time: float) -> PoolHealth:
    raw_payload = reading.get("payload")
    payload: Reading = cast(Reading, raw_payload) if isinstance(raw_payload, Mapping) else {}
    evidence = reading.get("state")
    age, ttl = _num(reading.get("age_seconds")), _num(reading.get("ttl_seconds"))
    stale = evidence == "STALE" if ttl is None else age is not None and age >= ttl
    remaining = _num(payload.get("remaining_fraction"))
    measured = evidence in ("MEASURED", "STALE") and remaining is not None and 0 <= remaining <= 1
    if not measured:
        remaining = None
    left = None if remaining is None else remaining * 100
    resets_at = quota_reset_at(reading, payload, frame_time)
    reset = None if resets_at is None else resets_at - frame_time
    projection_state = ("STALE" if stale else "MEASURED") if measured else evidence
    proj = observation_projection({**reading, "payload": {**payload, "resets_at": resets_at}, "state": projection_state},
                                  frame_time)
    samples = _num(reading.get("projection_samples")) or 0

    state: PoolState
    if evidence == "ABSENT" and not stale and (reading.get("display_retired")
                                              or str(payload.get("reason", "")).startswith(ABSENT_FACT_REASONS)):
        state = "n/a"
    elif remaining is None:
        state = "aged" if stale and reading.get("stored_state") == "ABSENT" else "?"
    elif reset is not None and reset <= 0:
        state = "reset"
    else:
        state = measured_pool_state(remaining, stale=stale, forecast=proj["state"], samples=samples)

    reserve: Reserve | None = None
    if remaining is not None and state != "reset":
        reserve = "out" if state == "out" else "low" if remaining <= LOW_QUOTA_FRACTION else "ok"
    pace = proj["pace_pt"] if measured and state != "reset" else None
    exhaust_at = proj["exhaustion_at"] if proj["state"] in ("threat", "on_pace") else None
    exhaust = None if exhaust_at is None else exhaust_at - frame_time
    margin = _exhaust_margin(exhaust, reset)
    return PoolHealth(
        state=state, pace_band=pace_band(pace), reserve=reserve,
        even_left_pct=None if left is None or pace is None else left + pace,
        exhaust_in_seconds=exhaust, exhaust_margin_seconds=margin,
        exhaust_before_reset=margin is not None and margin < 0,
        forecast=proj["state"],
        stale=stale, limited_by=None, own_left_pct=None, left_pct=left,
        pace_pt=pace, reset_in_seconds=reset, measured=measured,
    )


def limit_pool_health(own: PoolHealth, parent: PoolHealth, parent_ref: object) -> PoolHealth:
    """Apply a scoped parent to already projected pool facts."""
    if (own["stale"] or own["state"] not in _STATE_SEVERITY
            or parent["stale"] or parent["state"] not in _STATE_SEVERITY):
        return own

    left, reset, lower = limit_by_parent(own["left_pct"], own["reset_in_seconds"],
                                         parent["left_pct"], parent["reset_in_seconds"])
    worse = _STATE_SEVERITY[parent["state"]] > _STATE_SEVERITY[own["state"]]
    if not (lower or worse):
        return own
    source = parent if lower else own
    state = own["state"]
    if worse:
        state = "blocked" if parent["state"] == "out" else parent["state"]
    margin = _exhaust_margin(source["exhaust_in_seconds"], reset)
    return {
        **source, "state": state, "left_pct": left, "reset_in_seconds": reset,
        "limited_by": parent_ref if isinstance(parent_ref, str) else None, "own_left_pct": own["left_pct"],
        "exhaust_margin_seconds": margin, "exhaust_before_reset": margin is not None and margin < 0,
    }


def pool_health(reading: Reading, parent_reading: Reading | None, frame_time: float) -> PoolHealth:
    """Derive a quota reading's facts at frame time, optionally with a scoped parent."""
    own = _reading_health(reading, frame_time)
    if parent_reading is None or own["stale"] or own["state"] not in _STATE_SEVERITY:
        return own
    return limit_pool_health(own, _reading_health(parent_reading, frame_time), parent_reading.get("resource_pool_ref"))


def pace_gaps(healths: Mapping[str, PoolHealth]) -> PaceGaps:
    """Compare unrounded, fresh measured weekly paces by pool name."""
    def usable_pace(name: str) -> float | None:
        facts = healths.get(name)
        return facts["pace_pt"] if facts is not None and facts["measured"] and not facts["stale"] else None

    baseline = usable_pace(PACE_GAP_BASELINE)
    gaps: dict[str, float | None] = {}
    for name in PACE_GAP_POOLS:
        pace = usable_pace(name)
        gaps[name] = None if baseline is None or pace is None else pace - baseline
    return {"baseline": None if baseline is None else PACE_GAP_BASELINE, "pools": gaps}
