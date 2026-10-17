"""Unit and e2e tests for Slice 3: Quota refresh drift report.

Tests keep-unknown-agy-buckets, CLI version comparison vs observed_versions,
persistence in existing quota observation payload, and preservation of prior drift
on partial/failed refresh.
"""
from __future__ import annotations

import sqlite3
import json
from types import SimpleNamespace
from pathlib import Path
from typing import Any

import pytest

from peerhub.extensions.observation_model import EvidenceState
from peerhub.extensions.quota_capture import refresh_quota
from peerhub.extensions.quota_probes import _parse_agy_usage_output
from peerhub.core.store import CoreStore

pytestmark = [pytest.mark.unit]


def test_parse_agy_usage_keeps_unknown_bucket_ids():
    """S3: _parse_agy_usage_output keeps unknown agy bucket IDs instead of dropping them."""
    payload = json.dumps({
        "command": {
            "name": "usage",
            "data": {
                "groups": [
                    {
                        "buckets": [
                            {"id": "gemini-5h", "remaining_fraction": 0.85, "reset_time": "2026-10-17T15:00:00Z"},
                            {"id": "gemini-experimental-custom", "remaining_fraction": 0.50, "reset_time": "2026-10-17T15:00:00Z"},
                        ]
                    }
                ]
            }
        }
    })
    parsed = _parse_agy_usage_output(payload)
    assert "gemini-5h" in parsed
    assert "gemini-experimental-custom" in parsed
    assert parsed["gemini-experimental-custom"]["remaining_fraction"] == 0.50


def test_refresh_quota_attaches_facts_report_to_observation(tmp_path):
    """S3: refresh_quota persists ONE bounded facts_report in quota observation payload."""
    db = tmp_path / "core.db"
    CoreStore(db)

    value = SimpleNamespace(quota_pool_scope="G-5H", remaining_fraction=0.80,
                            window_started_at=100, resets_at=18100)
    evidence = SimpleNamespace(state=EvidenceState.MEASURED, value=value, evidence_ref="probe:ag",
                               source_tag="fake_usage", observed_at=100, captured_at=100)
    reading = SimpleNamespace(evidence=evidence, extra={"unknown_pools": []})

    result = refresh_quota(
        db, ["ag"],
        pollers={"ag": lambda **kw: [reading]},
        version_checker=lambda kind: "1.4.0",
    )
    assert result["status"] == "OK"
    obs = result["observations"][0]
    payload = obs["payload"]
    assert "facts_report" in payload

    report = payload["facts_report"]
    assert report["status"] == "ok"
    assert report["reasons"] == []
    assert report["checks"]["ag_pools"] == "ok"
    assert report["checks"]["ag_version"] == "ok"


def test_refresh_quota_detects_unknown_pool_drift(tmp_path):
    """S3: Unknown agy bucket ID causes facts_report status needs_recheck with ag: unknown_pool."""
    db = tmp_path / "core.db"
    CoreStore(db)

    value = SimpleNamespace(quota_pool_scope="G-5H", remaining_fraction=0.80,
                            window_started_at=100, resets_at=18100)
    evidence = SimpleNamespace(state=EvidenceState.MEASURED, value=value, evidence_ref="probe:ag",
                               source_tag="fake_usage", observed_at=100, captured_at=100)
    # Probe flagged an unknown pool
    reading = SimpleNamespace(evidence=evidence, extra={"unknown_pools": ["gemini-experimental"]})

    result = refresh_quota(
        db, ["ag"],
        pollers={"ag": lambda **kw: [reading]},
        version_checker=lambda kind: "1.4.0",
    )
    obs = result["observations"][0]
    report = obs["payload"]["facts_report"]
    assert report["status"] == "needs_recheck"
    assert "ag: unknown_pool" in report["reasons"]
    assert report["checks"]["ag_pools"] == "drift"


