"""Public ask exercises real Bridge claims/certainty, never the v0 dispatcher."""
import sqlite3

import pytest

from peerhub.extensions.ask import ask
from peerhub.core.store import CoreStore, IdempotencyConflictError
from tests.communication.fakes import FakeRuntimeTarget


def test_response_retry_is_idempotent_and_claim_is_released(tmp_path):
    db = tmp_path / "core.db"
    rt = FakeRuntimeTarget()
    rt.script_deliver(("started", "exec-1"), ("terminal", {"response": "answer"}))
    result = ask(db, "cx", "question", request_id="req", runtime=rt)
    assert result["status"] == "delivered" and result["response"] == "answer"
    other = FakeRuntimeTarget()
    replay = ask(db, "cx", "question", request_id="req", runtime=other)
    assert replay["status"] == "recovered_terminal" and replay["response"] == "answer"
    assert other.calls == []
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT kind FROM observations").fetchall() == [("activity",)]
    assert len([r for r in CoreStore(db).read_records(result["stream_id"]) if r.kind == "response"]) == 1
    with pytest.raises(IdempotencyConflictError):
        ask(db, "cx", "changed", request_id="req", runtime=other)
    # Immediate next call succeeds: the preceding claim was explicitly released.
    assert ask(db, "cx", "next", runtime=FakeRuntimeTarget())["status"] == "delivered"


def test_uncertain_retry_never_spawns_again(tmp_path):
    db = tmp_path / "core.db"
    rt = FakeRuntimeTarget()
    rt.script_deliver(("started", "exec-1"), ("timeout", 5))
    assert ask(db, "cx", "question", request_id="req", runtime=rt)["status"] == "uncertain"
    retry = FakeRuntimeTarget()
    assert ask(db, "cx", "question", request_id="req", runtime=retry)["status"] == "blocked_uncertain"
    assert retry.calls == []
    result = ask(db, "cx", "next", request_id="req2", runtime=retry)
    assert result["status"] == "pending_earlier_record" and retry.calls == []


def test_invalid_prompt_does_not_create_store(tmp_path):
    db = tmp_path / "core.db"
    with pytest.raises(ValueError):
        ask(db, "cx", " ", runtime=FakeRuntimeTarget())
    assert not db.exists()


def test_default_cli_ask_has_no_legacy_import(tmp_path, monkeypatch, capsys):
    from peerhub import cli
    from peerhub.extensions import ask as module
    real_ask = module.ask
    monkeypatch.setattr(module, "ask", lambda *a, **kw: real_ask(*a, **kw, runtime=FakeRuntimeTarget()))
    assert not hasattr(cli, "legacy_main")
    monkeypatch.delenv("PEERHUB_CLI", raising=False)
    assert cli.main(["--db", str(tmp_path / "core.db"), "ask", "cx", "hello", "--json"]) == 0
    assert '"certainty": "TERMINAL"' in capsys.readouterr().out


def test_prespawn_is_retryable_without_new_prompt(tmp_path):
    rt = FakeRuntimeTarget()
    rt.script_deliver(("prespawn_error", "missing executable"))
    db = tmp_path / "core.db"
    assert ask(db, "cc", "hello", request_id="one", runtime=rt)["status"] == "failed_not_started"
    assert ask(db, "cc", "hello", request_id="one", runtime=FakeRuntimeTarget())["status"] == "delivered"
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM records WHERE kind='prompt'").fetchone()[0] == 1


def test_request_id_cannot_silently_change_model_binding(tmp_path):
    db = tmp_path / "core.db"
    rt = FakeRuntimeTarget()
    ask(db, "cx", "question", request_id="req", runtime=rt)
    changed = FakeRuntimeTarget()
    changed.binding = lambda: "different-model"
    with pytest.raises(IdempotencyConflictError):
        ask(db, "cx", "question", request_id="req", runtime=changed)


def test_provider_workspace_owns_default_db_even_when_cwd_has_store(tmp_path, monkeypatch):
    from peerhub.cli.app import main
    from peerhub.extensions import ask as module
    cwd_db = tmp_path / ".peerhub" / "m1.db"
    cwd_db.parent.mkdir()
    CoreStore(cwd_db)
    provider = tmp_path / "other"
    provider.mkdir()
    monkeypatch.chdir(tmp_path)
    real_ask = module.ask
    monkeypatch.setattr(module, "ask", lambda *a, **kw: real_ask(*a, **kw, runtime=FakeRuntimeTarget()))
    assert main(["ask", "cx", "hello", "--workspace", str(provider), "--json"]) == 0
    assert (provider / ".peerhub" / "core.db").exists()
    assert CoreStore(cwd_db).get_peer("cx") is None


def test_active_claim_blocks_second_ask_before_prompt_append(tmp_path):
    from peerhub.extensions.bridge_claims import ClaimHeldError
    db = tmp_path / "core.db"
    class Runtime(FakeRuntimeTarget):
        def deliver(self, external_session_id, record, catch_up):
            yield "started", "exec"
            with pytest.raises(ClaimHeldError):
                ask(db, "cx", "concurrent", runtime=FakeRuntimeTarget())
            yield "terminal", {"response": "done"}
    assert ask(db, "cx", "first", runtime=Runtime())["status"] == "delivered"
    assert [r.body for r in CoreStore(db).read_records("peer:cx:chat") if r.kind == "prompt"] == ["first"]
