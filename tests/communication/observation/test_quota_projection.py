"""Deterministic quota policy fixtures; these do not establish predictive accuracy."""

import inspect
import math

import pytest

from peerhub.extensions.quota_projection import project_exhaustion

pytestmark = [pytest.mark.unit, pytest.mark.observation]
FIELDS = {"pace_pt", "projected_at_reset", "state", "exhaustion_at", "measured_at", "anchor_basis", "reason"}


def project(u, measured, *, now=None, state="MEASURED", start=0.0, reset=1000.0, **anchors):
    return project_exhaustion(u, start, measured, reset, measured if now is None else now,
                              evidence_state=state, **{"anchor_basis": "provider_fixed_window",
                                                       "anchor_validated": True, **anchors})


def assert_suppressed(result, state):
    assert set(result) == FIELDS
    assert result["state"] == state
    assert all(result[k] is None for k in ("pace_pt", "projected_at_reset", "exhaustion_at"))
    assert result["reason"]


@pytest.mark.parametrize("used,measured,state", [
    (0.05, 99.999, "insufficient"),
    (0.05, 100.0, "on_pace"),
    (0.049999, 100.0, "insufficient"),
    (0.050001, 100.001, "on_pace"),
    (0.104999, 100.0, "on_pace"),
    (0.105, 100.0, "threat"),
    (0.105001, 100.0, "threat"),
])
def test_support_and_threat_boundaries(used, measured, state):
    result = project(used, measured)
    assert set(result) == FIELDS
    assert result["state"] == state
    if state == "insufficient":
        assert_suppressed(result, state)
    else:
        assert result["reason"] is None


def test_supported_values_and_public_contract():
    assert project(0.4, 200.0) == {
        "pace_pt": 20.0, "projected_at_reset": 200.0, "state": "threat",
        "exhaustion_at": 500.0, "measured_at": 200.0,
        "anchor_basis": "provider_fixed_window", "reason": None,
    }
    on_pace = project(0.2, 500.0)
    assert on_pace["pace_pt"] == -30.0
    assert on_pace["projected_at_reset"] == 40.0
    assert on_pace["exhaustion_at"] == 2500.0
    assert on_pace["state"] == "on_pace"
    assert "recent_samples" not in inspect.signature(project_exhaustion).parameters


def test_owner_one_percent_at_79_seconds():
    # Five-hour window; later read time must not replace measurement time.
    result = project(0.01, 79.0, now=120.0, reset=18000.0)
    assert_suppressed(result, "insufficient")
    assert result["measured_at"] == 79.0


@pytest.mark.parametrize("used,measured", [(0.0, 500.0), (0.0, 0.0), (0.5, 0.0)])
def test_zero_usage_and_zero_elapsed_are_insufficient(used, measured):
    assert_suppressed(project(used, measured), "insufficient")


def test_quantised_one_percent_trace():
    for measured in (79.0, 100.0, 200.0, 500.0):
        assert_suppressed(project(0.01, measured), "insufficient")


def test_burst_trace_uses_only_window_average():
    results = [project(u, t) for u, t in [(0.01, 10.0), (0.4, 100.0), (0.4, 400.0)]]
    assert [r["state"] for r in results] == ["insufficient", "threat", "on_pace"]
    assert results[1]["exhaustion_at"] == 250.0
    assert results[2]["exhaustion_at"] == 1000.0


def test_idle_redraw_trace_preserves_measurement_values_then_expires():
    results = [project(0.4, 200.0, now=t) for t in (200.0, 300.0, 999.0)]
    assert results[0] == results[1] == results[2]
    assert_suppressed(project(0.4, 200.0, now=1000.0), "expired")
    # New window starts fresh; old usage cannot cross the reset.
    assert_suppressed(project(0.01, 1079.0, start=1000.0, reset=2000.0), "insufficient")


@pytest.mark.parametrize("elapsed", [0.0, 0.001, 1.0, 99.999])
def test_fresh_exhaustion_precedes_support_gates(elapsed):
    result = project(1.0, elapsed)
    assert result["state"] == "exhausted"
    assert result["exhaustion_at"] == elapsed
    assert result["pace_pt"] is None and result["projected_at_reset"] is None
    assert result["reason"] is None


def test_fresh_exhaustion_is_observed_even_without_an_anchor():
    result = project_exhaustion(1.0, None, 1.0, None, 1.0)
    assert result["state"] == "exhausted"
    assert result["exhaustion_at"] == 1.0


@pytest.mark.parametrize("used", [0.4, 1.0])
def test_stale_and_expired_suppress_even_exhaustion(used):
    assert_suppressed(project(used, 200.0, now=500.0, state="STALE"), "stale")
    assert_suppressed(project(used, 200.0, now=1000.0, state="STALE"), "expired")


@pytest.mark.parametrize("state", ["UNKNOWN", "ERROR", "ABSENT", "UNAVAILABLE", None])
def test_nonmeasured_evidence_never_forecasts(state):
    assert_suppressed(project(1.0, 200.0, state=state), "unknown")


@pytest.mark.parametrize("anchors", [
    {"anchor_basis": None}, {"anchor_basis": ""}, {"anchor_basis": " "},
    {"anchor_validated": False}, {"anchor_validated": None}, {"anchor_validated": 1},
])
def test_unknown_or_unvalidated_anchors(anchors):
    assert_suppressed(project(0.4, 200.0, **anchors), "unknown")


@pytest.mark.parametrize("used,start,measured,reset,now", [
    (-0.1, 0, 100, 1000, 100), (1.1, 0, 100, 1000, 100),
    (math.nan, 0, 100, 1000, 100), (math.inf, 0, 100, 1000, 100),
    (True, 0, 100, 1000, 100), (None, 0, 100, 1000, 100),
    (0.5, 200, 100, 1000, 100), (1.0, 200, 100, 1000, 100),
    (0.5, 0, 1000, 1000, 1000), (1.0, 0, 1000, 1000, 1000),
    (0.5, 0, 100, math.nan, 100), (0.5, math.inf, 100, 1000, 100),
    (0.5, 0, math.nan, 1000, 100), (0.5, 0, 100, 1000, math.inf),
    (0.5, 0, 100, 1000, None), (0.5, 0, 100, 1000, True),
    (0.5, 0, 100, 1000, 99), (0.5, -1e308, 0, 1e308, 0),
    (1.0, math.nan, 1, 1000, 1), (1.0, 0, 1, math.inf, 1),
    (0.5, True, 100, 1000, 100), (0.5, 0, True, 1000, 100),
    (0.5, 0, 100, True, 100), (10**400, 0, 100, 1000, 100),
    (0.5, 0, 100, 1000, 10**400),
])
def test_invalid_measurements_clocks_and_window_ordering(used, start, measured, reset, now):
    result = project_exhaustion(used, start, measured, reset, now,
                                anchor_basis="provider_fixed_window", anchor_validated=True)
    assert_suppressed(result, "unknown")
