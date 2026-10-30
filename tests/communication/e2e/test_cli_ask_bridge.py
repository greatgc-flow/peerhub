"""Public ask exercises real Bridge claims/certainty, never the v0 dispatcher."""
import sqlite3

import pytest

from peerhub.extensions.ask import ask
from peerhub.core.store import CoreStore, IdempotencyConflictError
from tests.communication.fakes import FakeRuntimeTarget


@pytest.mark.parametrize("usage", [None, {"input_tokens": 18351, "cached_input_tokens": 18176,
                                        "cache_write_tokens": 0, "output_tokens": 10}])
def test_response_retry_is_idempotent_and_claim_is_released(tmp_path, usage):
    db = tmp_path / "core.db"
    rt = FakeRuntimeTarget()
    payload = {"response": "answer", **({"usage": usage} if usage is not None else {})}
    rt.script_deliver(("started", "exec-1"), ("terminal", payload))
    result = ask(db, "cx", "question", request_id="req", runtime=rt)
    assert result["status"] == "delivered" and result["response"] == "answer"
    other = FakeRuntimeTarget()
    replay = ask(db, "cx", "question", request_id="req", runtime=other)
    assert replay["status"] == "recovered_terminal" and replay["response"] == "answer"
    assert other.calls == []
    response = next(r for r in CoreStore(db).read_records(result["stream_id"]) if r.kind == "response")
    for data in (result, replay, response.metadata):
        if usage is None:
            assert "usage" not in data
        else:
            assert data["usage"] == usage
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
    assert cli.main(["--db", str(tmp_path / "core.db"), "ask", "cx", "hello", "--json"]) == 0
    assert '"certainty": "TERMINAL"' in capsys.readouterr().out


@pytest.mark.parametrize("json_output", [False, True])
def test_cli_usage_is_json_only(tmp_path, monkeypatch, capsys, json_output):
    import json
    from peerhub import cli
    from peerhub.extensions import ask as module
    usage = {"input_tokens": 20, "output_tokens": 2}
    rt = FakeRuntimeTarget()
    rt.script_deliver(("started", "exec-usage"), ("terminal", {"response": "answer", "usage": usage}))
    real_ask = module.ask
    monkeypatch.setattr(module, "ask", lambda *a, **kw: real_ask(*a, **kw, runtime=rt))
    argv = ["--db", str(tmp_path / "core.db"), "ask", "cx", "hello"]
    assert cli.main(argv + (["--json"] if json_output else [])) == 0
    output = capsys.readouterr()
    assert output.err == ""
    if json_output:
        assert json.loads(output.out)["usage"] == usage
    else:
        assert output.out.startswith("answer\nsession: sess_")
        assert output.out.endswith(" fresh gen1 reason=no_prior\n")


def test_prespawn_is_retryable_without_new_prompt(tmp_path):
    rt = FakeRuntimeTarget()
    rt.script_deliver(("prespawn_error", "missing executable"))
    db = tmp_path / "core.db"
    assert ask(db, "cc", "hello", request_id="one", runtime=rt)["status"] == "failed_not_started"
    assert ask(db, "cc", "hello", request_id="one", runtime=FakeRuntimeTarget())["status"] == "delivered"
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM records WHERE kind='prompt'").fetchone()[0] == 1


def test_changed_binding_supersedes_proven_prespawn_failure(tmp_path):
    db = tmp_path / "core.db"
    failed = FakeRuntimeTarget()
    failed.binding = lambda: "workspace=nonexistent"
    failed.script_deliver(("prespawn_error", "workspace is not a directory"))
    first = ask(db, "cx", "first", stream_id="shared", request_id="R1", runtime=failed)
    assert (first["status"], first["certainty"]) == ("failed_not_started", "NOT_STARTED")

    corrected = FakeRuntimeTarget()
    corrected.binding = lambda: "workspace=corrected"
    second = ask(db, "cx", "second", stream_id="shared", request_id="R2", runtime=corrected)
    assert second["status"] == "delivered"
    assert second["record_id"] == second["requested_record_id"]
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT certainty,acked FROM bridge_deliveries WHERE record_id=?",
                            (first["record_id"],)).fetchone() == ("NOT_STARTED", 1)
        assert conn.execute("SELECT COUNT(*) FROM bridge_invocations WHERE delivery_id=?",
                            (first["delivery_id"],)).fetchone()[0] == 1
    # The abandoned request remains a failure; replay must never execute it.
    failed.calls.clear()
    replay = ask(db, "cx", "first", stream_id="shared", request_id="R1", runtime=failed)
    assert replay["status"] == "failed_not_started" and failed.calls == []
    with pytest.raises(IdempotencyConflictError):
        ask(db, "cx", "first", stream_id="shared", request_id="R1", runtime=corrected)
    assert ask(db, "cx", "third", stream_id="shared", request_id="R3", runtime=corrected)["status"] == "delivered"


@pytest.mark.parametrize("started", [False, True])
@pytest.mark.parametrize("prior_prespawn_failure", [False, True])
def test_changed_binding_never_supersedes_uncertain_failure(tmp_path, started, prior_prespawn_failure):
    db = tmp_path / "core.db"
    failed = FakeRuntimeTarget()
    if prior_prespawn_failure:
        failed.script_deliver(("prespawn_error", "missing executable"))
        assert ask(db, "cx", "first", request_id="R1", runtime=failed)["status"] == "failed_not_started"
    failed.script_deliver(*([("started", "exec")] if started else []), ("timeout", 5))
    first = ask(db, "cx", "first", request_id="R1", runtime=failed)
    assert first["status"] == "uncertain"
    corrected = FakeRuntimeTarget()
    corrected.binding = lambda: "different-workspace"
    second = ask(db, "cx", "second", request_id="R2", runtime=corrected)
    assert second["status"] == "pending_earlier_record"
    assert second["processed_status"] == "blocked_uncertain"
    assert corrected.calls == []
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT acked FROM bridge_deliveries WHERE record_id=?",
                            (first["record_id"],)).fetchone() == (0,)


