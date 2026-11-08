"""plan-0.24.0 P4: a complete provider report that omits a declared pool records it ABSENT for every peer.

Malformed, partial or fallback evidence never produces ABSENT (it must stay unknown, never "unlimited").
"""
from __future__ import annotations

import datetime as dt
import json
import subprocess
from types import SimpleNamespace

import pytest

from peerhub.core.store import CoreStore
from peerhub.extensions.observation_model import NOT_REPORTED_REASON, EvidenceState
from peerhub.extensions.quota_capture import refresh_quota
from peerhub.extensions.quota_probes import poll_agy_usage, poll_claude_usage
from tests.communication.observation.test_quota_probes import AGY_USAGE_JSON, DummyIdSource

pytestmark = [pytest.mark.unit]

REASON = f"{NOT_REPORTED_REASON}; plan=unknown"
RESET = dt.datetime(2026, 11, 8, 12, 0, tzinfo=dt.timezone.utc)
SESSION = "Current session: 50% used resets 12:00pm"
WEEK = "Current week (all models): 10% used resets 12:00pm"
FABLE = "Current week (Fable): 20% used resets 12:00pm"


def run_cc(monkeypatch, text, returncode=0, **kw):
    class FakeProc:
        pid = 9999
        def communicate(self, timeout=None):
            self.returncode = returncode
            return (text, "")

    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: FakeProc())
    monkeypatch.setattr("peerhub.extensions.quota_probes._real_binary", lambda _peer, _sys_dir=None: "dummy.exe")
    monkeypatch.setattr("peerhub.extensions.quota_probes._parse_claude_usage_reset", lambda *a, **k: RESET)
    return poll_claude_usage(DummyIdSource(), "cc", "default", **kw)


def states(res):
    return [(r.evidence.value.quota_pool_scope if r.evidence.value else r.extra.get("pool_scope"), r.evidence.state) for r in res]


def test_cc_missing_session_line_records_c5h_absent(monkeypatch):
    res = run_cc(monkeypatch, "\n".join([WEEK, FABLE]))
    assert states(res) == [("C-7D", EvidenceState.MEASURED), ("F-7D", EvidenceState.MEASURED), ("C-5H", EvidenceState.ABSENT)]
    absent = res[2]
    assert absent.evidence.value is None and absent.extra == {"pool_scope": "C-5H", "reason": REASON}


def test_cc_complete_report_records_no_absent(monkeypatch):
    res = run_cc(monkeypatch, "\n".join([SESSION, WEEK, FABLE]))
    assert [s for _, s in states(res)] == [EvidenceState.MEASURED] * 3


def test_cc_absent_follows_the_injected_configured_pools(monkeypatch):
    res = run_cc(monkeypatch, "\n".join([WEEK]), configured_pools=["C-5H", "C-7D"])
    assert states(res) == [("C-7D", EvidenceState.MEASURED), ("C-5H", EvidenceState.ABSENT)]  # F-7D not declared


@pytest.mark.parametrize("text", [
    "Current session: abc% used resets 12:00pm\n" + WEEK,   # malformed section poisons the whole parse
    "Current session\n" + WEEK,                              # section present but never parsed into a row
    "garbled nonsense",
])
def test_cc_malformed_report_records_no_absent(monkeypatch, text):
    res = run_cc(monkeypatch, text)
    assert EvidenceState.ABSENT not in [s for _, s in states(res)]


@pytest.mark.parametrize("returncode", [1, 2, None])
def test_cc_failed_or_unfinished_run_records_no_absent(monkeypatch, returncode):
    """Partial output of a failed /usage run must not turn the missing session line into 'not reported'."""
    res = run_cc(monkeypatch, "\n".join([WEEK, FABLE]), returncode=returncode)
    assert states(res) == [("C-7D", EvidenceState.MEASURED), ("F-7D", EvidenceState.MEASURED)]


def run_ag(monkeypatch, tmp_path, stdout, **kw):
    completed = SimpleNamespace(returncode=0, stdout=stdout, stderr="")
    monkeypatch.delenv("PEERHUB_AG_STATUSLINE_LOG", raising=False)
    monkeypatch.setattr("peerhub.extensions.quota_probes._real_command",
                        lambda peer, _sys_dir=None: ["agy.exe"] if peer == "ag" else None)
    monkeypatch.setattr(subprocess, "run", lambda argv, **kwargs: completed)
    return poll_agy_usage(DummyIdSource(), "ag", "standard", clock=lambda: 1_790_000_000, sys_dir=tmp_path / "_sys", **kw)


