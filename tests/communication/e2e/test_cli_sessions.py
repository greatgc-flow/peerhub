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


def test_session_show_links_configured_profile_and_scoped_usage(tmp_path, capsys):
    class ProfileTelemetryRuntime(FakeRuntimeTarget):
        runtime_kind = "cx"

        def __init__(self, *args, model=None, effort=None, profile=None, writable=False, **kwargs):
            super().__init__(fingerprint="adapter:cx:v2", binding="gpt-6-luna/low")

        def session_binding(self):
            return {"profile": "cx.standard", "model": "gpt-6-luna", "effort": "low", "writable": False}

        def deliver(self, external_session_id, record, catch_up):
            self.script_deliver(("started", "exec"), ("terminal", {
                "response": "ok", "provider_telemetry": {
                    "schema_version": 1, "scope": "session_cumulative", "source": "cx.turn.completed.usage",
                    "reported": {"input_tokens": 40, "output_tokens": 4},
                    "normalized": {"provider_input_tokens": 40, "output_tokens": 4},
                    "coverage": {"state": "MEASURED", "reason": None},
                },
                "usage": {"input_tokens": 40, "output_tokens": 4},
            }))
            yield from super().deliver(external_session_id, record, catch_up)

    db = tmp_path / "core.db"
    with patch("peerhub.extensions.ask.CliRuntimeTarget", ProfileTelemetryRuntime):
        assert main(["--db", str(db), "ask", "cx", "profile check", "--json"]) == 0
    ask_result = json.loads(capsys.readouterr().out)
    assert ask_result["usage"] == {"input_tokens": 40, "output_tokens": 4}
    assert ask_result["usage_scope"] == "session_cumulative"
    assert ask_result["usage_attribution"] == "unattributed"
    listed = json.loads((lambda: (main(["--db", str(db), "session", "list"]), capsys.readouterr().out)[1])())
    handle = listed["items"][0]["handle"]

    assert main(["--db", str(db), "session", "show", handle]) == 0
    session = json.loads(capsys.readouterr().out)["session"]
    assert session["configured_binding"] == {
        "profile": "cx.standard", "model": "gpt-6-luna", "effort": "low", "writable": False,
    }
    assert session["call_counts"]["fresh"] == 1 and session["call_counts"]["complete"] is True
    assert session["latest_telemetry"]["schema_version"] == 3
    assert session["latest_telemetry"]["usage"]["schema_version"] == 1
    assert session["latest_telemetry"]["configured_model"] == "gpt-6-luna"
    assert session["latest_telemetry"]["resolved_model"] is None
    assert session["latest_telemetry"]["usage"]["scope"] == "session_cumulative"
    assert session["latest_telemetry"]["usage"]["attribution"] == "unattributed"
    assert "provider_telemetry" not in json.dumps(session)
    assert main(["--db", str(db), "session", "list", "--format", "table"]) == 0
    table = capsys.readouterr().out
    assert "None" not in table
    assert "cx.standard" in table and "gpt-6-luna" in table


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
