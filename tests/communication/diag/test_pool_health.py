"""Quota facts shared by the plan and monitor."""
from __future__ import annotations

from collections.abc import Mapping

import pytest

from peerhub.core.quota_policy import PLAN_REST_PACE_PT, PLAN_SPEND_PACE_PT
from peerhub.core.quota_policy import LOW_QUOTA_FRACTION, QUOTA_TTL_SECONDS
from peerhub.extensions.quota_projection import MIN_ELAPSED_LONG
from tests.communication.diag.test_diag_plan_339 import DAY, HOUR, NOW, _item

WEEK = 7 * DAY
HALF = .5
FRESH_AGE = QUOTA_TTL_SECONDS / 2
CHILD_LEFT = .26
OVER_BOUNDARY = .0001


def reading(left, *, elapsed=WEEK / 2, **kwargs):
    return _item("cc", "C-7D", left, span=WEEK, elapsed=elapsed, **kwargs)


def health(item, parent=None):
    from peerhub.extensions.pool_health import pool_health

    return pool_health(item, parent, NOW)


@pytest.mark.parametrize("pace,band", [
    (-PLAN_SPEND_PACE_PT, "spend"),
    (PLAN_REST_PACE_PT, "ok"),
    (PLAN_REST_PACE_PT + OVER_BOUNDARY, "rest"),
])
def test_pace_band_uses_unrounded_shared_projection(pace, band):
    item = reading(HALF, elapsed=WEEK * (HALF - pace / 100))
    facts = health(item)
    assert facts["pace_pt"] == pytest.approx(pace, abs=1e-9)
    assert facts["pace_band"] == band
    assert facts["even_left_pct"] == pytest.approx(facts["left_pct"] + facts["pace_pt"])


def test_missing_pace_leaves_known_reserve_and_unavailable_projection():
    item = reading(HALF)
    del item["payload"]["window_started_at"]
    facts = health(item)
    assert facts["state"] == facts["reserve"] == "ok"
    for field in ("pace_band", "pace_pt", "even_left_pct", "exhaust_in_seconds", "exhaust_margin_seconds"):
        assert facts[field] is None
    assert facts["reset_in_seconds"] is not None and facts["measured"]


@pytest.mark.parametrize("age,ttl,stored_state,state,reserve", [
    (FRESH_AGE, QUOTA_TTL_SECONDS, "MEASURED", "out", "out"),
    (QUOTA_TTL_SECONDS, QUOTA_TTL_SECONDS, "MEASURED", "aged", "low"),
    (FRESH_AGE, None, "STALE", "aged", "low"),
])
def test_zero_is_out_only_when_fresh(age, ttl, stored_state, state, reserve):
    facts = health({**reading(0, age=age, state=stored_state), "ttl_seconds": ttl})
    assert (facts["state"], facts["reserve"]) == (state, reserve)
    assert facts["stale"] is (state == "aged")
    assert facts["exhaust_before_reset"] is False
    assert facts["exhaust_margin_seconds"] is None


@pytest.mark.parametrize("age", [FRESH_AGE, QUOTA_TTL_SECONDS])
def test_zero_from_the_previous_window_is_reset(age):
    facts = health(reading(0, elapsed=WEEK - age / 2, age=age))
    assert facts["state"] == "reset" and facts["reserve"] is None
    assert facts["reset_in_seconds"] == -age / 2
    assert facts["exhaust_in_seconds"] is None
    assert facts["exhaust_margin_seconds"] is None


def test_state_precedence_keeps_low_and_fast_ahead_of_aged_or_ok():
    low = reading(LOW_QUOTA_FRACTION, age=QUOTA_TTL_SECONDS)
    fast = {**reading(HALF, elapsed=WEEK / 3), "projection_samples": 2}
    assert health(low)["state"] == "low" and health(low)["stale"]
    assert health(fast)["state"] == "fast"
    assert health(reading(None, state="ABSENT", reason="not reported by provider"))["state"] == "n/a"
    assert health(reading(None, state="UNKNOWN"))["state"] == "?"


def test_exhaustion_reuses_projection_guard_and_compares_to_reset():
    from peerhub.extensions.diag_quota import projection

    guarded = health(reading(.90, elapsed=WEEK * MIN_ELAPSED_LONG - 1))
    assert guarded["exhaust_in_seconds"] is None and not guarded["exhaust_before_reset"]
    assert guarded["exhaust_margin_seconds"] is None
    tied = health(reading(HALF))
    assert tied["exhaust_in_seconds"] == tied["reset_in_seconds"]
    assert not tied["exhaust_before_reset"]
    assert tied["exhaust_margin_seconds"] == 0
    early_item = reading(HALF, elapsed=WEEK / 3)
    early = health(early_item)
    assert early["exhaust_in_seconds"] == projection(early_item, NOW)["exhaustion_at"] - NOW
    assert early["exhaust_before_reset"]
    assert early["exhaust_margin_seconds"] == -WEEK / 3


