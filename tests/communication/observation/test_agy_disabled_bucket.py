"""#328 A: an agy bucket flagged `disabled` is recorded as ABSENT (not applicable) with provider provenance."""
from __future__ import annotations

import copy
import json
import sqlite3
import subprocess

import pytest

from peerhub.extensions.observation_model import DISABLED_REASON, EvidenceState
from peerhub.extensions.quota_capture import refresh_quota
from peerhub.extensions.quota_probes import _agy_report_is_clean, _parse_agy_usage_output, poll_agy_usage  # pyright: ignore[reportPrivateUsage]
from tests.communication.harness.core import CoreHarness
from tests.communication.observation.test_quota_probes import DummyIdSource

pytestmark = [pytest.mark.unit]

DESC = "You have hit your weekly limit, the 5-hour limit does not currently apply"
OBSERVED = 1_790_000_000


def usage(disabled_bucket=None, *, five_h_ok=True):
    five_h = {"id": "3p-5h", "remaining_fraction": 1.0, "reset_time": "2026-09-28T09:24:42Z"} if five_h_ok else disabled_bucket
    return json.dumps({"status": "SUCCESS", "usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}, "command": {
        "name": "usage", "data": {"groups": [
            {"name": "Gemini Models", "buckets": [
                {"id": "gemini-weekly", "remaining_fraction": 0.5, "reset_time": "2026-09-30T02:26:19Z"},
                {"id": "gemini-5h", "remaining_fraction": 0.9, "reset_time": "2026-09-28T09:11:04Z"}]},
            {"name": "Claude and GPT models", "buckets": [
                {"id": "3p-weekly", "remaining_fraction": 0.0, "reset_time": "2026-10-05T04:24:42Z"}, five_h]}]}}})


def disabled(**extra):
    return {"id": "3p-5h", "disabled": True, "description": DESC, **extra}


def poll(monkeypatch, tmp_path, text):
    monkeypatch.delenv("PEERHUB_AG_STATUSLINE_LOG", raising=False)
    monkeypatch.setattr("peerhub.extensions.quota_probes._real_command", lambda peer, _s=None: ["agy.exe"] if peer == "ag" else None)
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(args=["agy.exe"], returncode=0, stdout=text, stderr=""))
    return poll_agy_usage(DummyIdSource(), "ag", "standard", clock=lambda: OBSERVED, sys_dir=tmp_path / "_sys")


def by_pool(res):
    return {(r.evidence.value.quota_pool_scope if r.evidence.value else r.extra["pool_scope"]): r for r in res}


@pytest.mark.parametrize("bucket", [disabled(), disabled(remaining_fraction=1.0), disabled(remaining_fraction=1.0, reset_time=None)],
                         ids=["bare", "with-fraction", "null-reset"])
def test_disabled_bucket_is_parsed_and_clean(bucket):
    text = usage(bucket, five_h_ok=False)
    parsed = _parse_agy_usage_output(text)
    assert parsed["3p-5h"]["disabled"] is True and parsed["3p-5h"]["description"] == DESC
    assert "remaining_fraction" not in parsed["3p-5h"] and "reset_time" not in parsed["3p-5h"]
    assert set(parsed) >= {"gemini-weekly", "gemini-5h", "3p-weekly"}
    assert _agy_report_is_clean(text)


def test_disabled_is_only_honoured_as_the_literal_true():
    for flag in ("true", 1, None, False):
        parsed = _parse_agy_usage_output(usage(disabled(disabled=flag), five_h_ok=False))
        assert parsed == {}  # a known bucket without a valid fraction still poisons the parse, as before


def test_poll_records_disabled_pool_absent_with_reason_and_keeps_other_pools(monkeypatch, tmp_path):
    res = by_pool(poll(monkeypatch, tmp_path, usage(disabled(), five_h_ok=False)))
    absent = res["3P-5H"]
    assert absent.evidence.state == EvidenceState.ABSENT and absent.evidence.value is None
    assert absent.extra == {"pool_scope": "3P-5H", "reason": f"{DISABLED_REASON}: {DESC}"}
    assert [res[p].evidence.state for p in ("G-5H", "G-7D", "3P-7D")] == [EvidenceState.MEASURED] * 3
    assert res["A-7D"].extra["reason"].startswith("not reported by provider")  # A-7D is genuinely absent, 3P-5H is not "not reported"
    assert absent.evidence.evidence_ref == "probe:ag:usage"


