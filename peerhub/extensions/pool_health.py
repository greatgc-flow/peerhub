"""Quota health and pace comparisons from stored evidence at a supplied frame time."""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Literal, TypedDict, cast

from peerhub.core.quota_policy import LOW_QUOTA_FRACTION, PACE_BAND_ROUNDING_DIGITS, PLAN_REST_PACE_PT, PLAN_SPEND_PACE_PT
from peerhub.extensions.diag_quota import projection
from peerhub.extensions.observation_model import ABSENT_FACT_REASONS, instant_us
from peerhub.extensions.quota_projection import ProjectionState, limit_by_parent

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


def _before_reset(exhaust: float | None, reset: float | None) -> bool:
    return exhaust is not None and reset is not None and exhaust < reset


def _reset_at(reading: Reading, payload: Reading, frame_time: float) -> float | None:
    absolute = _num(payload.get("resets_at"))
    if absolute is not None:
        return absolute
    # Stored durations are relative to their observation. The monitor can also
    # supply a countdown already evaluated at its snapshot time.
    duration = _num(payload.get("reset_in_seconds"))
    snapshot_at = _num(reading.get("read_at"))
    if duration is None:
        duration = _num(reading.get("reset_in_seconds"))
        if snapshot_at is not None and duration is not None:
            return snapshot_at + duration
    if duration is None:
        return None
    observed_at = reading.get("observed_at")
    if isinstance(observed_at, str):
        try:
            return instant_us(observed_at) / 1_000_000 + duration
        except (ValueError, OverflowError):
            pass
    anchor = snapshot_at if snapshot_at is not None else frame_time - (_num(reading.get("age_seconds")) or 0)
    return anchor + duration


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
    resets_at = _reset_at(reading, payload, frame_time)
    reset = None if resets_at is None else resets_at - frame_time
    projection_state = ("STALE" if stale else "MEASURED") if measured else evidence
    proj = projection({**reading, "payload": {**payload, "resets_at": resets_at}, "state": projection_state}, frame_time)
    samples = _num(reading.get("projection_samples")) or 0

    state: PoolState
    if evidence == "ABSENT" and not stale and str(payload.get("reason", "")).startswith(ABSENT_FACT_REASONS):
        state = "n/a"
    elif remaining is None:
        state = "aged" if stale and reading.get("stored_state") == "ABSENT" else "?"
    elif reset is not None and reset <= 0:
        state = "reset"
    elif remaining == 0:
        state = "aged" if stale else "out"
    elif remaining <= LOW_QUOTA_FRACTION:
        state = "low"
    elif samples >= _FAST_MIN_SAMPLES and proj["state"] == "threat":
        state = "fast"
    else:
        state = "aged" if stale else "ok"

    reserve: Reserve | None = None
    if remaining is not None and state != "reset":
        reserve = "out" if state == "out" else "low" if remaining <= LOW_QUOTA_FRACTION else "ok"
    pace = proj["pace_pt"] if measured and state != "reset" else None
    exhaust_at = proj["exhaustion_at"] if proj["state"] in ("threat", "on_pace") else None
    exhaust = None if exhaust_at is None else exhaust_at - frame_time
    return PoolHealth(
        state=state, pace_band=pace_band(pace), reserve=reserve,
        even_left_pct=None if left is None or pace is None else left + pace,
        exhaust_in_seconds=exhaust, exhaust_before_reset=_before_reset(exhaust, reset),
        forecast=proj["state"],
        stale=stale, limited_by=None, own_left_pct=None, left_pct=left,
        pace_pt=pace, reset_in_seconds=reset, measured=measured,
    )


def pool_health(reading: Reading, parent_reading: Reading | None, frame_time: float) -> PoolHealth:
    """Derive a diagnostic quota reading's facts, optionally with a scoped parent.

    The reading's age is evaluated at frame time. Its payload and observed_at
    provide the anchors for the shared projection.
    """
    own = _reading_health(reading, frame_time)
    if parent_reading is None or own["stale"] or own["state"] not in _STATE_SEVERITY:
        return own
    parent = _reading_health(parent_reading, frame_time)
    if parent["stale"] or parent["state"] not in _STATE_SEVERITY:
        return own

    left, reset, lower = limit_by_parent(own["left_pct"], own["reset_in_seconds"],
                                         parent["left_pct"], parent["reset_in_seconds"])
    worse = _STATE_SEVERITY[parent["state"]] > _STATE_SEVERITY[own["state"]]
    if not (lower or worse):
        return own
    source = parent if lower else own
    ref = parent_reading.get("resource_pool_ref")
    state = own["state"]
    if worse:
        state = "blocked" if parent["state"] == "out" else parent["state"]
    return {
        **source, "state": state, "left_pct": left, "reset_in_seconds": reset,
        "limited_by": ref if isinstance(ref, str) else None, "own_left_pct": own["left_pct"],
        "exhaust_before_reset": _before_reset(source["exhaust_in_seconds"], reset),
    }


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