@pytest.mark.parametrize("margin", [-HOUR, HOUR, -0.5, 0.5])
def test_exhaustion_margin_preserves_sign_and_precision(margin):
    facts = health(reading(HALF, elapsed=(WEEK + margin) / 2))
    assert facts["exhaust_margin_seconds"] == margin
    assert facts["exhaust_before_reset"] is (margin < 0)


def test_exhaustion_margin_requires_a_fresh_forecast_and_reset():
    missing_reset = reading(HALF)
    del missing_reset["payload"]["resets_at"]
    for item in (missing_reset, reading(HALF, age=QUOTA_TTL_SECONDS)):
        facts = health(item)
        assert facts["exhaust_margin_seconds"] is None
        assert not facts["exhaust_before_reset"]


def test_positive_child_is_blocked_by_its_fresh_exhausted_parent():
    child = _item("cc", "F-7D", CHILD_LEFT, span=WEEK, elapsed=WEEK * (1 - CHILD_LEFT))
    parent = reading(0, elapsed=WEEK * 3 / 4)
    facts = health(child, parent)
    assert facts["state"] == "blocked" and facts["reserve"] == "out"
    assert facts["own_left_pct"] == pytest.approx(CHILD_LEFT * 100)
    assert facts["left_pct"] == 0
    assert facts["limited_by"] == parent["resource_pool_ref"]
    assert facts["pace_pt"] == health(parent)["pace_pt"]
    assert facts["reset_in_seconds"] == health(parent)["reset_in_seconds"]
    assert health(child, {**parent, "age_seconds": QUOTA_TTL_SECONDS}) == health(child)


def test_parent_forecast_is_compared_to_the_nearer_reset():
    child = _item("cc", "F-7D", CHILD_LEFT, span=WEEK, elapsed=WEEK - HOUR)
    parent = reading(LOW_QUOTA_FRACTION)
    facts = health(child, parent)
    assert facts["state"] == "low"
    assert facts["pace_pt"] == health(parent)["pace_pt"]
    assert facts["reset_in_seconds"] == health(child)["reset_in_seconds"]
    assert facts["exhaust_in_seconds"] == health(parent)["exhaust_in_seconds"]
    assert not facts["exhaust_before_reset"]
    assert facts["exhaust_margin_seconds"] == facts["exhaust_in_seconds"] - facts["reset_in_seconds"]
    assert facts["exhaust_margin_seconds"] > 0 > health(parent)["exhaust_margin_seconds"]


def test_relative_reset_is_anchored_at_observation_time():
    item = reading(HALF, age=FRESH_AGE)
    del item["payload"]["resets_at"]
    item["payload"]["reset_in_seconds"] = HOUR
    facts = health(item)
    assert facts["reset_in_seconds"] == HOUR - FRESH_AGE
    assert facts["pace_pt"] is not None


@pytest.mark.parametrize("baseline", [
    None,
    {"pace_pt": None, "measured": True, "stale": False},
    {"pace_pt": 2, "measured": False, "stale": False},
    {"pace_pt": 2, "measured": True, "stale": True},
])
def test_gaps_require_a_fresh_measured_baseline(baseline: Mapping[str, object] | None):
    from peerhub.extensions.pool_health import pace_gaps

    pools = {"C-7D": health(reading(HALF))}
    if baseline is not None:
        pools["X-7D"] = {**pools["C-7D"], **baseline}
    assert pace_gaps(pools)["baseline"] is None
    assert all(value is None for value in pace_gaps(pools)["pools"].values())


def test_gaps_use_each_weekly_parent_and_keep_fixed_order():
    from peerhub.extensions.pool_health import pace_gaps

    base = health(reading(HALF, elapsed=WEEK * .47777))
    other = health(reading(HALF, elapsed=WEEK * .45555))
    gaps = pace_gaps({
        "A-7D": {**other, "stale": True}, "G-5H": base, "F-7D": base, "G-7D": base,
        "X-7D": base, "C-7D": other,
    })
    assert gaps["baseline"] == "X-7D"
    assert list(gaps["pools"]) == ["C-7D", "G-7D", "A-7D", "3P-7D"]
    assert list(gaps["pools"].values()) == [other["pace_pt"] - base["pace_pt"], 0, None, None]
