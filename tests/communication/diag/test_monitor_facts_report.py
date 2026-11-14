"""#366 M1b: monitor shows the facts_report of the newest capacity row (highest capture_seq), not the first in query order."""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from peerhub.cli.monitor_view import _compact_footer
from peerhub.cli.monitor_view import _render_expanded_frame as render_grouped_frame
from peerhub.extensions.adapters.profiles import packaged_defaults_meta
from peerhub.extensions.monitor_read_model import render_monitor_frame
from peerhub.extensions.observation import ObservationStore
from peerhub.extensions.observation_model import EvidenceState, ResourcePool, epoch_to_iso
from peerhub.extensions.quota_capture import refresh_quota
from tests.communication.bridge_helpers import bseed
from tests.communication.fakes.observation import FakeObservationSource, measured
from tests.communication.harness.bridge import BridgeHarness

pytestmark = pytest.mark.unit
OK = {"status": "ok", "reasons": [], "checks": {"ag_version": "ok", "cc_version": "ok", "cx_version": "ok"},
      "checked_at": "2026-11-14"}
DRIFT = {"status": "needs_recheck", "reasons": ["ag: version_drift"],
         "checks": {"ag_version": "drift", "cc_version": "ok", "cx_version": "ok"}, "checked_at": "2026-11-10"}


@pytest.fixture
def store(tmp_path):
    harness = BridgeHarness(tmp_path / "workspace")
    bseed(harness, peers=("a", "ag", "cc"), stream="s")
    obs = ObservationStore(harness.db_path, clock=harness.clock.now)
    for pool, provider in (("quota:ag:G-5H", "ag"), ("quota:cc:C-5H", "cc")):  # as refresh_quota registers them
        obs.register_resource_pool(ResourcePool.model_validate(
            {"schema_version": "1.0", "resource_pool_id": pool, "provider": provider, "kind": "QUOTA", "metadata": {}}))
    return SimpleNamespace(harness=harness, obs=obs)


def _capture(store, peer: str, pool: str | None, age_seconds: int, facts: dict[str, Any] | None) -> None:
    payload: dict[str, Any] = {"remaining_fraction": 0.85}
    if facts is not None:
        payload["facts_report"] = facts
    observed_at = epoch_to_iso(store.harness.clock.now() - age_seconds)
    store.obs.capture(peer, "quota", FakeObservationSource(measured(payload, observed_at=observed_at)),
                      resource_pool_ref=pool)


def _facts(store) -> tuple[dict[str, Any], str, str]:
    harness = store.harness
    frame = render_monitor_frame(harness.db_path, ["peers"], read_at=harness.clock.now() + 5, workspace=harness.workspace)
    return (frame["groups"]["peers"]["data"]["facts_report"], render_grouped_frame(frame, view="plain", width=120),
            _compact_footer(frame["groups"])[0])


def test_old_null_pool_drift_does_not_hide_newer_ok_reports(store):
    _capture(store, "ag", None, 300, DRIFT)  # NULL pool sorts first in the capacity query
    _capture(store, "ag", "quota:ag:G-5H", 200, OK)
    _capture(store, "cc", "quota:cc:C-5H", 100, OK)
    report, expanded, compact = _facts(store)
    assert report["status"] == "ok"
    assert "Facts: ok (checked 2026-11-14)" in expanded
    assert "version_drift" not in expanded
    assert compact.startswith("facts ok 2026-11-14")


def test_newer_drift_report_wins_over_older_ok_reports(store):
    newer = dict(DRIFT, checked_at="2026-11-15")
    _capture(store, "ag", "quota:ag:G-5H", 200, OK)
    _capture(store, "cc", "quota:cc:C-5H", 100, newer)
    report, expanded, compact = _facts(store)
    assert report["status"] == "needs_recheck"
    assert "Facts: needs_recheck (ag: version_drift)" in expanded
    assert compact.startswith("facts needs_recheck 2026-11-15")


def test_no_capacity_row_with_report_keeps_the_fallback(store):
    _capture(store, "ag", "quota:ag:G-5H", 200, DRIFT)
    _capture(store, "ag", "quota:ag:G-5H", 100, None)  # supersedes the reported row in the capacity read
    report, expanded, _ = _facts(store)
    assert report == DRIFT
    assert "Facts: needs_recheck (ag: version_drift)" in expanded


def test_newest_report_from_a_cc_only_refresh_still_carries_cx_drift(store):
    observed = packaged_defaults_meta()["observed_versions"]

    def reading(scope: str):
        value = SimpleNamespace(quota_pool_scope=scope, remaining_fraction=0.8, window_started_at=100, resets_at=18100)
        evidence = SimpleNamespace(state=EvidenceState.MEASURED, value=value, evidence_ref="probe", source_tag="fake",
                                   observed_at=100, captured_at=100)
        return SimpleNamespace(evidence=evidence, extra={})

    db = store.harness.db_path
    refresh_quota(db, ["cx"], pollers={"cx": lambda **kw: [reading("X-5H")]},
                  version_checker=lambda kind: observed["cx"] + ".drift")
    result = refresh_quota(db, ["cc"], pollers={"cc": lambda **kw: [reading("C-5H")]},
                           version_checker=lambda kind: observed["cc"])
    assert result["observations"][0]["payload"]["facts_report"]["checks"]["cc_version"] == "ok"
    report, expanded, _ = _facts(store)
    assert report["checks"]["cx_version"] == "drift"
    assert "Facts: needs_recheck (cx: version_drift)" in expanded
