import pytest
import time
from peerhub.telemetry.presenter import DisplayTier, format_headroom_surface
from peerhub.telemetry.contract import UsageProjectionSnapshot

def make_snapshot(used=0.0, remaining=1.0, updated_at=None):
    now = int(time.time())
    return UsageProjectionSnapshot(
        projection_id="proj-1",
        instance_id="ag",
        profile_id="ag.deepthink",
        quota_pool_scope="G-5H",
        used_fraction=used,
        remaining_fraction=remaining,
        window_started_at=now - 3600,
        resets_at=now + 14400,
        revision=1,
        updated_at=updated_at or now
    )

def test_th_01_no_telemetry_data():
    result = format_headroom_surface(None, tier=DisplayTier.BASIC)
    assert result == "No telemetry data"

def test_th_02_data_for_one_peer_only():
    # Only "ag" has data, "cc" and "cx" are missing
    input_data = {"ag": make_snapshot()}
    result = format_headroom_surface(input_data, tier=DisplayTier.BASIC)
    # The exact string would depend on the format, but it should not be "No telemetry data"
    assert "No telemetry data" not in result

def test_th_03_stale_data():
    now = int(time.time())
    # 5 minutes ago
    stale_time = now - 300
    input_data = {"ag": make_snapshot(updated_at=stale_time)}
    result = format_headroom_surface(input_data, tier=DisplayTier.FULL)
    assert "[stale: 5m ago]" in result

def test_th_04_vendor_api_error():
    input_data = {"error": "vendor_api_timeout"}
    result = format_headroom_surface(input_data, tier=DisplayTier.BASIC)
    assert "vendor error" in result.lower()

def test_th_05_fail_rate_zero_dispatches():
    input_data = {"ag": make_snapshot(), "dispatches": 0, "failures": 0}
    result = format_headroom_surface(input_data, tier=DisplayTier.FULL)
    assert "fail rate: no dispatches" in result

def test_th_06_trend_one_data_point():
    input_data = {"ag": make_snapshot(), "data_points": 1}
    result = format_headroom_surface(input_data, tier=DisplayTier.FULL)
    assert "trend: insufficient data" in result

def test_th_07_telemetry_tracking_not_populated():
    input_data = {"tracking_populated": False}
    result = format_headroom_surface(input_data, tier=DisplayTier.BASIC)
    assert "not populated" in result.lower()

def test_th_08_rate_limit_window_changed():
    input_data = {"ag": make_snapshot(), "window_changed": True}
    result = format_headroom_surface(input_data, tier=DisplayTier.FULL)
    assert "window changed" in result.lower()

def test_th_09_malformed_input():
    input_data = {"ag": {"invalid_key": "bad_value"}}
    result = format_headroom_surface(input_data, tier=DisplayTier.BASIC)
    assert result == "No telemetry data"



# ---- render_headroom (policy-tiered surface with 24h reliability) ----
from peerhub.telemetry.presenter import render_headroom  # noqa: E402
from peerhub.telemetry.reliability import Reliability24h  # noqa: E402


def test_none_tier_renders_nothing_and_absence_is_stated_not_zero():
    assert render_headroom([], {}, tier=DisplayTier.NONE) == ""
    assert render_headroom([], {}, tier=DisplayTier.BASIC) == "No telemetry data"


def test_full_tier_reports_reliability_counts_exclusions_and_no_dispatch_honestly():
    reliability = {
        ("cx", "cx.standard"): Reliability24h(8, 2, 1, 1, 0, 1000),
        ("cx", "cx.effort"): Reliability24h(0, 0, 0, 0, 0, 1000),
    }
    out = render_headroom([], reliability, tier=DisplayTier.FULL, now=1000)
    assert "cx.standard: 8 ok / 2 failed, fail rate 20%" in out
    assert "1 cancelled, 1 unknown, 0 pre-admission" in out and "[partial coverage]" in out
    assert "cx.effort: fail rate: no definitive attempts" in out
    effort_line = next(l for l in out.splitlines() if "cx.effort" in l)
    assert "0%" not in effort_line
    assert "24h reliability" not in render_headroom([], reliability, tier=DisplayTier.BASIC, now=1000)
