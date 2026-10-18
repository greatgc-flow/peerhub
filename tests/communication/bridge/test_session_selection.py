"""PR3 selectors: immutable intent, exact identity and durable rejection oracles."""
import json
import sqlite3

import pytest

from peerhub.core.store import IdempotencyConflictError
from peerhub.extensions.ask import ask
from peerhub.extensions import bridge as errors
from tests.communication.bridge_helpers import bseed, sql
from tests.communication.bridge.test_retained_sessions import add
from tests.communication.fakes.runtime import NativeRuntimeTarget, FakeRuntimeTarget
from tests.communication.harness.bridge import BridgeHarness


@pytest.mark.parametrize("runtime_cls", [NativeRuntimeTarget, FakeRuntimeTarget])
def test_default_fresh_exact_and_older_session_delta(tmp_path, runtime_cls):
    db = tmp_path / "ask.db"
    rt = runtime_cls()
    first = ask(db, "cx", "A", request_id="a", runtime=rt)
    continued = ask(db, "cx", "continue", request_id="c", runtime=rt)
    fresh = ask(db, "cx", "B", request_id="b", fresh=True, runtime=rt)
    exact = ask(db, "cx", "A again", request_id="e", session=first["session"]["handle"], runtime=rt)
    assert continued["session"]["handle"] == exact["session"]["handle"] == first["session"]["handle"]
    assert exact["session"]["mode"] == "resumed"
    assert fresh["session"]["handle"] != first["session"]["handle"]
    assert fresh["session"]["reason"] == "explicit_fresh"
    assert fresh["session"]["prior_handle"] == first["session"]["handle"]
    assert first["requested_record_id"] in rt.catch_ups[-2]
    assert fresh["response_record_id"] in rt.catch_ups[-1]
    with sqlite3.connect(db) as conn:
        selectors = [json.loads(r[0])["session_selector"] for r in conn.execute("SELECT metadata_json FROM records WHERE kind='prompt' ORDER BY position")]
        receipts = [json.loads(r[0]) for r in conn.execute("SELECT detail FROM bridge_evidence WHERE kind='session_call' ORDER BY seq")]
    assert selectors == ["latest", "latest", "fresh", "sess:" + first["session"]["handle"]]
    assert receipts[-1]["handle"] == first["session"]["handle"]


@pytest.mark.parametrize("kwargs", [{"fresh": True, "session": "sess_0000000000000001"}, {"session": "bad"}, {"session": ""}])
def test_invalid_selector_before_store_creation(tmp_path, kwargs):
    db = tmp_path / "absent.db"
    rt = NativeRuntimeTarget()
    with pytest.raises(ValueError):
        ask(db, "cx", "x", runtime=rt, **kwargs)
    assert not db.exists() and rt.calls == []


@pytest.mark.parametrize("change,error", [
    ("missing", "SessionNotFoundError"), ("stream", "SessionStreamMismatchError"),
    ("peer", "SessionPeerMismatchError"), ("binding", "SessionBindingMismatchError"),
    ("fingerprint", "SessionFingerprintMismatchError"), ("runtime", "SessionUnavailableError"),
    ("capability", "SessionUnavailableError"), ("lost", "SessionLostError"),
    ("vendor", "SessionUnavailableError"), ("watermark", "SessionUnavailableError"),
    ("version", "SessionUnavailableError"), ("lineage", "SessionWorkspaceMismatchError"),
    ("expired", "SessionTombstonedError"),
])
def test_direct_exact_rejection_never_appends_allocates_or_invokes(tmp_path, change, error):
    db = tmp_path / "ask.db"
    rt = NativeRuntimeTarget()
    rt.runtime_kind = "cx"
    first = ask(db, "cx", "first", runtime=rt)
    handle = first["session"]["handle"]
    kw = {}
    if change == "missing":
        handle = "sess_0000000000000000"
    elif change in ("peer", "stream"):
        kw = {"peer_id": "other", "stream_id": first["stream_id"]} if change == "peer" else {"stream_id": "other"}
    elif change == "binding":
        rt.set_binding("changed")
    elif change == "fingerprint":
        rt.set_fingerprint("changed")
    elif change == "runtime":
        rt.runtime_kind = "other"
    elif change == "capability":
        rt.resumable = False
    else:
        changes = {"lost": "state='LOST'", "vendor": "vendor_session_id=NULL", "watermark": "context_watermark=NULL",
                   "version": "context_watermark_version=0", "lineage": "workspace_generation='other'", "expired": "last_seen=0"}
        with sqlite3.connect(db) as conn:
            if change == "lineage":
                conn.execute("DROP TRIGGER bridge_history_identity_immutable")
            conn.execute("UPDATE bridge_session_history SET " + changes[change])
    before = list(rt.calls)
    with pytest.raises(getattr(errors, error)):
        ask(db, kw.pop("peer_id", "cx"), "reject", session=handle, runtime=rt, **kw)
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM records WHERE kind='prompt'").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM bridge_session_history").fetchone()[0] == 1
        if change == "expired":
            assert conn.execute("SELECT state FROM bridge_session_history").fetchone()[0] == "TOMBSTONED"
    assert rt.calls == before


