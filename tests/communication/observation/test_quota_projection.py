"""Fixed-clock boundary tests for pure quota exhaustion projection."""

import math
import pytest

from peerhub.extensions.quota_projection import project_exhaustion


def test_min_elapsed_boundary():
    t_start = 1000.0
    t_reset = 5000.0
    now = 2000.0
    u = 0.5

    # 59 seconds elapsed (< 60s minimum)
    r_under = project_exhaustion(u, t_start, t_start + 59.0, t_reset, now)
    assert r_under["status"] == "unavailable"
    assert r_under["basis"] == "window_average"
    assert r_under["exhaustion_at"] is None
    assert r_under["exhausts_before_reset"] is None
    assert "60s minimum" in r_under["reason"]

    # Exactly 60 seconds elapsed (>= 60s minimum)
    r_at = project_exhaustion(u, t_start, t_start + 60.0, t_reset, now)
    assert r_at["status"] in ("ok", "safe_until_reset")
    assert r_at["exhaustion_at"] == pytest.approx(t_start + 60.0 / 0.5)  # 1120.0
    assert r_at["reason"] is None


def test_zero_usage_is_idle():
    t_start = 1000.0
    t_meas = 2000.0
    t_reset = 5000.0
    now = 2000.0

    res = project_exhaustion(0.0, t_start, t_meas, t_reset, now)
    assert res["status"] == "idle"
    assert res["basis"] == "window_average"
    assert res["measured_at"] == t_meas
    assert res["exhaustion_at"] is None
    assert res["exhausts_before_reset"] is False
    assert res["reason"] is None


def test_exhausted_usage():
    t_start = 1000.0
    t_meas = 2000.0
    t_reset = 5000.0
    now = 2000.0

    res = project_exhaustion(1.0, t_start, t_meas, t_reset, now)
    assert res["status"] == "exhausted"
    assert res["basis"] == "window_average"
    assert res["measured_at"] == t_meas
    assert res["exhaustion_at"] == t_meas
    assert res["exhausts_before_reset"] is True
    assert res["reason"] is None


def test_safe_until_reset():
    t_start = 0.0
    t_meas = 3600.0
    t_reset = 7200.0
    now = 3600.0
    # At u = 0.2, elapsed = 3600s, window_start + elapsed / u = 0 + 3600 / 0.2 = 18000s > 7200s
    res = project_exhaustion(0.2, t_start, t_meas, t_reset, now)
    assert res["status"] == "safe_until_reset"
    assert res["basis"] == "window_average"
    assert res["measured_at"] == t_meas
    assert res["exhaustion_at"] == 18000.0
    assert res["exhausts_before_reset"] is False
    assert res["reason"] is None


def test_exhausts_before_reset():
    t_start = 0.0
    t_meas = 3600.0
    t_reset = 7200.0
    now = 3600.0
    # At u = 0.8, elapsed = 3600s, window_start + elapsed / u = 0 + 3600 / 0.8 = 4500s < 7200s
    res = project_exhaustion(0.8, t_start, t_meas, t_reset, now)
    assert res["status"] == "ok"
    assert res["basis"] == "window_average"
    assert res["measured_at"] == t_meas
    assert res["exhaustion_at"] == 4500.0
    assert res["exhausts_before_reset"] is True
    assert res["reason"] is None


@pytest.mark.parametrize("u,start,meas,reset", [
    (-0.1, 0, 100, 200),
    (1.1, 0, 100, 200),
    (math.nan, 0, 100, 200),
    (math.inf, 0, 100, 200),
    (True, 0, 100, 200),  # bool is not accepted as float
    (None, 0, 100, 200),
    (0.5, 100, 50, 200),  # start > meas
    (0.5, 0, 200, 200),   # meas >= reset
    (0.5, 0, 300, 200),   # meas > reset
    (0.5, 0, 100, math.nan),
    (0.5, math.nan, 100, 200),
])
def test_invalid_bounds_and_types_return_unavailable(u, start, meas, reset):
    res = project_exhaustion(u, start, meas, reset, 100.0)
    assert res["status"] == "unavailable"
    assert res["exhaustion_at"] is None
    assert res["exhausts_before_reset"] is None
    assert isinstance(res["reason"], str) and res["reason"]


def test_recent_vs_window_average():
    t_start = 0.0
    t_meas = 3600.0
    t_reset = 10000.0
    now = 3600.0
    u_current = 0.4

    # 1. Without recent samples -> window average: rate = 0.4 / 3600 => exhaustion_at = 9000.0 (< 10000.0)
    avg_res = project_exhaustion(u_current, t_start, t_meas, t_reset, now, recent_samples=None)
    assert avg_res["status"] == "ok"
    assert avg_res["basis"] == "window_average"
    assert avg_res["exhaustion_at"] == pytest.approx(9000.0)

    # 2. With recent samples surging in last 10 minutes:
    # At t=3000 (within 30m of 3600): u=0.1
    # At t=3600: u=0.4
    # dt = 600, du = 0.3 => rate = 0.3 / 600 = 0.0005
    # remaining = 1.0 - 0.4 = 0.6 => time to exhaust = 0.6 / 0.0005 = 1200s => exhaustion_at = 3600 + 1200 = 4800.0
    recent_samples = [(3000.0, 0.1), (3600.0, 0.4)]
    rec_res = project_exhaustion(u_current, t_start, t_meas, t_reset, now, recent_samples=recent_samples)
    assert rec_res["status"] == "ok"
    assert rec_res["basis"] == "recent_rate"
    assert rec_res["exhaustion_at"] == pytest.approx(4800.0)
    assert rec_res["exhausts_before_reset"] is True

    # 3. With samples older than 30 minutes (e.g. t=1000, which is > 1800s before 3600s)
    # Only 1 sample in recent 30 min window (current measurement) -> falls back to window average
    old_samples = [(1000.0, 0.1)]
    fallback_res = project_exhaustion(u_current, t_start, t_meas, t_reset, now, recent_samples=old_samples)
    assert fallback_res["basis"] == "window_average"
    assert fallback_res["exhaustion_at"] == pytest.approx(9000.0)

    # 4. With flat recent usage (du = 0) -> falls back to window average
    flat_samples = [(3200.0, 0.4), (3600.0, 0.4)]
    flat_res = project_exhaustion(u_current, t_start, t_meas, t_reset, now, recent_samples=flat_samples)
    assert flat_res["basis"] == "window_average"
    assert flat_res["exhaustion_at"] == pytest.approx(9000.0)
