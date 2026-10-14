import json
from unittest.mock import patch

from peerhub.cli.app import main
from tests.communication.fakes import FakeRuntimeTarget


def _seed(db, capsys, count=3):
    def target(*args, **kwargs):
        runtime = FakeRuntimeTarget()
        runtime.script_deliver(("started", "exec"), ("terminal", {"response": "ok"}))
        return runtime

    with patch("peerhub.extensions.ask.CliRuntimeTarget", target):
        for index in range(count):
            assert main(["--db", str(db), "ask", "cx", f"prompt {index}", "--fresh", "--json"]) == 0
            capsys.readouterr()


def test_session_list_is_bounded_stable_and_redacted(tmp_path, capsys):
    db = tmp_path / "core.db"
    _seed(db, capsys)

    assert main(["--db", str(db), "session", "list", "--limit", "1"]) == 0
    first = json.loads(capsys.readouterr().out)
    assert len(first["items"]) == 1 and first["next_cursor"]
    assert "vendor_session_id" not in first["items"][0] and "external_session_id" not in first["items"][0]

    # A newer generation inserted after page one is outside its captured high-water.
    _seed(db, capsys, count=1)
    assert main(["--db", str(db), "session", "list", "--limit", "1", "--cursor", first["next_cursor"]]) == 0
    second = json.loads(capsys.readouterr().out)
    assert len(second["items"]) == 1 and second["next_cursor"]
    assert second["items"][0]["handle"] != first["items"][0]["handle"]


def test_session_show_is_read_only_and_not_found_is_structured(tmp_path, capsys):
    db = tmp_path / "core.db"
    _seed(db, capsys, count=1)
    listed = json.loads((lambda: (main(["--db", str(db), "session", "list"]), capsys.readouterr().out)[1])())
    handle = listed["items"][0]["handle"]

    assert main(["--db", str(db), "session", "show", handle]) == 0
    shown = json.loads(capsys.readouterr().out)
    assert shown["session"]["handle"] == handle
    assert "vendor_session_id" not in shown["session"] and "external_session_id" not in shown["session"]

    assert main(["--db", str(db), "session", "show", "sess_0000000000000000"]) == 1
    assert json.loads(capsys.readouterr().out)["status"] == "NOT_FOUND"


def test_session_cursor_rejects_changed_filter(tmp_path, capsys):
    db = tmp_path / "core.db"
    _seed(db, capsys, count=2)
    assert main(["--db", str(db), "session", "list", "--limit", "1", "--peer", "cx"]) == 0
    cursor = json.loads(capsys.readouterr().out)["next_cursor"]

    assert main(["--db", str(db), "session", "list", "--limit", "1", "--peer", "ag", "--cursor", cursor]) == 1
    assert json.loads(capsys.readouterr().out)["status"] == "ERROR"


def test_session_inspection_refuses_future_schema(tmp_path, capsys):
    import sqlite3

    db = tmp_path / "future.db"
    with sqlite3.connect(db) as conn:
        conn.execute("PRAGMA user_version = 999")

    assert main(["--db", str(db), "session", "list"]) == 6
    assert json.loads(capsys.readouterr().out)["status"] == "SCHEMA_VERSION_ERROR"
