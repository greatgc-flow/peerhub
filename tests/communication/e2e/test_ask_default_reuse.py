"""Public removal and normal first/continuing asks, without provider calls."""
import importlib
import json
import sqlite3
import subprocess
import sys

import pytest

from peerhub.cli import main
from peerhub.extensions.adapters import base
from peerhub.extensions.ask import ask
from tests.communication.fakes.runtime import NativeRuntimeTarget


def test_emitted_help_and_removed_option_rejection(tmp_path):
    help_run = subprocess.run([sys.executable, "-m", "peerhub", "ask", "--help"], capture_output=True)
    assert help_run.returncode == 0 and b"--resume" not in help_run.stdout
    assert b"compatible current session" in help_run.stdout
    db = tmp_path / "absent.db"
    removed = subprocess.run([sys.executable, "-m", "peerhub", "--db", str(db),
                              "ask", "cx", "hello", "--resume"], capture_output=True)
    assert removed.returncode == 2 and b"unrecognized arguments: --resume" in removed.stderr
    assert not db.exists()


def test_removed_python_keywords_fail_before_writes(tmp_path):
    db = tmp_path / "absent.db"
    with pytest.raises(TypeError, match="resume"):
        ask(db, "cx", "hello", resume=True)
    with pytest.raises(TypeError, match="resume"):
        base.CliRuntimeTarget("cx", tmp_path, resume=True)
    assert not db.exists()


@pytest.mark.parametrize("kind", ["cc", "cx", "ag"])
def test_cli_default_reuse_has_no_preference_in_kwargs_or_records(tmp_path, monkeypatch, capsys, kind):
    module = importlib.import_module("peerhub.extensions.ask")
    kwargs_seen = []
    runtimes = []

    def runtime(kind, workspace, **kwargs):
        kwargs_seen.append(kwargs)
        rt = NativeRuntimeTarget()
        rt.runtime_kind = kind
        runtimes.append(rt)
        return rt

    monkeypatch.setattr(module, "CliRuntimeTarget", runtime)
    db = tmp_path / "core.db"
    def turn(prompt, rid):
        assert main(["--db", str(db), "ask", kind, prompt, "--request-id", rid, "--json"]) == 0
        return json.loads(capsys.readouterr().out)

    first = turn("first", "one")
    second = turn("second", "two")
    assert first["effective_mode"] == "fresh" and second["effective_mode"] == "resumed"
    assert first["session_generation"] == second["session_generation"] == 1
    assert first["requested_record_id"] not in second["injected_record_ids"]
    assert runtimes[1].vendor_ids == ["native-ext-1"]
    replay = turn("second", "two")
    assert replay["status"] == "recovered_terminal" and runtimes[2].calls == []
    assert replay["response_record_id"] == second["response_record_id"]
    with sqlite3.connect(db) as conn:
        conn.execute("ALTER TABLE bridge_sessions DROP COLUMN context_watermark_version")
        # This identity has no matching exact receipt, so upgrade must discard continuity.
        conn.execute("UPDATE bridge_sessions SET vendor_session_id='unproven', resumable=0")
    upgraded_replay = turn("second", "two")
    assert upgraded_replay["status"] == "recovered_terminal" and runtimes[3].calls == []
    assert upgraded_replay["response_record_id"] == second["response_record_id"]
    assert all("resume" not in kwargs for kwargs in kwargs_seen)
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT resumable FROM bridge_sessions").fetchone() == (1,)
        metadata = [json.loads(row[0]) for row in conn.execute("SELECT metadata_json FROM records")]
    assert all("resume" not in row and "session_selector" not in row for row in metadata)
