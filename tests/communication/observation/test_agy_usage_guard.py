"""agy `/usage` token-consumption guard: a fake agy executable (python script) stands in for the real binary; no model is ever called."""
import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from peerhub.cli.app import main
from peerhub.extensions.observation_model import EvidenceState
from peerhub.extensions.quota_capture import refresh_quota
from peerhub.extensions.quota_probes import poll_agy_usage

GOOD_DATA = {"groups": [{"name": "g", "buckets": [
    {"id": "gemini-5h", "remaining_fraction": 0.5, "reset_time": "2026-09-28T09:11:04Z"},
    {"id": "gemini-weekly", "remaining_fraction": 0.25, "reset_time": "2026-09-30T02:26:19Z"}]}]}
GOOD = {"status": "SUCCESS", "num_turns": 0, "usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
        "command": {"name": "usage", "data": GOOD_DATA}}
PROSE = "Gemini 5h: 99% remaining (this prose must never be parsed as quota)"
CONSUMED = {"status": "SUCCESS", "num_turns": 1, "usage": {"total_tokens": 94000}, "response": PROSE, "command": {"name": "usage", "data": GOOD_DATA}}
SCRIPT = ("import os, sys, json\nhere = os.path.dirname(os.path.abspath(__file__))\n"
          "open(os.path.join(here, 'spawns.log'), 'a').write(os.getcwd() + '|' + ' '.join(sys.argv[1:]) + '\\n')\n"
          "sys.stdout.write(open(os.path.join(here, 'out.txt'), encoding='utf-8').read())\n")


class Ids:
    def new_id(self, prefix):
        return f"{prefix}-1"


@pytest.fixture
def fake(tmp_path, monkeypatch):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    script = bindir / "fake_agy.py"
    script.write_text(SCRIPT, encoding="utf-8")
    monkeypatch.delenv("PEERHUB_AG_STATUSLINE_LOG", raising=False)
    monkeypatch.setattr("peerhub.extensions.quota_probes._real_command", lambda peer, _s=None: [sys.executable, str(script)] if peer == "ag" else None)

    class F:
        root = tmp_path
        sys_dir = tmp_path / "_sys"
        def emit(self, obj):
            (bindir / "out.txt").write_text(obj if isinstance(obj, str) else json.dumps(obj), encoding="utf-8")
        def spawns(self):
            p = bindir / "spawns.log"
            return p.read_text(encoding="utf-8").splitlines() if p.exists() else []
    return F()


def poll(f, **kw):
    return poll_agy_usage(Ids(), "ag", "default", clock=lambda: 1_790_000_000, sys_dir=f.sys_dir, deadline_sec=20, **kw)


def test_proper_usage_json_is_measured_and_runs_in_workspace_root(fake):
    fake.emit(GOOD)
    res = poll(fake)
    assert len(res) == 2 and all(r.evidence.state == EvidenceState.MEASURED for r in res)
    (line,) = fake.spawns()
    assert Path(line.split("|")[0]).resolve() == fake.root.resolve() and "-p /usage" in line


def test_tokens_consumed_is_error_and_prose_never_parsed(fake):
    fake.emit(CONSUMED)
    (r,) = poll(fake)
    assert r.evidence.state == EvidenceState.ERROR and r.evidence.value is None
    assert r.evidence.evidence_ref == "agy_usage_consumed_tokens"
    assert r.extra["consumed_tokens"] == 94000 and r.extra["num_turns"] == 1 and "94000" in r.extra["warning"]


@pytest.mark.parametrize("tokens", [1, 94000, 0.5])
def test_tokens_alone_without_turns_is_consumption(fake, tokens):
    fake.emit({**GOOD, "usage": {"total_tokens": tokens}, "num_turns": 0})
    (r,) = poll(fake)
    assert r.evidence.state == EvidenceState.ERROR and r.evidence.evidence_ref == "agy_usage_consumed_tokens" and r.extra["consumed_tokens"] == tokens


def test_num_turns_alone_is_consumption(fake):
    fake.emit({**GOOD, "num_turns": 2})
    (r,) = poll(fake)
    assert r.evidence.state == EvidenceState.ERROR and r.evidence.evidence_ref == "agy_usage_consumed_tokens"