def test_queued_exact_failure_is_consumed_atomically_and_replayable(tmp_path):
    h = BridgeHarness(tmp_path / "ws")
    bseed(h, n=0)
    rejected = add(h, "bad", kind="prompt", metadata={"session_selector": "sess:sess_0000000000000000"})
    add(h, "good", kind="prompt", metadata={"session_selector": "fresh"})
    rt = NativeRuntimeTarget()
    result = h.delivery_cycle("b", "s", rt)
    assert result.status == "failed_not_started" and result.record_id == rejected.record_id
    assert result.detail["error"]["code"] == "session_not_found"
    assert rt.calls == []
    assert sql(h, "SELECT certainty,acked FROM bridge_deliveries") == [("NOT_STARTED", 1)]
    assert h.store.get_offset("b", "s").read_through_position == rejected.position
    assert sql(h, "SELECT COUNT(*) FROM bridge_evidence WHERE kind='request_rejected'") == [(1,)]
    assert h.delivery_cycle("b", "s", rt).status == "delivered"


@pytest.mark.parametrize("selector", ["fresh", "exact"])
def test_replay_after_tombstoning_and_changed_selector_conflict(tmp_path, monkeypatch, selector):
    db = tmp_path / "ask.db"
    rt = NativeRuntimeTarget()
    first = ask(db, "cx", "seed", runtime=rt)
    kw = {"fresh": True} if selector == "fresh" else {"session": first["session"]["handle"]}
    original = ask(db, "cx", "same", request_id="id", runtime=rt, **kw)
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE bridge_session_history SET state='TOMBSTONED',tombstoned_at=1")
        conn.execute("DELETE FROM bridge_sessions")
    monkeypatch.setattr(errors.Bridge, "__init__", lambda *a, **k: pytest.fail("replay checked liveness"))
    rt.calls.clear()
    replay = ask(db, "cx", "same", request_id="id", runtime=rt, **kw)
    assert replay["session"] == original["session"] and rt.calls == []
    with pytest.raises(IdempotencyConflictError):
        ask(db, "cx", "same", request_id="id", runtime=rt)


@pytest.mark.parametrize("outcome", ["missing", "unsupported", "rejected"])
def test_exact_resume_failure_never_creates_substitute(tmp_path, outcome):
    h = BridgeHarness(tmp_path / "ws")
    bseed(h)
    rt = NativeRuntimeTarget()
    first = h.delivery_cycle("b", "s", rt)
    add(h, "exact", kind="prompt", metadata={"session_selector": "sess:" + first.session["handle"]})
    rt.script_resume(outcome)
    result = h.delivery_cycle("b", "s", rt)
    assert result.status == "failed_not_started" and result.session is None
    assert rt.count("create") == 1 and rt.count("deliver") == 1
    assert sql(h, "SELECT COUNT(*) FROM bridge_session_history") == [(1,)]


