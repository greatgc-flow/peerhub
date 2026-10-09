"""Offline oracles for measurement helpers; no provider execution."""
import sqlite3

import pytest

from tests.communication.live.ask_support import ask_args, assert_measured_response, running_session
from tests.communication.live.live_support import opt_in_reason


def test_args_keep_standard_profile_and_explicit_session_binding(tmp_path):
    args = ask_args("cc", tmp_path, "tiny prompt", "turn-2", resume=True, writable=True)
    assert args[args.index("--profile") + 1] == "cc.standard"
    assert args[args.index("--workspace") + 1] == str(tmp_path)
    assert args[args.index("--db") + 1] == str(tmp_path / "core.db")
    assert args[args.index("--stream") + 1] == "live-measurement"
    assert args[args.index("--request-id") + 1] == "turn-2"
    assert "--resume" in args and "--writable" in args
    plain = ask_args("ag", tmp_path, "OK", "basic")
    assert "--resume" not in plain and "--writable" not in plain


@pytest.mark.parametrize("usage", [None, {}, {"input_tokens": True, "output_tokens": 1},
                                  {"input_tokens": 1, "output_tokens": 0},
                                  {"input_tokens": 1, "output_tokens": 1, "cached_input_tokens": -1}])
def test_measurements_reject_unknown_invalid_or_empty_counters(usage):
    result = {"status": "delivered", "certainty": "TERMINAL", "response_record_id": "r", "response": "OK"}
    if usage is not None:
        result["usage"] = usage
    with pytest.raises((AssertionError, KeyError)):
        assert_measured_response(result)


def test_measurements_accept_reported_usage():
    assert_measured_response({"status": "delivered", "certainty": "TERMINAL", "response_record_id": "r",
                              "response": "OK", "usage": {"input_tokens": 10, "output_tokens": 1,
                                                          "cached_input_tokens": 0}})


def test_running_session_requires_committed_started_unacked_target(tmp_path):
    db = tmp_path / "core.db"
    assert running_session(db, "cx") is None and not db.exists()
    with sqlite3.connect(db) as conn:
        assert running_session(db, "cx") is None
        conn.execute("CREATE TABLE bridge_deliveries (peer_id TEXT, certainty TEXT, acked INTEGER, external_session_id TEXT)")
        conn.executemany("INSERT INTO bridge_deliveries VALUES (?, ?, ?, ?)",
                         [("ag", "STARTED", 0, "wrong-peer"), ("cx", "MAY_HAVE_STARTED", 0, "not-started"),
                          ("cx", "STARTED", 1, "acked")])
        conn.commit()
        assert running_session(db, "cx") is None
        conn.execute("INSERT INTO bridge_deliveries VALUES ('cx', 'STARTED', 0, 'running')")
        assert running_session(db, "cx") is None
        conn.commit()
        assert running_session(db, "cx") == "running"


def test_ci_blocks_even_explicit_live_opt_in(monkeypatch):
    monkeypatch.setenv("PEERHUB_LIVE", "1")
    monkeypatch.setenv("CI", "true")
    assert opt_in_reason() == "LIVE-CI-DISABLED: real provider calls are local-only"
    monkeypatch.delenv("CI")
    assert opt_in_reason() is None