def test_nonzero_tokens_with_nonzero_exit_still_flagged(fake):
    fake.emit(CONSUMED)
    (r,) = poll(fake)  # exit code irrelevant: fake exits 0 here, real one may not
    assert r.evidence.evidence_ref == "agy_usage_consumed_tokens"


@pytest.mark.parametrize("env", [{"status": "SUCCESS", "num_turns": 0, "usage": {"total_tokens": 0}, "response": PROSE},
                                 {**GOOD, "command": {"name": "help", "data": GOOD_DATA}},
                                 {**GOOD, "command": None}])
def test_command_missing_or_other_is_error(fake, env):
    fake.emit(env)
    (r,) = poll(fake)
    assert r.evidence.state == EvidenceState.ERROR and r.evidence.evidence_ref == "agy_usage_not_a_command" and r.evidence.value is None


def test_unverifiable_token_count_fails_closed(fake):
    fake.emit({**GOOD, "usage": {"total_tokens": "lots"}})
    (r,) = poll(fake)
    assert r.evidence.state == EvidenceState.ERROR and r.evidence.evidence_ref == "agy_usage_unverifiable_tokens"


def test_non_json_keeps_existing_behaviour(fake):
    fake.emit("this is not json at all")
    (r,) = poll(fake)  # falls through to the statusline-log fallback: no log in the temp sys dir -> ABSENT
    assert r.evidence.state == EvidenceState.ABSENT and r.evidence.evidence_ref != "agy_usage_not_a_command"


def test_cwd_guard_missing_directory_spawns_nothing(fake):
    (r,) = poll_agy_usage(Ids(), "ag", "default", clock=lambda: 1, sys_dir=fake.root / "missing_dir" / "_sys")
    assert r.evidence.state == EvidenceState.ERROR and r.evidence.evidence_ref == "agy_usage_cwd_refused" and fake.spawns() == []


def test_cwd_guard_system_temp_dir_spawns_nothing(fake):
    fake.emit(GOOD)
    (r,) = poll_agy_usage(Ids(), "ag", "default", clock=lambda: 1, sys_dir=Path(tempfile.gettempdir()) / "_sys")
    assert r.evidence.evidence_ref == "agy_usage_cwd_refused" and fake.spawns() == []


def test_refresh_quota_reports_warning_and_payload(fake, tmp_path):
    fake.emit(CONSUMED)
    res = refresh_quota(tmp_path / "w" / "core.db", ["ag"], sys_dir=fake.sys_dir)
    o = res["observations"][0]
    assert res["status"] == "PARTIAL" and o["state"] == "ERROR"
    assert o["payload"]["evidence_ref"] == "agy_usage_consumed_tokens" and o["payload"]["consumed_tokens"] == 94000
    assert o["payload"]["condition"] == "provider_probe_error" and "remaining_fraction" not in o["payload"]
    assert len(res["warnings"]) == 1 and "94000" in res["warnings"][0]


def test_refresh_quota_without_warnings_has_empty_list(fake, tmp_path):
    fake.emit(GOOD)
    res = refresh_quota(tmp_path / "w" / "core.db", ["ag"], sys_dir=fake.sys_dir)
    assert res["warnings"] == [] and res["status"] == "OK"


# ----------------------------------------------------------------------------- CLI
def cli(db, fake, *args):
    with patch("time.sleep", lambda s: sleeps.append(s)):
        try:
            return main(["--db", str(db), "observation", "refresh", "--peers", "ag", "--sys-dir", str(fake.sys_dir), *args])
        except SystemExit as e:
            return e.code


sleeps: list = []


def test_single_refresh_prints_warning_on_stderr_exit_1(fake, tmp_path, capsys):
    fake.emit(CONSUMED)
    assert cli(tmp_path / "w" / "core.db", fake) == 1
    cap = capsys.readouterr()
    assert "94000" in cap.err and "agy_usage_consumed_tokens" in cap.err and json.loads(cap.out)["warnings"]