def test_exact_delta_overflow_is_rejected_before_resume(tmp_path):
    from peerhub.extensions.catchup import CatchUpBudget
    h = BridgeHarness(tmp_path / "ws", catch_up_budget=CatchUpBudget(max_bytes=200))
    bseed(h)
    rt = NativeRuntimeTarget()
    first = h.delivery_cycle("b", "s", rt)
    add(h, "x" * 1000, author="b")
    add(h, "exact", kind="prompt", metadata={"session_selector": "sess:" + first.session["handle"]})
    result = h.delivery_cycle("b", "s", rt)
    assert result.detail["error"]["code"] == "session_delta_overflow"
    assert rt.count("resume") == 0 and rt.count("create") == 1


@pytest.mark.parametrize("selector", ["fresh", "sess:sess_0000000000000000"])
def test_uncertain_work_blocks_all_selectors(tmp_path, selector):
    h = BridgeHarness(tmp_path / "ws")
    bseed(h)
    rt = NativeRuntimeTarget()
    rt.script_deliver(("started", "exec"), ("timeout", 1))
    assert h.delivery_cycle("b", "s", rt).status == "uncertain"
    add(h, "next", kind="prompt", metadata={"session_selector": selector})
    before = list(rt.calls)
    assert h.delivery_cycle("b", "s", rt).status == "blocked_uncertain"
    assert rt.calls == before


def test_removed_python_alias_is_rejected(tmp_path):
    with pytest.raises(TypeError, match="session_handle"):
        ask(tmp_path / "absent.db", "cx", "x", session_handle="sess_0000000000000001")


def test_direct_overflow_is_checked_before_prompt_append(tmp_path, monkeypatch):
    from peerhub.extensions.catchup import CatchUpBudget
    from peerhub.core.store import CoreStore
    db = tmp_path / "ask.db"
    rt = NativeRuntimeTarget()
    first = ask(db, "cx", "first", runtime=rt)
    CoreStore(db).append_record(stream_id=first["stream_id"], author_peer_id="user", kind="message",
                               body="x" * 1000, idempotency_key="large", created_at="2026-10-14T00:00:00Z")
    original = errors.Bridge.__init__
    def bounded(self, *a, **kw):
        original(self, *a, **kw, catch_up_budget=CatchUpBudget(max_bytes=200))
    monkeypatch.setattr(errors.Bridge, "__init__", bounded)
    with pytest.raises(errors.SessionDeltaOverflowError):
        ask(db, "cx", "reject", session=first["session"]["handle"], runtime=rt)
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM records WHERE kind='prompt'").fetchone()[0] == 1
    assert rt.count("resume") == 0 and rt.count("create") == 1


def test_queued_expiry_and_claim_revalidation_before_resume(tmp_path):
    h = BridgeHarness(tmp_path / "ws")
    bseed(h)
    rt = NativeRuntimeTarget()
    first = h.delivery_cycle("b", "s", rt)
    queued = add(h, "exact", kind="prompt", metadata={"session_selector": "sess:" + first.session["handle"]})
    h.clock.advance(30 * 86400 + 1)
    result = h.delivery_cycle("b", "s", rt)
    assert result.detail["error"]["code"] == "session_tombstoned"
    assert sql(h, "SELECT state FROM bridge_session_history") == [("TOMBSTONED",)]
    assert h.store.get_offset("b", "s").read_through_position == queued.position
    assert rt.count("resume") == 0 and rt.count("create") == 1


def test_selector_rejection_storage_fault_rolls_back_result_evidence_and_offset(tmp_path):
    h = BridgeHarness(tmp_path / "ws")
    bseed(h, n=0)
    rec = add(h, "exact", kind="prompt", metadata={"session_selector": "sess:sess_0000000000000000"})
    with sqlite3.connect(h.db_path) as conn:
        conn.execute("CREATE TRIGGER refuse_rejection BEFORE INSERT ON bridge_evidence WHEN NEW.kind='offset_acked' BEGIN SELECT RAISE(ABORT,'injected storage fault'); END")
    rt = NativeRuntimeTarget()
    with pytest.raises(Exception, match="injected storage fault"):
        h.delivery_cycle("b", "s", rt)
    assert h.store.get_offset("b", "s").read_through_position < rec.position
    assert sql(h, "SELECT acked,result_json FROM bridge_deliveries") == [(0, None)]
    assert sql(h, "SELECT COUNT(*) FROM bridge_evidence WHERE kind='request_rejected'") == [(0,)]
    assert rt.calls == []