def test_poll_without_disabled_flag_still_measures(monkeypatch, tmp_path):
    res = by_pool(poll(monkeypatch, tmp_path, usage()))
    assert res["3P-5H"].evidence.state == EvidenceState.MEASURED


def test_description_is_sanitized_and_bounded(monkeypatch, tmp_path):
    nasty = "weekly\x1b[31m limit;\nplan=evil\t" + "x" * 500 + "‮"
    res = by_pool(poll(monkeypatch, tmp_path, usage(disabled(description=nasty), five_h_ok=False)))
    reason = res["3P-5H"].extra["reason"]
    assert reason.startswith(f"{DISABLED_REASON}: weekly") and len(reason) <= len(DISABLED_REASON) + 2 + 100
    assert all(32 <= ord(c) < 127 for c in reason) and ";" not in reason and "=" not in reason and "\x1b" not in reason


@pytest.mark.parametrize("desc", [None, 5, "", "   "])
def test_missing_or_blank_description_still_records_the_disabled_fact(monkeypatch, tmp_path, desc):
    bucket = disabled()
    bucket["description"] = desc
    res = by_pool(poll(monkeypatch, tmp_path, usage(bucket, five_h_ok=False)))
    assert res["3P-5H"].extra["reason"] == DISABLED_REASON


def test_disabled_bucket_from_the_statusline_fallback_keeps_that_sources_provenance(monkeypatch, tmp_path):
    log = tmp_path / "statusline.json"
    log.write_text(json.dumps({"quota": {
        "3p-weekly": {"remaining_fraction": 0.0, "reset_in_seconds": 3600},
        "3p-5h": {"disabled": True, "description": DESC}}}), encoding="utf-8")
    monkeypatch.delenv("PEERHUB_AG_STATUSLINE_LOG", raising=False)
    res = by_pool(poll_agy_usage(DummyIdSource(), "ag", "standard", clock=lambda: int(log.stat().st_mtime), log_path=log))
    absent = res["3P-5H"]
    assert absent.evidence.state == EvidenceState.ABSENT and absent.extra["reason"] == f"{DISABLED_REASON}: {DESC}"
    assert absent.evidence.source_tag == "agy_statusline" and absent.evidence.evidence_ref == "probe:ag:statusline"
    assert res["3P-7D"].evidence.state == EvidenceState.MEASURED
    assert "G-5H" not in res and "A-7D" not in res  # the fallback never infers ABSENT for pools it did not see


def test_group_conflict_on_a_disabled_bucket_is_still_a_conflict(monkeypatch, tmp_path):
    text = usage(disabled(window="weekly"), five_h_ok=False)  # 3p-5h claims a weekly window
    res = poll(monkeypatch, tmp_path, text)
    assert any(r.evidence.state == EvidenceState.ERROR and r.extra.get("reason") == "envelope_consistency_conflict" for r in res)


def test_refresh_persists_disabled_then_reenabled_measures_again(monkeypatch, tmp_path):
    h = CoreHarness(tmp_path / "ws")
    from peerhub.extensions.observation import ObservationStore
    ObservationStore(h.db_path)
    monkeypatch.setattr("peerhub.extensions.quota_probes._real_command", lambda peer, _s=None: ["agy.exe"] if peer == "ag" else None)
    monkeypatch.delenv("PEERHUB_AG_STATUSLINE_LOG", raising=False)

    def refresh(text):
        monkeypatch.setattr(subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(args=["agy.exe"], returncode=0, stdout=text, stderr=""))
        return refresh_quota(h.db_path, ["ag"], sys_dir=tmp_path / "_sys", version_checker=lambda k: None)

    refresh(usage())
    refresh(usage(disabled(), five_h_ok=False))
    refresh(usage())
    with sqlite3.connect(h.db_path) as c:
        rows = c.execute("SELECT state, payload_json FROM observations WHERE resource_pool_ref='quota:ag:3P-5H' ORDER BY capture_seq").fetchall()
    assert [state for state, _ in rows] == ["MEASURED", "ABSENT", "MEASURED"]  # history is kept, latest wins
    payload = json.loads(rows[1][1])
    assert payload["reason"] == f"{DISABLED_REASON}: {DESC}"
    assert "remaining_fraction" not in payload and "resets_at" not in payload
    assert copy.deepcopy(rows)  # rows are plain data
