import json
import sqlite3
from unittest.mock import patch

import pytest

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


def test_hidden_store_uses_database_directory_for_lineage(tmp_path, capsys):
    from peerhub.core.workspace import Workspace
    from peerhub.extensions.monitor_read_model import _monitor_workspace

    root = tmp_path / ".peerhub"
    db = root / "core.db"
    _seed(db, capsys, count=2)
    lineage = Workspace(root).generation()
    assert not (tmp_path / "workspace.generation").exists()
    with sqlite3.connect(db) as conn:
        metadata = json.loads(conn.execute("SELECT metadata_json FROM records WHERE kind='prompt' LIMIT 1").fetchone()[0])
        assert metadata["workspace_generation"] == lineage
        assert conn.execute("SELECT DISTINCT workspace_generation FROM bridge_session_history").fetchall() == [(lineage,)]
    assert _monitor_workspace(str(db), None) == tmp_path
    assert main(["--db", str(db), "session", "list", "--limit", "1"]) == 0
    cursor = json.loads(capsys.readouterr().out)["next_cursor"]
    Workspace(root).replace_generation()
    assert main(["--db", str(db), "session", "list", "--limit", "1", "--cursor", cursor]) == 1
    assert json.loads(capsys.readouterr().out)["status"] == "ERROR"


@pytest.mark.parametrize("mismatch", [None, "handle", "generation", "provider_kind", "schema_version"])
def test_session_inspection_validates_receipt_identity_usage_and_context(tmp_path, capsys, mismatch):
    db = tmp_path / "core.db"
    _seed(db, capsys, count=1)
    with sqlite3.connect(db) as conn:
        handle, generation, provider = conn.execute(
            "SELECT session_handle,session_generation,runtime_kind FROM bridge_session_history").fetchone()
        detail = {
            "schema_version": 3, "handle": handle, "generation": generation, "provider_kind": provider,
            "configured_model": "untrusted\nmodel", "resolved_model": "untrusted\nmodel",
            "last_input_tokens": 50, "context_window": 100, "context_pct": 50,
            "source": "invented", "observed_at": 123, "window_reported_at": 123,
            "usage": {"schema_version": 1, "reported": {"input_tokens": -1}, "normalized": {"output_tokens": -1}},
        }
        if mismatch:
            detail[mismatch] = "wrong"
            detail["configured_model"] = "plausible-model"
        # Import an untrusted receipt and point the projection at it without
        # mutating the append-only evidence already written by the Bridge.
        receipt = conn.execute(
            "INSERT INTO bridge_evidence (stream_id,peer_id,kind,detail) SELECT stream_id,peer_id,kind,? "
            "FROM bridge_evidence WHERE kind='session_telemetry' LIMIT 1", (json.dumps(detail),))
        conn.execute("UPDATE bridge_session_monitor_stats SET latest_telemetry_seq=?", (receipt.lastrowid,))

    for command in (["session", "list"], ["session", "show", handle]):
        assert main(["--db", str(db), *command]) == 0
        payload = json.loads(capsys.readouterr().out)
        session = payload["session"] if "session" in payload else payload["items"][0]
        telemetry = session["latest_telemetry"]
        assert telemetry["configured_model"] is None
        assert telemetry["resolved_model"] is None
        assert telemetry["usage"] is None
        assert telemetry["context"]["state"] == "UNKNOWN"
        assert telemetry["context"]["percent"] is None
        if mismatch:
            assert telemetry["context"]["reason"] == "invalid_or_missing_receipt"


def test_session_inspection_refuses_future_schema(tmp_path, capsys):
    import sqlite3

    db = tmp_path / "future.db"
    with sqlite3.connect(db) as conn:
        conn.execute("PRAGMA user_version = 999")

    assert main(["--db", str(db), "session", "list"]) == 6
    assert json.loads(capsys.readouterr().out)["status"] == "SCHEMA_VERSION_ERROR"