def test_queued_rejection_replays_before_liveness_and_changed_intent_conflicts(tmp_path, monkeypatch):
    from peerhub.core.models import Peer, Stream
    from peerhub.core.store import CoreStore
    from peerhub.core.workspace import Workspace
    db = tmp_path / "ask.db"
    rt = NativeRuntimeTarget()
    workspace = Workspace(db.parent)
    store = CoreStore(db)
    for peer in ("user", "cx"):
        store.register_peer(Peer(peer_id=peer))
    store.create_stream(Stream(stream_id="s", members=["user", "cx"]))
    handle = "sess_0000000000000000"
    store.append_record(stream_id="s", author_peer_id="user", kind="prompt", body="queued",
                        targets=["cx"], idempotency_key="ask:reject", created_at="2026-10-14T00:00:00Z",
                        metadata={"runtime_kind": rt.runtime_kind, "binding": rt.binding(), "session_selector": "sess:" + handle,
                                  "adapter_fingerprint": rt.fingerprint(), "workspace_generation": workspace.generation()})
    kw = dict(stream_id="s", request_id="reject", session=handle, runtime=rt)
    original = ask(db, "cx", "queued", **kw)
    assert original["status"] == "failed_not_started" and original["error"]["code"] == "session_not_found"
    monkeypatch.setattr(errors.Bridge, "__init__", lambda *a, **k: pytest.fail("rejection replay constructed Bridge"))
    replay = ask(db, "cx", "queued", **kw)
    assert replay["error"] == original["error"] and rt.calls == []
    with pytest.raises(IdempotencyConflictError):
        ask(db, "cx", "queued", stream_id="s", request_id="reject", fresh=True, runtime=rt)


def test_open_marker_and_lost_claim_never_qualify_for_selector_consumption(tmp_path):
    from peerhub.extensions.bridge_claims import StaleClaimError
    h = BridgeHarness(tmp_path / "ws")
    (rec,) = bseed(h)
    persisted = h.store.read_records("s", rec.position - 1, 1)[0]
    token = h.acquire_claim("b", "s", "owner", lease_sec=1)
    did = h.bridge.begin_attempt(token, persisted)
    h.bridge._mark_invocation(token, did)
    with pytest.raises(errors.InvocationMarkerError):
        h.bridge._reject_selector(token, persisted, did, {"code": "session_not_found", "message": "missing"})
    h.clock.advance(2)
    h.acquire_claim("b", "s", "other")
    with pytest.raises(StaleClaimError):
        h.bridge._reject_selector(token, persisted, did, {"code": "session_not_found", "message": "missing"})
    assert sql(h, "SELECT acked,result_json FROM bridge_deliveries") == [(0, None)]


@pytest.mark.parametrize("change", ["fingerprint", "lineage"])
def test_complete_identity_is_checked_before_cached_replay(tmp_path, change):
    from peerhub.core.workspace import Workspace
    db = tmp_path / "ask.db"
    rt = NativeRuntimeTarget()
    ask(db, "cx", "same", request_id="id", runtime=rt)
    if change == "fingerprint":
        rt.set_fingerprint("changed")
    else:
        Workspace(db.parent).replace_generation()
    before = list(rt.calls)
    with pytest.raises(IdempotencyConflictError):
        ask(db, "cx", "same", request_id="id", runtime=rt)
    assert rt.calls == before


@pytest.mark.parametrize("selector", ["fresh", "sess:sess_0000000000000000"])
def test_queued_effective_intent_is_revalidated_before_allocation(tmp_path, selector):
    h = BridgeHarness(tmp_path / "ws")
    bseed(h, n=0)
    add(h, "queued", kind="prompt", metadata={"session_selector": selector, "binding": "old-binding"})
    rt = NativeRuntimeTarget()
    result = h.delivery_cycle("b", "s", rt)
    assert result.detail["error"]["code"] == "session_binding_mismatch"
    assert rt.calls == [] and sql(h, "SELECT COUNT(*) FROM bridge_session_history") == [(0,)]