def test_refresh_quota_detects_cli_version_drift(tmp_path):
    """S3: Discovered CLI version mismatch against observed_versions causes needs_recheck."""
    db = tmp_path / "core.db"
    CoreStore(db)

    value = SimpleNamespace(quota_pool_scope="X-5H", remaining_fraction=0.80,
                            window_started_at=100, resets_at=18100)
    evidence = SimpleNamespace(state=EvidenceState.MEASURED, value=value, evidence_ref="probe:cx",
                               source_tag="fake_usage", observed_at=100, captured_at=100)
    reading = SimpleNamespace(evidence=evidence, extra={})

    # Expected codex-cli is 0.163.0; simulate upgraded version 0.164.0
    result = refresh_quota(
        db, ["cx"],
        pollers={"cx": lambda **kw: [reading]},
        version_checker=lambda kind: "0.164.0",
    )
    obs = result["observations"][0]
    report = obs["payload"]["facts_report"]
    assert report["status"] == "needs_recheck"
    assert "cx: version_drift" in report["reasons"]
    assert report["checks"]["cx_version"] == "drift"


def test_refresh_quota_partial_or_failed_refresh_preserves_prior_drift(tmp_path):
    """S3: A partial or failed refresh never clears prior drift."""
    db = tmp_path / "core.db"
    CoreStore(db)

    # Cycle 1: ag has drift
    value_ag = SimpleNamespace(quota_pool_scope="G-5H", remaining_fraction=0.80,
                               window_started_at=100, resets_at=18100)
    ev_ag = SimpleNamespace(state=EvidenceState.MEASURED, value=value_ag, evidence_ref="probe:ag",
                            source_tag="fake_usage", observed_at=100, captured_at=100)
    reading_ag = SimpleNamespace(evidence=ev_ag, extra={"unknown_pools": ["gemini-surprise"]})

    res1 = refresh_quota(
        db, ["ag"],
        pollers={"ag": lambda **kw: [reading_ag]},
        version_checker=lambda kind: "1.4.0",
    )
    assert res1["observations"][0]["payload"]["facts_report"]["status"] == "needs_recheck"

    # Cycle 2: partial refresh for cx only (ag not probed)
    value_cx = SimpleNamespace(quota_pool_scope="X-5H", remaining_fraction=0.90,
                               window_started_at=200, resets_at=18200)
    ev_cx = SimpleNamespace(state=EvidenceState.MEASURED, value=value_cx, evidence_ref="probe:cx",
                            source_tag="fake_usage", observed_at=200, captured_at=200)
    reading_cx = SimpleNamespace(evidence=ev_cx, extra={})

    res2 = refresh_quota(
        db, ["cx"],
        pollers={"cx": lambda **kw: [reading_cx]},
        version_checker=lambda kind: "0.163.0",
    )
    rep2 = res2["observations"][0]["payload"]["facts_report"]
    # ag_pools drift MUST be preserved from prior report!
    assert rep2["status"] == "needs_recheck"
    assert "ag: unknown_pool" in rep2["reasons"]
    assert rep2["checks"]["ag_pools"] == "drift"
    assert rep2["checks"]["cx_version"] == "ok"

    # Cycle 3: clean ag refresh clears the drift
    reading_ag_clean = SimpleNamespace(evidence=ev_ag, extra={"unknown_pools": []})
    res3 = refresh_quota(
        db, ["ag"],
        pollers={"ag": lambda **kw: [reading_ag_clean]},
        version_checker=lambda kind: "1.4.0",
    )
    rep3 = res3["observations"][0]["payload"]["facts_report"]
    assert rep3["status"] == "ok"
    assert "ag: unknown_pool" not in rep3["reasons"]
    assert rep3["checks"]["ag_pools"] == "ok"


def test_refresh_quota_with_peer_alias_agy(tmp_path):
    """S3/CC review: key pool-drift/version checks on kind (aliases agy/claude/codex work)."""
    db = tmp_path / "core.db"
    CoreStore(db)

    value = SimpleNamespace(quota_pool_scope="G-5H", remaining_fraction=0.80,
                            window_started_at=100, resets_at=18100)
    evidence = SimpleNamespace(state=EvidenceState.MEASURED, value=value, evidence_ref="probe:ag",
                               source_tag="fake_usage", observed_at=100, captured_at=100)
    reading = SimpleNamespace(evidence=evidence, extra={"unknown_pools": ["custom-pool-1"]})

    result = refresh_quota(
        db, ["agy"],
        pollers={"ag": lambda **kw: [reading]},
        version_checker=lambda kind: "1.4.0",
    )
    obs = result["observations"][0]
    report = obs["payload"]["facts_report"]
    assert report["status"] == "needs_recheck"
    assert "ag: unknown_pool" in report["reasons"]
    assert report["checks"]["ag_pools"] == "drift"
    assert report["checks"]["ag_version"] == "ok"
    assert report["unknown_pools"] == ["custom-pool-1"]


