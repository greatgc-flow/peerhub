"""Public PR3 selector/help/output and paid-helper contracts; no providers."""
import json
import sqlite3

import pytest

from peerhub.cli.app import main
from peerhub.extensions import ask as module
from tests.communication.fakes.runtime import NativeRuntimeTarget
from tests.communication.live.ask_support import ask_args


def test_cli_default_fresh_exact(tmp_path, monkeypatch, capsys):
    rt = NativeRuntimeTarget()
    real = module.ask
    monkeypatch.setattr(module, "ask", lambda *a, **kw: real(*a, **kw, runtime=rt))
    argv = ["--db", str(tmp_path / "core.db"), "ask", "cx", "hello", "--json"]
    assert main(argv) == 0
    a = json.loads(capsys.readouterr().out)["session"]
    assert main(argv + ["--fresh"]) == 0
    b = json.loads(capsys.readouterr().out)["session"]
    assert b["handle"] != a["handle"] and b["reason"] == "explicit_fresh"
    assert main(argv + ["--session", a["handle"]]) == 0
    assert json.loads(capsys.readouterr().out)["session"]["handle"] == a["handle"]


@pytest.mark.parametrize("flags,code", [
    (["--fresh", "--session", "sess_0000000000000001"], "conflicting_session_flags"),
    (["--session", "bad"], "invalid_session_handle_syntax"),
    (["--session", "sess_0000000000000000"], "session_not_found"),
])
def test_cli_selector_errors_have_final_nullable_envelope(tmp_path, monkeypatch, capsys, flags, code):
    rt = NativeRuntimeTarget()
    real = module.ask
    monkeypatch.setattr(module, "ask", lambda *a, **kw: real(*a, **kw, runtime=rt))
    assert main(["--db", str(tmp_path / "core.db"), "ask", "cx", "hello", "--json", *flags]) == 2
    result = json.loads(capsys.readouterr().out)
    assert result["session"] is None and result["error"]["code"] == code and rt.calls == []


@pytest.mark.parametrize("loss", ["before_append", "during_resume"])
def test_cli_session_lost_exits_one(tmp_path, monkeypatch, capsys, loss):
    rt = NativeRuntimeTarget()
    real = module.ask
    monkeypatch.setattr(module, "ask", lambda *a, **kw: real(*a, **kw, runtime=rt))
    db = tmp_path / "core.db"
    argv = ["--db", str(db), "ask", "cx", "hello", "--json"]
    assert main(argv) == 0
    handle = json.loads(capsys.readouterr().out)["session"]["handle"]
    if loss == "before_append":
        with sqlite3.connect(db) as conn:
            conn.execute("UPDATE bridge_session_history SET state='LOST'")
    else:
        rt.script_resume("missing")
    exact = argv + ["--session", handle, "--request-id", "lost"]
    assert main(exact) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["session"] is None and result["error"]["code"] == "session_lost"
    assert rt.count("create") == rt.count("deliver") == 1
    if loss == "during_resume":
        calls = list(rt.calls)
        assert main(exact) == 1
        assert json.loads(capsys.readouterr().out)["error"]["code"] == "session_lost"
        assert rt.calls == calls


def test_help_and_live_helper_selectors(tmp_path, capsys):
    with pytest.raises(SystemExit) as exc:
        main(["ask", "--help"])
    assert exc.value.code == 0
    help_text = capsys.readouterr().out
    assert "--fresh" in help_text and "--session HANDLE" in help_text
    assert "--resume" not in help_text
    assert "--fresh" in ask_args("cx", tmp_path, "x", "fresh", fresh=True)
    handle = "sess_0000000000000001"
    exact = ask_args("cx", tmp_path, "x", "exact", session=handle)
    assert exact[exact.index("--session") + 1] == handle
    with pytest.raises(ValueError):
        ask_args("cx", tmp_path, "x", "bad", fresh=True, session=handle)