def test_changed_binding_never_supersedes_crashed_retry_after_prespawn_failure(tmp_path):
    db = tmp_path / "core.db"
    failed = FakeRuntimeTarget()
    failed.script_deliver(("prespawn_error", "missing executable"))
    first = ask(db, "cx", "first", request_id="R1", runtime=failed)
    assert first["status"] == "failed_not_started"

    def crash_before_start_evidence():
        raise SystemExit

    # Same-binding retry leaves a NEW unresolved marker, despite old failure evidence.
    failed.script_deliver(("call", crash_before_start_evidence))
    with pytest.raises(SystemExit):
        ask(db, "cx", "first", request_id="R1", runtime=failed)
    corrected = FakeRuntimeTarget(binding="corrected")
    second = ask(db, "cx", "second", request_id="R2", runtime=corrected)
    assert second["processed_status"] == "blocked_uncertain"
    assert corrected.calls == []
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT certainty,acked FROM bridge_deliveries WHERE record_id=?",
                            (first["record_id"],)).fetchone() == ("MAY_HAVE_STARTED", 0)


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
    cwd_db = tmp_path / ".peerhub" / "core.db"
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


def test_older_work_progress_reported_without_uncertain_label(tmp_path):
    from peerhub.cli.recovery import select_guidance_code
    from peerhub.core.models import Peer, Stream
    from peerhub.core.store import utc_now_iso
    db = tmp_path / "core.db"
    store = CoreStore(db)
    store.register_peer(Peer(peer_id="cx"))
    store.register_peer(Peer(peer_id="user"))
    store.create_stream(Stream(stream_id="peer:cx:chat", members=["user", "cx"]))
    rt = FakeRuntimeTarget()
    store.append_record(
        stream_id="peer:cx:chat",
        author_peer_id="user",
        kind="prompt",
        body="earlier prompt",
        targets=["cx"],
        idempotency_key="ask:earlier-1",
        metadata={
            "runtime_kind": rt.runtime_kind,
            "binding": rt.binding(),
            "session_selector": "latest",
            "adapter_fingerprint": rt.fingerprint(),
        },
        created_at=utc_now_iso(),
    )
    rt.script_deliver(("started", "exec-earlier"), ("terminal", {"response": "earlier answer"}))
    res = ask(db, "cx", "second prompt", stream_id="peer:cx:chat", runtime=rt)
    assert res["status"] == "pending_earlier_record"
    assert res.get("processed_status") == "delivered"
    assert res.get("response") == "earlier answer"
    guidance = select_guidance_code(
        status=res.get("status"),
        processed_status=res.get("processed_status"),
        certainty=res.get("certainty"),
    )
    assert guidance == ""


def test_stream_peer_mismatch_single_peer_binding(tmp_path):
    from peerhub.extensions.ask import StreamPeerMismatchError
    db = tmp_path / "core.db"
    rt = FakeRuntimeTarget()
    ask(db, "cx", "first to cx", stream_id="shared-stream", runtime=rt)
    with pytest.raises(StreamPeerMismatchError, match="cannot dispatch to 'cc'"):
        ask(db, "cc", "second to cc", stream_id="shared-stream", runtime=rt)


def test_historical_mixed_targets_prompt_rejected_by_prompt_guard(tmp_path):
    from peerhub.extensions.ask import StreamPeerMismatchError
    from peerhub.core.models import Peer, Stream
    from peerhub.core.store import utc_now_iso
    db = tmp_path / "core.db"
    store = CoreStore(db)
    store.register_peer(Peer(peer_id="cx"))
    store.register_peer(Peer(peer_id="user"))
    store.create_stream(Stream(stream_id="mixed-stream", members=["user", "cx"]))

    meta = {
        "runtime_kind": "cx",
        "binding": "session",
        "session_selector": "test",
        "adapter_fingerprint": "fp-123",
    }
    store.append_record(
        stream_id="mixed-stream",
        author_peer_id="user",
        kind="prompt",
        body="mixed targets prompt",
        targets=["cx", "cc"],
        idempotency_key="ask:mixed-1",
        metadata=meta,
        created_at=utc_now_iso(),
    )

    rt = FakeRuntimeTarget()
    with pytest.raises(StreamPeerMismatchError, match="cannot dispatch to 'cx'"):
        ask(db, "cx", "subsequent ask to cx", stream_id="mixed-stream", runtime=rt)


def test_partial_metadata_does_not_bind_stream_peer(tmp_path):
    from peerhub.core.models import Peer, Stream
    from peerhub.core.store import utc_now_iso
    db = tmp_path / "core.db"
    store = CoreStore(db)
    store.register_peer(Peer(peer_id="cx"))
    store.register_peer(Peer(peer_id="user"))
    store.create_stream(Stream(stream_id="partial-stream", members=["user", "cx"]))

    meta = {
        "runtime_kind": "cx",
    }
    store.append_record(
        stream_id="partial-stream",
        author_peer_id="user",
        kind="prompt",
        body="partial metadata prompt",
        targets=[],
        idempotency_key="ask:partial-1",
        metadata=meta,
        created_at=utc_now_iso(),
    )

    rt = FakeRuntimeTarget()
    res = ask(db, "cc", "ask to cc", stream_id="partial-stream", runtime=rt)
    assert res["status"] in ("delivered", "pending_earlier_record")


