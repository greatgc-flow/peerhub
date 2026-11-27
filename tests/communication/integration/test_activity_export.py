"""Public activity contract on disposable source stores; no provider credentials."""
import json
import shutil
import sqlite3
from contextlib import closing

import pytest

from peerhub.cli.app import main
from peerhub.extensions.ask import ask
from peerhub.extensions.profile_stats import mark_outcome
from tests.communication.fakes import FakeRuntimeTarget


def deliver(db, stream="z", request="fixture-request"):
    runtime = FakeRuntimeTarget().script_deliver(("started", "fixture-execution"),
                                                ("terminal", {"response": "PRIVATE-REPLY"}))
    receipt = ask(db, "cx", "PRIVATE-PROMPT", stream_id=stream, request_id=request, runtime=runtime)
    assert receipt["status"] == "delivered"
    return receipt["requested_record_id"]


def page(capsys, db, *args, code=0):
    argv = ["--db", str(db)] if db is not None else []
    assert main([*argv, "activity", *args, "--json"]) == code
    captured = capsys.readouterr()
    assert "PRIVATE-" not in captured.out + captured.err
    return json.loads(captured.out)


def test_export_late_sources_have_independent_checkpoints(tmp_path, capsys):
    db = tmp_path / "source.db"
    rid = deliver(db)
    first = page(capsys, db, "export", "--limit", "1")
    assert first["items"][0]["record_id"] == rid
    assert first["has_more"]
    cursor = first["next_cursor"]
    original = page(capsys, db, "export", "--cursor", cursor)
    deliver(db, "a", "another")
    for state in ("accepted", "rejected", "retried"):
        mark_outcome(db, rid, state)
    assert page(capsys, db, "export", "--cursor", cursor) == original
    later = page(capsys, db, "export", "--cursor", original["next_cursor"])
    assert [e["outcome"] for e in later["items"] if e["kind"] == "peer.outcome"] == ["accepted", "rejected", "retried"]
    assert any(e["kind"] == "ai.prompt" and e["stream_id"] == "a" for e in later["items"])
    assert not page(capsys, db, "export", "--cursor", later["next_cursor"])["items"]


def test_scoped_show_linkage_privacy_and_no_writes(tmp_path, capsys):
    db = tmp_path / "source.db"
    rid = deliver(db)
    deliver(db, "a", "other")
    with closing(sqlite3.connect(db)) as conn:
        before = list(conn.iterdump())
        result = page(capsys, db, "show", rid)
        assert list(conn.iterdump()) == before
    events = result["items"]
    assert {e["stream_id"] for e in events} == {"z"}
    prompt, response = [e for e in events if e["kind"].startswith("ai.")]
    assert response["reply_to"] == prompt["record_id"] == rid
    assert prompt["request_id"] == "fixture-request"
    assert any(e.get("event") == "offset_acked" and e["acked"] for e in events)
    assert any(e["kind"] == "peer.usage" and e["usage"]["coverage"]["state"] == "UNKNOWN" for e in events)
    assert all(len(e["source_digest"]) == 64 for e in events)
    assert page(capsys, db, "export", "--cursor", result["next_cursor"], code=2)["error"] == "CURSOR_SCOPE_MISMATCH"


@pytest.mark.parametrize("changed", ["clone", "workspace.generation", "restore.epoch"])
def test_restore_requires_new_snapshot(tmp_path, capsys, changed):
    db = tmp_path / "source.db"
    deliver(db)
    cursor = page(capsys, db, "export")["next_cursor"]
    if changed == "clone":
        other = tmp_path / "clone.db"
        shutil.copyfile(db, other)
        db = other
    else:
        (tmp_path / changed).write_text("2")
    assert page(capsys, db, "export", "--cursor", cursor, code=2)["error"] == "RESNAPSHOT_REQUIRED"


def test_requires_existing_explicit_store(tmp_path, capsys, monkeypatch):
    db = tmp_path / "absent.db"
    monkeypatch.setenv("PEERHUB_DB", str(db))
    page(capsys, None, "export", code=2)
    page(capsys, db, "export", code=5)
    assert not db.exists()