def test_ag_missing_bucket_records_absent_pool(monkeypatch, tmp_path):
    res = run_ag(monkeypatch, tmp_path, AGY_USAGE_JSON)
    assert [s for _, s in states(res)].count(EvidenceState.MEASURED) == 4
    absent = [r for r in res if r.evidence.state == EvidenceState.ABSENT]
    assert [r.extra for r in absent] == [{"pool_scope": "A-7D", "reason": REASON}]


def test_ag_absent_follows_the_injected_configured_pools(monkeypatch, tmp_path):
    res = run_ag(monkeypatch, tmp_path, AGY_USAGE_JSON, configured_pools=["G-5H", "A-7D"])
    assert [r.extra["pool_scope"] for r in res if r.evidence.state == EvidenceState.ABSENT] == ["A-7D"]
    undeclared = run_ag(monkeypatch, tmp_path, AGY_USAGE_JSON, configured_pools=["G-5H"])
    assert EvidenceState.ABSENT not in [r.evidence.state for r in undeclared]


def test_ag_unknown_bucket_drift_records_no_absent(monkeypatch, tmp_path):
    """An unfamiliar bucket id means the report vocabulary changed: a missing known bucket may just be renamed."""
    payload = json.loads(AGY_USAGE_JSON)
    payload["command"]["data"]["groups"][0]["buckets"].append(
        {"id": "gemini-new-preview-5h", "remaining_fraction": 0.5, "reset_time": "2026-09-28T09:11:04Z"})
    res = run_ag(monkeypatch, tmp_path, json.dumps(payload))
    assert [s for _, s in states(res)] == [EvidenceState.MEASURED] * 4  # known buckets stay measured, nothing inferred
    assert all(r.extra.get("unknown_pools") == ["gemini-new-preview-5h"] for r in res)


def test_ag_malformed_bucket_records_no_absent(monkeypatch, tmp_path):
    payload = json.loads(AGY_USAGE_JSON)
    payload["command"]["data"]["groups"][0]["buckets"][0].pop("reset_time")  # present but unusable
    res = run_ag(monkeypatch, tmp_path, json.dumps(payload))
    assert EvidenceState.ABSENT not in [s for _, s in states(res)]


def test_ag_statusline_fallback_never_records_absent(tmp_path):
    log_file = tmp_path / "ag.json"
    log_file.write_text(json.dumps({"quota": {"gemini-5h": {"remaining_fraction": 0.2, "reset_in_seconds": 3600}}}), encoding="utf-8")
    res = poll_agy_usage(DummyIdSource(), "ag", "standard", log_path=log_file)
    assert [s for _, s in states(res)] == [EvidenceState.MEASURED]


def test_refresh_forwards_injected_pools_to_each_peer_and_ag_probe_stays_complete(tmp_path):
    db = tmp_path / "core.db"
    CoreStore(db)
    calls = {}

    def poller(kind, readings):
        def poll(**options):
            calls[kind] = options
            return readings
        return poll

    def reading(state, scope=None, extra=None):
        value = SimpleNamespace(quota_pool_scope=scope, remaining_fraction=0.5, window_started_at=100, resets_at=18100) \
            if state == EvidenceState.MEASURED else None
        return SimpleNamespace(evidence=SimpleNamespace(state=state, value=value, evidence_ref="probe", source_tag="fake",
                                                        observed_at=100, captured_at=100), extra=extra or {})

    ag = [reading(EvidenceState.MEASURED, "G-5H"),
          reading(EvidenceState.ABSENT, extra={"pool_scope": "A-7D", "reason": REASON})]
    cc = [reading(EvidenceState.MEASURED, "C-7D"),
          reading(EvidenceState.ABSENT, extra={"pool_scope": "F-7D", "reason": REASON})]
    result = refresh_quota(db, ["ag", "cc"], cc_pools=["C-7D", "F-7D"], ag_pools=["G-5H", "A-7D"],
                           pollers={"ag": poller("ag", ag), "cc": poller("cc", cc)}, version_checker=lambda _: None)
    assert calls["ag"]["configured_pools"] == ["G-5H", "A-7D"] and calls["cc"]["configured_pools"] == ["C-7D", "F-7D"]
    by_pool = {(o["subject_ref"], o["resource_pool_ref"]): o for o in result["observations"]}
    assert by_pool[("ag", "quota:ag:A-7D")]["state"] == "ABSENT" and by_pool[("cc", "quota:cc:F-7D")]["payload"]["reason"] == REASON
    # An ag report that is complete (with a not-reported pool) is not an incomplete probe: no drift is recorded.
    assert result["observations"][0]["payload"]["facts_report"]["checks"]["ag_pools"] == "ok"