def test_facts_report_sanitizes_and_caps_unknown_pools(tmp_path):
    """S3/CC review: up to 5 sanitized unknown pool IDs stored in report."""
    db = tmp_path / "core.db"
    CoreStore(db)

    value = SimpleNamespace(quota_pool_scope="G-5H", remaining_fraction=0.80,
                            window_started_at=100, resets_at=18100)
    evidence = SimpleNamespace(state=EvidenceState.MEASURED, value=value, evidence_ref="probe:ag",
                               source_tag="fake_usage", observed_at=100, captured_at=100)
    pools = ["bad pool @ 1", "pool-2", "pool-3", "pool-4", "pool-5", "pool-6", "pool-2"]
    reading = SimpleNamespace(evidence=evidence, extra={"unknown_pools": pools})

    result = refresh_quota(
        db, ["ag"],
        pollers={"ag": lambda **kw: [reading]},
        version_checker=lambda kind: "1.4.0",
    )
    obs = result["observations"][0]
    report = obs["payload"]["facts_report"]
    assert len(report["unknown_pools"]) == 5
    assert "badpool1" in report["unknown_pools"]
    assert "pool-2" in report["unknown_pools"]


def test_facts_report_carries_per_check_date_and_unverified_status(tmp_path):
    """S3/CC review: carry per-check date forward without refreshing checked_at; overall unverified unless >=1 ok."""
    from peerhub.extensions.observation import ObservationStore
    from peerhub.extensions.observation_model import Observation, epoch_to_iso
    import uuid

    db = tmp_path / "core.db"
    CoreStore(db)
    store = ObservationStore(db)

    # Seed an observation with check_dates from earlier
    prior_facts = {
        "status": "ok",
        "reasons": [],
        "checks": {"ag_pools": "ok", "ag_version": "ok", "cc_version": "unknown", "cx_version": "unknown"},
        "check_dates": {"ag_pools": "2026-10-10", "ag_version": "2026-10-10"},
        "checked_at": "2026-10-10",
        "unknown_pools": [],
    }
    store.persist(Observation(
        observation_id=uuid.uuid4().hex,
        subject_ref="ag",
        kind="quota",
        source="seed",
        state=EvidenceState.MEASURED,
        payload={"remaining_fraction": 0.9, "facts_report": prior_facts},
        observed_at="2026-10-10T12:00:00Z",
        captured_at="2026-10-10T12:00:00Z",
    ))

    # Probe cx only (no version returned)
    value_cx = SimpleNamespace(quota_pool_scope="X-5H", remaining_fraction=0.85,
                               window_started_at=200, resets_at=18200)
    ev_cx = SimpleNamespace(state=EvidenceState.MEASURED, value=value_cx, evidence_ref="probe:cx",
                            source_tag="fake", observed_at=200, captured_at=200)
    reading_cx = SimpleNamespace(evidence=ev_cx, extra={})

    res = refresh_quota(
        db, ["cx"],
        pollers={"cx": lambda **kw: [reading_cx]},
        version_checker=lambda kind: None,  # no version probe
    )
    rep = res["observations"][0]["payload"]["facts_report"]
    # Carried checks ag_pools and ag_version must keep their original 2026-10-10 dates!
    assert rep["check_dates"]["ag_pools"] == "2026-10-10"
    assert rep["check_dates"]["ag_version"] == "2026-10-10"
    assert rep["checks"]["ag_pools"] == "ok"
    assert rep["status"] == "ok"
    assert rep["checked_at"] == "2026-10-10"

