"""`observation refresh` watch mode: argument validation, default single collection, interrupt handling."""
import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from peerhub.cli.app import main
from peerhub.extensions.observation_model import EvidenceState


def _probe(calls):
    def probe(**kw):
        calls.append(kw)
        ev = SimpleNamespace(state=EvidenceState.ABSENT, value=None, evidence_ref="t", source_tag="fake", observed_at=1, captured_at=1)
        return [SimpleNamespace(evidence=ev)]
    return probe


def _run(db, *args):
    try:
        return main(["--db", str(db), "observation", "refresh", "--peers", "cc", *args])
    except SystemExit as exc:
        return exc.code


@pytest.mark.parametrize("args", [["--interval-seconds", "0"], ["--interval-seconds", "-1"], ["--interval-seconds", "nan"],
                                  ["--count", "2"], ["--interval-seconds", "1", "--count", "-1"]])
def test_invalid_watch_arguments_exit_2_and_collect_nothing(tmp_path, capsys, args):
    calls = []
    with patch("peerhub.extensions.quota_probes.poll_claude_usage", _probe(calls)):
        assert _run(tmp_path / "w.db", *args) == 2
    assert calls == [] and "Traceback" not in capsys.readouterr().err and not (tmp_path / "w.db").exists()


def test_default_is_a_single_pretty_collection_without_sleeping(tmp_path, capsys):  # positive control for the unchanged behaviour
    calls, sleeps = [], []
    with patch("peerhub.extensions.quota_probes.poll_claude_usage", _probe(calls)), patch("time.sleep", lambda s: sleeps.append(s)):
        assert _run(tmp_path / "w.db") == 0
    out = capsys.readouterr().out
    assert len(calls) == 1 and sleeps == [] and out.startswith("{\n") and json.loads(out)["observations"][0]["state"] == "ABSENT"


def test_interval_without_count_runs_until_interrupted_and_exits_cleanly(tmp_path, capsys):
    calls, sleeps = [], []

    def sleep(seconds):
        sleeps.append(seconds)
        if len(sleeps) == 3:
            raise KeyboardInterrupt

    with patch("peerhub.extensions.quota_probes.poll_claude_usage", _probe(calls)), patch("time.sleep", sleep):
        assert _run(tmp_path / "w.db", "--interval-seconds", "7") == 0
    assert len(calls) == 3 and sleeps == [7.0, 7.0, 7.0]
    assert len([x for x in capsys.readouterr().out.splitlines() if x.strip()]) == 3


def test_count_zero_means_until_interrupted(tmp_path):
    calls = []

    def sleep(_s):
        if len(calls) == 4:
            raise KeyboardInterrupt

    with patch("peerhub.extensions.quota_probes.poll_claude_usage", _probe(calls)), patch("time.sleep", sleep):
        assert _run(tmp_path / "w.db", "--interval-seconds", "1", "--count", "0") == 0
    assert len(calls) == 4
