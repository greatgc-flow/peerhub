"""Project quota pool rows for the diagnostic plan and the grouped monitor."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, NotRequired, TypedDict, cast

from peerhub.core.scalars import finite_converted_number, finite_number
from peerhub.extensions.observation_model import ABSENT_FACT_REASONS
from peerhub.extensions.pool_health import (
    PoolHealth, PoolState, Reading, limit_pool_health, measured_pool_state, pool_health,
)
from peerhub.extensions.quota_windows import scoped_weekly_parents


class PoolReading(TypedDict):
    peer: str
    pool: str
    pool_ref: str
    profiles: tuple[str, ...]
    retired: bool
    reading: Reading
    state: NotRequired[PoolState]


@dataclass(frozen=True)
class PoolRow:
    source: PoolReading
    own_health: PoolHealth
    health: PoolHealth
    parent: PoolReading | None


def project_pool_rows(readings: list[PoolReading], frame_time: float, *,
                      exclude_unavailable: bool = False, require_consumers: bool = True) -> list[PoolRow]:
    """Project each reading and apply the scoped weekly dependency to its effective facts."""
    healths = [pool_health(row["reading"], None, frame_time) for row in readings]
    descriptors: list[PoolReading] = readings
    if exclude_unavailable:
        descriptors = [{**row, "state": own["state"]} for row, own in zip(readings, healths)]
    health_by_row = {id(row): own for row, own in zip(descriptors, healths)}

    def strictness(row: PoolReading) -> tuple[bool, float]:  # #498: a fresh, lower reading of a duplicate parent wins
        own = health_by_row[id(row)]
        left = own["left_pct"]
        return bool(own["stale"]), float(left) if isinstance(left, (int, float)) else 101.0

    parents = scoped_weekly_parents(descriptors, require_consumers=require_consumers, strictness=strictness)
    result: list[PoolRow] = []
    for row, own in zip(descriptors, healths):
        parent = parents.get(id(row))
        effective = own if parent is None else limit_pool_health(
            own, health_by_row[id(parent)], parent["reading"].get("resource_pool_ref"))
        result.append(PoolRow(row, own, effective, parent))
    return result


def monitor_reading(item: Reading, read_at: float, elapsed: float) -> Reading:
    """Adapt a monitor observation and advance its age for a redraw."""
    measurements: Reading = {}
    for key in ("last_measurements", "measurements", "payload"):
        candidate = item.get(key)
        if isinstance(candidate, Mapping) and candidate:
            measurements = cast(Mapping[str, object], candidate)
            break
    age = finite_converted_number(item.get("age_seconds"))
    return {**item, "resource_pool_ref": item.get("pool_ref"), "read_at": read_at,
            "age_seconds": None if age is None else max(0, age + elapsed),
            "payload": {**measurements, "reason": item.get("reason", measurements.get("reason"))}}


LastKnown = dict[str, tuple[float, float | None]]  # observation_id -> (validated fraction, resets_at) of a STALE row


def pool_states(observations: list[dict[str, Any]], last_known: LastKnown | None = None,
                now: float | None = None, samples: dict[str, int] | None = None) -> list[dict[str, Any]]:
    """Display state of every quota pool row and its rounded percent left. A fresh (MEASURED) row is out, low, fast, ok or
    reset; a STALE row with a last-known fraction is `aged` (never out, low or fast, so a3 stays fresh-only), or `reset` once
    its resets_at passed; anything else is `?`. `fresh` marks a MEASURED row with a valid fraction; `na` marks a window the
    provider says it does not have or has disabled (ABSENT with one of ABSENT_FACT_REASONS, also when aged).

    `shown` is what the POOLS table shows (precedence n/a ? reset out low fast aged ok): `state` for a fresh row; for a STALE
    row aged at 0, low above 0 through `LOW_QUOTA_FRACTION`, fast on a threat projection, else aged. `fast` needs two projection
    samples, as in the human pool rows: the item's `projection_samples`, else `samples[observation_id]`, else 0."""
    pools: list[dict[str, Any]] = []
    for item in observations:
        if item.get("kind") != "quota" or item.get("pool_ref") is None:
            continue
        pool = str(item.get("pool_ref")).split(":")[-1]
        evidence = item.get("state")
        if evidence == "STALE" and item.get("stored_state", "MEASURED") != "MEASURED":
            evidence = item.get("stored_state")  # an aged ABSENT or failed reading is not a stale measurement
        raw_m: object = item.get("measurements")
        measured = cast(dict[str, Any], raw_m) if isinstance(raw_m, dict) else {}
        remaining = finite_number(measured.get("remaining_fraction"), overflow_is_missing=True) if evidence == "MEASURED" else None
        if remaining is not None and not 0 <= remaining <= 1:
            remaining = None
        fresh = remaining is not None
        raw_p: object = item.get("projection")
        projection = cast(dict[str, Any], raw_p) if isinstance(raw_p, dict) else {}
        reset_in = finite_number(item.get("reset_in_seconds"), overflow_is_missing=True)
        count = finite_number(item.get("projection_samples"), overflow_is_missing=True)
        if count is None:
            count = finite_number((samples or {}).get(str(item.get("observation_id"))), overflow_is_missing=True)
        known = (last_known or {}).get(str(item.get("observation_id"))) if evidence == "STALE" else None
        if known is not None:
            remaining, resets_at = known
            passed = resets_at is not None and now is not None and resets_at <= now
            state, remaining = ("reset", None) if passed else ("aged", remaining)
            shown = state if remaining is None else measured_pool_state(
                remaining, stale=True, forecast=projection.get("state"), samples=count or 0)
        elif remaining is None:
            state = shown = "?"
        elif reset_in is not None and reset_in <= 0:
            state, remaining = "reset", None
            shown = state
        else:
            state = shown = measured_pool_state(
                remaining, stale=False, forecast=projection.get("state"), samples=count or 0)
        pools.append({"pool": f"{item.get('peer_id')} {pool}", "left_pct": None if remaining is None else round(remaining * 100),
                      "state": state, "shown": shown, "window": pool.rpartition("-")[2] if "-" in pool else "?",
                      "evidence": evidence, "fresh": fresh,
                      "na": evidence == "ABSENT" and str(item.get("reason") or "").startswith(ABSENT_FACT_REASONS)})
    return pools
