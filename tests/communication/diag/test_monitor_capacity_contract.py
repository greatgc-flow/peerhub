"""Capacity monitor frames preserve immutable, pool-scoped observation evidence."""
from __future__ import annotations

import json
import sqlite3

import pytest

from peerhub.extensions.monitor_read_model import render_monitor_frame
from peerhub.extensions.observation_model import EvidenceState, epoch_to_iso
from tests.communication.fakes.observation import FakeObservationSource, absent, measured, unknown
from tests.communication.harness.observation import T0, ObservationHarness

pytestmark = [pytest.mark.unit, pytest.mark.diag, pytest.mark.platform_sensitive]


def _pool(pool_id: str, provider: str) -> dict:
    return {"schema_version": "1.0", "resource_pool_id": pool_id,
            "provider": provider, "kind": "QUOTA", "metadata": {}}


def _capacity_frame(harness: ObservationHarness, read_at: float) -> dict:
    frame = render_monitor_frame(harness.db_path, ["capacity"], read_at=read_at)
    assert frame["groups"]["capacity"]["status"] == "OK", frame
    return frame["groups"]["capacity"]["data"]


def test_capacity_preserves_pool_source_times_and_allowlisted_measurements(tmp_path):
    h = ObservationHarness(tmp_path / "workspace")
    h.register_pool(_pool("openai-weekly", "openai"))
    h.register_pool(_pool("anthropic-session", "anthropic"))
    h.capture("peer:cx", "quota", FakeObservationSource(measured({
        "remaining": 4200,
        "remaining_fraction": 0.42,
        "used": 5800,
        "limit": 10000,
        "reset_in_seconds": 7200,
        "response": "must-not-leak",
        "vendor_account_token": "secret-must-not-leak",
    }, observed_at=epoch_to_iso(T0 - 2))), resource_pool_ref="openai-weekly")
    h.capture("peer:cx", "quota", FakeObservationSource(measured({
        "remaining_requests": 17,
        "requests_per_minute": 30,
        "retry_after_seconds": 15,
    }, observed_at=epoch_to_iso(T0 - 1))), resource_pool_ref="anthropic-session")
    with sqlite3.connect(h.db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM observations WHERE kind='quota'").fetchone()[0] == 2
        assert conn.execute("SELECT COUNT(*) FROM observation_rollups WHERE kind='quota'").fetchone()[0] == 2
        assert conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='trigger' AND name='observations_no_update'").fetchone()[0] == 1

    rows = _capacity_frame(h, T0 + 10)["observations"]
    by_pool = {row["pool_ref"]: row for row in rows}
    assert set(by_pool) == {"openai-weekly", "anthropic-session"}

    openai = by_pool["openai-weekly"]
    required_fields = {
        "peer_id", "provider", "pool_kind", "kind", "pool_ref", "source", "state", "stored_state",
        "observed_at", "captured_at", "effective_at_us", "age_seconds", "ttl_seconds", "freshness_basis",
        "measurements",
    }
    assert required_fields <= openai.keys(), f"capacity fields missing: {sorted(required_fields - openai.keys())}"
    assert openai["provider"] == "openai"
    assert openai["pool_kind"] == "QUOTA"
    assert openai["source"] == "fake-source"
    assert openai["state"] == openai["stored_state"] == EvidenceState.MEASURED.value
    assert openai["observed_at"] == epoch_to_iso(T0 - 2)
    assert openai["captured_at"] == epoch_to_iso(T0)
    assert openai["effective_at_us"] == round(T0 * 1_000_000)
    assert openai["age_seconds"] == pytest.approx(10)
    assert openai["freshness_basis"] == "captured_at"
    assert openai["measurements"] == {
        "remaining": 4200,
        "remaining_fraction": 0.42,
        "used": 5800,
        "limit": 10000,
        "reset_in_seconds": 7200,
    }

    anthropic = by_pool["anthropic-session"]
    assert anthropic["provider"] == "anthropic"
    assert anthropic["pool_kind"] == "QUOTA"
    assert anthropic["measurements"] == {
        "remaining_requests": 17,
        "requests_per_minute": 30,
        "retry_after_seconds": 15,
    }
    serialized = json.dumps(rows, sort_keys=True)
    assert "must-not-leak" not in serialized
    assert "secret-must-not-leak" not in serialized


def test_capacity_freshness_matches_diag_and_stale_values_are_not_current(tmp_path):
    h = ObservationHarness(tmp_path / "workspace")
    h.register_pool(_pool("pool-measured", "openai"))
    h.capture("peer:cx", "quota", FakeObservationSource(measured({"remaining": 9, "limit": 10})),
              resource_pool_ref="pool-measured")
    stale = {row["pool_ref"]: row for row in _capacity_frame(h, T0 + 300)["observations"]}
    assert stale["pool-measured"]["state"] == h.latest("peer:cx", "quota", T0 + 300,
                                                         "pool-measured").state.value
    assert stale["pool-measured"]["state"] == EvidenceState.STALE.value
    assert stale["pool-measured"]["stored_state"] == EvidenceState.MEASURED.value
    assert stale["pool-measured"]["measurements"] is None


@pytest.mark.parametrize(("source", "state"), [(absent(), EvidenceState.ABSENT), (unknown(), EvidenceState.UNKNOWN)])
def test_capacity_absent_and_unknown_have_no_measurements(tmp_path, source, state):
    h = ObservationHarness(tmp_path / "workspace")
    pool_ref = f"pool-{state.value.lower()}"
    h.register_pool(_pool(pool_ref, "openai"))
    observation = h.capture("peer:cx", "quota", FakeObservationSource(source), resource_pool_ref=pool_ref)
    assert observation.state is state

    row = _capacity_frame(h, T0 + 10)["observations"][0]
    assert row["state"] == state.value
    assert row["measurements"] is None


def test_capacity_never_allocates_pool_evidence_to_sessions(tmp_path):
    h = ObservationHarness(tmp_path / "workspace")
    h.register_pool(_pool("shared-account", "openai"))
    h.capture("peer:cx", "quota", FakeObservationSource(measured({"remaining": 3})),
              resource_pool_ref="shared-account")

    frame = render_monitor_frame(h.db_path, ["capacity", "sessions"], read_at=T0 + 1)
    capacity = frame["groups"]["capacity"]["data"]
    assert capacity["observations"][0]["pool_ref"] == "shared-account"
    assert capacity["observations"][0]["attribution_scope"] == "peer_account_pool"
    assert "sessions" not in capacity
    assert all(not any("session" in key for key in row) for row in capacity["observations"])
    assert "session_allocations" not in frame["groups"]["capacity"]["data"]


def test_capacity_includes_pool_scoped_rate_limit_observations(tmp_path):
    h = ObservationHarness(tmp_path / "workspace")
    h.register_pool(_pool("request-window", "openai"))
    h.capture("peer:ag", "rate_limit", FakeObservationSource(measured({
        "remaining_requests": 12, "requests_per_minute": 30, "retry_after_seconds": 3,
    })), resource_pool_ref="request-window")

    rows = _capacity_frame(h, T0 + 1)["observations"]
    assert len(rows) == 1
    assert rows[0]["kind"] == "rate_limit"
    assert rows[0]["pool_ref"] == "request-window"
    assert rows[0]["measurements"] == {
        "remaining_requests": 12, "requests_per_minute": 30, "retry_after_seconds": 3,
    }
