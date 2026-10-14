"""PR2 retained identity, fenced writers and independent durable SQL oracles."""
import json
import re
import sqlite3

import pytest

from peerhub.core.models import Peer, Stream
from peerhub.core.store import CoreStore, IdempotencyConflictError
from peerhub.extensions.ask import ask
from peerhub.extensions.bridge import Bridge, _DDL
from peerhub.extensions import bridge_registry
from peerhub.extensions.bridge_claims import ClaimStore, StaleClaimError
from tests.communication.bridge_helpers import bseed, sql
from tests.communication.control_helpers import ctl
from tests.communication.fakes import CrashInjected, CrashInjector, FakeRuntimeTarget
from tests.communication.fakes.runtime import NativeRuntimeTarget
from tests.communication.harness.bridge import BridgeHarness
from tests.communication.helpers import req


def add(h, key, **kw):
    return h.append_record(req(key=key, body=key, **kw))


def history(h):
    return sql(h, "SELECT session_handle, session_generation, state FROM bridge_session_history ORDER BY session_generation")


def counts(h, handle):
    return sql(h, "SELECT reuse_count, fresh_count, counts_complete FROM bridge_session_monitor_stats WHERE session_handle=?", (handle,))[0]


def test_initialized_registry_reopen_does_not_reseed_history(tmp_path):
    h = BridgeHarness(tmp_path / "ws")
    bseed(h)
    h.delivery_cycle("b", "s", FakeRuntimeTarget())
    statements = []
    with h.cs.transaction() as conn:
        before = [tuple(r) for r in conn.execute("SELECT * FROM bridge_session_counters")]
        scopes = [tuple(r) for r in conn.execute("SELECT * FROM bridge_session_monitor_scope")]
        conn.set_trace_callback(statements.append)
        try:
            bridge_registry.initialize(conn, h.bridge._upgrade_session_context, h.bridge._fire)
        finally:
            conn.set_trace_callback(None)
        assert [tuple(r) for r in conn.execute("SELECT * FROM bridge_session_counters")] == before
        assert [tuple(r) for r in conn.execute("SELECT * FROM bridge_session_monitor_scope")] == scopes
    assert not any("GROUP BY" in s or "UNION ALL" in s for s in statements)
    assert not any("INSERT INTO bridge_session_counters" in s or "INSERT INTO bridge_session_monitor_scope" in s for s in statements)


@pytest.mark.parametrize("missing", ["bridge_session_counters", "bridge_session_monitor_scope"])
def test_missing_registry_seed_table_reseeds_without_rewinding(tmp_path, missing):
    h = BridgeHarness(tmp_path / "ws")
    bseed(h)
    h.delivery_cycle("b", "s", FakeRuntimeTarget())
    with h.cs.transaction() as conn:
        if missing == "bridge_session_monitor_scope":
            conn.execute("UPDATE bridge_session_counters SET next_generation=100")
        conn.execute(f"DROP TABLE {missing}")
    h.reopen()
    expected = 100 if missing == "bridge_session_monitor_scope" else 2
    assert sql(h, "SELECT next_generation FROM bridge_session_counters") == [(expected,)]
    expected_counts = (None, None, 0) if missing == "bridge_session_monitor_scope" else (0, 1, 1)
    assert sql(h, "SELECT reuse_count,fresh_count,counts_complete FROM bridge_session_monitor_scope") == [expected_counts]


def test_delivery_only_scope_is_initialized_by_allocation_not_reopen(tmp_path):
    h = BridgeHarness(tmp_path / "ws")
    (rec,) = bseed(h)
    with h.cs.transaction() as conn:
        conn.execute("INSERT INTO bridge_deliveries(delivery_id,seq,stream_id,peer_id,record_id,record_position,attempt,certainty,claim_generation) "
                     "VALUES ('unstarted',1,'s','b',?,?,1,'NOT_STARTED',1)", (rec.record_id, rec.position))
    h.reopen()
    assert sql(h, "SELECT * FROM bridge_session_monitor_scope") == []
    result = h.delivery_cycle("b", "s", FakeRuntimeTarget())
    assert result.status == "delivered"
    assert sql(h, "SELECT reuse_count,fresh_count,counts_complete,coverage_start_seq FROM bridge_session_monitor_scope") == [(0, 1, 1, 0)]


@pytest.mark.parametrize("runtime_cls", [NativeRuntimeTarget, FakeRuntimeTarget])
def test_binding_a_b_a_selects_older_compatible_history_and_injects_other_session_response(tmp_path, runtime_cls):
    h = BridgeHarness(tmp_path / "ws")
    bseed(h)
    rt = runtime_cls()
    first = h.delivery_cycle("b", "s", rt)
    a = first.session
    rt.set_binding("other")
    add(h, "second")
    second = h.delivery_cycle("b", "s", rt)
    assert second.session["generation"] == 2 and second.session["prior_handle"] == a["handle"]
    rt.set_binding("model-a/profile-1")
    add(h, "third")
    third = h.delivery_cycle("b", "s", rt)
    assert third.session["handle"] == a["handle"] and third.session["mode"] == "resumed"
    assert second.response_record_id in rt.catch_ups[-1]
    assert first.record_id not in rt.catch_ups[-1]
    assert sql(h, "SELECT session_handle, session_generation FROM bridge_sessions") == [(a["handle"], 1)]
    assert len(history(h)) == 2 and counts(h, a["handle"]) == (1, 1, 1)


def test_retention_cap_tombstones_audit_rows_and_generation_never_rewinds(tmp_path):
    h = BridgeHarness(tmp_path / "ws")
    bseed(h)
    rt = NativeRuntimeTarget()
    handles = []
    for i in range(23):
        if i:
            add(h, str(i))
        rt.set_binding(str(i))
        result = h.delivery_cycle("b", "s", rt)
        handles.append(result.session["handle"])
        assert result.session["generation"] == i + 1
        assert re.fullmatch(r"sess_[0-9a-f]{16}", handles[-1])
    assert len(set(handles)) == 23
    assert sql(h, "SELECT COUNT(*) FROM bridge_session_history WHERE state!='TOMBSTONED'") == [(20,)]
    assert sql(h, "SELECT COUNT(*) FROM bridge_session_history WHERE state='TOMBSTONED'") == [(3,)]
    assert counts(h, handles[0]) == (0, 1, 1)
    assert sql(h, "SELECT next_generation FROM bridge_session_counters") == [(24,)]
    h.reopen()
    assert len(history(h)) == 23


def test_inactivity_boundary_and_expired_prior_are_committed_before_fresh(tmp_path):
    h = BridgeHarness(tmp_path / "ws")
    bseed(h)
    rt = NativeRuntimeTarget()
    first = h.delivery_cycle("b", "s", rt)
    h.clock.advance(30 * 86400)
    add(h, "boundary")
    assert h.delivery_cycle("b", "s", rt).session["handle"] == first.session["handle"]
    h.clock.advance(30 * 86400 + 1)
    add(h, "expired")
    fresh = h.delivery_cycle("b", "s", rt)
    assert fresh.session["reason"] == "retention_max_age"
    assert fresh.session["prior_handle"] == first.session["handle"]
    assert history(h)[0][2] == "TOMBSTONED"


@pytest.mark.parametrize("started", [False, True])
def test_certainty_receipts_are_exact_and_uncertain_execution_never_falls_back(tmp_path, started):
    h = BridgeHarness(tmp_path / "ws")
    bseed(h)
    rt = NativeRuntimeTarget()
    rt.runtime_kind = "cx"
    events = [("started", "exec"), ("started", "exec")] if started else []
    rt.script_deliver(*events, ("runtime_error", "disconnect"))
    result = h.delivery_cycle("b", "s", rt)
    assert result.status == "uncertain" and result.session["handle"]
    assert sql(h, "SELECT state,vendor_session_id FROM bridge_session_history") == [("LOST", None)]
    assert sql(h, "SELECT session_handle,session_generation FROM bridge_sessions") == []
    assert sql(h, "SELECT COUNT(*) FROM bridge_evidence WHERE kind='session_call'") == [(int(started),)]
    assert counts(h, result.session["handle"]) == (0, int(started), 1)
    calls = list(rt.calls)
    assert h.delivery_cycle("b", "s", rt).status == "blocked_uncertain" and rt.calls == calls
    assert sql(h, "SELECT state FROM bridge_session_history") == [("LOST",)]


@pytest.mark.parametrize("point", ["bridge.after_terminal_evidence_before_response_append", "bridge.after_terminal_evidence_before_offset_ack"])
def test_terminal_recovery_and_duplicate_callback_never_duplicate_receipts(tmp_path, point):
    crash = CrashInjector()
    h = BridgeHarness(tmp_path / "ws", fault_hook=crash)
    bseed(h)
    rt = NativeRuntimeTarget()
    crash.arm(point)
    with pytest.raises(CrashInjected):
        h.delivery_cycle("b", "s", rt)
    crash.disarm()
    h.reopen()
    calls = list(rt.calls)
    result = h.delivery_cycle("b", "s", rt)
    assert result.status == "recovered_terminal" and rt.calls == calls
    rows = sql(h, "SELECT kind, detail FROM bridge_evidence WHERE kind IN ('session_call','session_telemetry') ORDER BY seq")
    assert [r[0] for r in rows] == ["session_call", "session_telemetry"]
    telemetry = json.loads(rows[1][1])
    assert telemetry["last_input_tokens"] is telemetry["context_window"] is telemetry["context_pct"] is None
    assert telemetry["source"] == "unknown"
    stored = json.loads(sql(h, "SELECT result_json FROM bridge_deliveries")[0][0])
    assert stored["session"] == result.session
    token = h.acquire_claim("b", "s", h.bridge.owner_id)
    h.finalize_terminal(token, {"response": "ok", "vendor_session": {"id": "native-ext-1"}}, result.delivery_id)
    assert sql(h, "SELECT COUNT(*) FROM bridge_evidence WHERE kind IN ('session_call','session_telemetry')") == [(2,)]


def test_stale_owner_cannot_mutate_registry_or_receipts(tmp_path):
    h = BridgeHarness(tmp_path / "ws")
    (record,) = bseed(h)
    token = h.acquire_claim("b", "s", "old", lease_sec=1)
    did = h.bridge.begin_attempt(token, record)
    h.bridge._resolve_session(token, NativeRuntimeTarget(), h.store.read_records("s", 0, 1)[0], did)
    before = history(h)
    h.clock.advance(2)
    h.acquire_claim("b", "s", "new")
    with pytest.raises(StaleClaimError):
        h.bridge.transition(token, did, "STARTED", "started")
    assert history(h) == before
    assert sql(h, "SELECT COUNT(*) FROM bridge_evidence WHERE kind='session_call'") == [(0,)]


def legacy_store(tmp_path):
    db = tmp_path / "legacy.db"
    store = CoreStore(db)
    for peer in ("a", "b"):
        store.register_peer(Peer(peer_id=peer))
    store.create_stream(Stream(stream_id="s", members=["a", "b"]))
    claims = ClaimStore(db, generation=lambda: "lineage", clock=lambda: 100.0)
    with claims.transaction() as conn:
        for stmt in _DDL:
            conn.execute(stmt)
        conn.execute("DROP TABLE IF EXISTS bridge_sessions")
        conn.execute("CREATE TABLE bridge_sessions (stream_id TEXT, peer_id TEXT, runtime_kind TEXT, external_session_id TEXT, "
                     "session_generation INTEGER, adapter_fingerprint TEXT, binding TEXT, resumable INTEGER, state TEXT, last_seen REAL, "
                     "workspace_generation TEXT, vendor_session_id TEXT, context_watermark INTEGER, bootstrap_truncated INTEGER, usage_json TEXT, "
                     "context_watermark_version INTEGER NOT NULL DEFAULT 1, PRIMARY KEY(stream_id,peer_id))")
        conn.execute("INSERT INTO bridge_sessions VALUES ('s','b','fake','local-4',4,'F1','model-a/profile-1',1,'ACTIVE',100,'lineage',NULL,NULL,NULL,NULL,1)")
        conn.execute("INSERT INTO bridge_session_events(stream_id,peer_id,event,from_state,to_state,generation,detail) VALUES ('s','b','created','NONE','ACTIVE',12,'{}')")
    return db, store, claims


def test_legacy_slot_migration_is_atomic_idempotent_and_preserves_unknown_counts(tmp_path):
    db, store, claims = legacy_store(tmp_path)
    bridge = Bridge(store, claims)
    with claims.transaction() as conn:
        before = [tuple(r) for r in conn.execute("SELECT * FROM bridge_session_history")]
        assert [r[1] for r in conn.execute("PRAGMA table_info(bridge_sessions)")] == ["stream_id", "peer_id", "session_handle", "session_generation", "updated_at"]
        assert conn.execute("SELECT next_generation FROM bridge_session_counters").fetchone()[0] == 13
        assert tuple(conn.execute("SELECT reuse_count,fresh_count,counts_complete FROM bridge_session_monitor_stats").fetchone()) == (None, None, 0)
        assert conn.execute("SELECT COUNT(*) FROM bridge_evidence WHERE kind='session_call'").fetchone()[0] == 0
    Bridge(store, claims)
    with claims.transaction() as conn:
        assert [tuple(r) for r in conn.execute("SELECT * FROM bridge_session_history")] == before
    assert bridge.current_mapping("b", "s")["external_session_id"] == "local-4"


def test_registry_migration_rolls_back_ddl_data_and_minted_handles(tmp_path):
    db, store, claims = legacy_store(tmp_path)
    with claims.transaction() as conn:
        conn.execute("INSERT INTO bridge_session_events(stream_id,peer_id,event,from_state,to_state,generation,detail) "
                     "VALUES ('overwritten','b','created','NONE','ACTIVE',19,'{}')")
    crash = CrashInjector()
    crash.arm("bridge.registry.before_commit")
    with pytest.raises(CrashInjected):
        Bridge(store, claims, fault_hook=crash)
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT external_session_id FROM bridge_sessions").fetchone() == ("local-4",)
        assert conn.execute("SELECT name FROM sqlite_master WHERE name='bridge_session_history'").fetchone() is None
        assert conn.execute("SELECT name FROM sqlite_master WHERE name IN ('bridge_session_counters','bridge_session_monitor_scope')").fetchall() == []
    Bridge(store, claims)
    with claims.transaction() as conn:
        assert conn.execute("SELECT next_generation FROM bridge_session_counters WHERE stream_id='overwritten'").fetchone()[0] == 20
        assert tuple(conn.execute("SELECT reuse_count,fresh_count,counts_complete FROM bridge_session_monitor_scope WHERE stream_id='overwritten'").fetchone()) == (None, None, 0)


def test_legacy_queued_prompt_is_consumed_without_execution_and_later_intent_runs(tmp_path):
    h = BridgeHarness(tmp_path / "ws")
    bseed(h, n=0)
    old = add(h, "old", kind="prompt")
    add(h, "new", kind="prompt", metadata={"session_selector": "latest"})
    rt = NativeRuntimeTarget()
    refused = h.delivery_cycle("b", "s", rt)
    assert refused.record_id == old.record_id and refused.status == "failed_not_started"
    assert refused.detail["error"]["code"] == "legacy_selector_unsupported" and rt.calls == []
    assert sql(h, "SELECT certainty,acked FROM bridge_deliveries") == [("NOT_STARTED", 1)]
    assert h.delivery_cycle("b", "s", rt).status == "delivered"


def test_replay_returns_original_snapshot_even_after_pointer_and_handle_are_gone(tmp_path, monkeypatch):
    db = tmp_path / "ask.db"
    first = ask(db, "cx", "first", request_id="one", runtime=NativeRuntimeTarget())
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE bridge_session_history SET state='TOMBSTONED',tombstoned_at=99")
        conn.execute("DELETE FROM bridge_sessions")
    monkeypatch.setattr(Bridge, "__init__", lambda *a, **kw: pytest.fail("terminal replay constructed a Bridge"))
    rt = NativeRuntimeTarget()
    replay = ask(db, "cx", "first", request_id="one", runtime=rt)
    assert replay["session"] == first["session"] and replay["response_record_id"] == first["response_record_id"]
    assert rt.calls == []
    assert "vendor_session" not in first and "vendor_session" not in replay
    with pytest.raises(IdempotencyConflictError):
        ask(db, "cx", "changed", request_id="one", runtime=rt)


def test_selectorless_historical_terminal_has_no_replay_shim_and_identity_conflicts_first(tmp_path):
    db, store, claims = legacy_store(tmp_path)
    rt = FakeRuntimeTarget()
    rec = store.append_record(stream_id="s", author_peer_id="a", kind="prompt", body="old", targets=["b"],
                              idempotency_key="ask:one", metadata={"runtime_kind": rt.runtime_kind, "binding": rt.binding()}, created_at="2026-10-01T00:00:00Z")
    bridge = Bridge(store, claims)
    token = claims.acquire("b", "s", bridge.owner_id, 30)
    did = bridge.begin_attempt(token, rec)
    bridge.transition(token, did, "STARTED", "started")
    bridge.finalize_terminal(token, {"response": "old answer"}, did)
    claims.release(token)
    with pytest.raises(IdempotencyConflictError):
        ask(db, "b", "changed", author="a", stream_id="s", request_id="one", runtime=rt)
    with pytest.raises(ValueError, match="selector metadata"):
        ask(db, "b", "old", author="a", stream_id="s", request_id="one", runtime=rt)
    assert rt.calls == []


def test_monitor_quota_index_is_installed_by_observation_writer(tmp_path):
    from peerhub.extensions.observation import ObservationStore
    db = tmp_path / "quota.db"
    CoreStore(db)
    ObservationStore(db)
    with sqlite3.connect(db) as conn:
        plan = conn.execute("EXPLAIN QUERY PLAN SELECT capture_seq FROM observations WHERE subject_ref=? AND kind=? AND resource_pool_ref=? ORDER BY effective_at_us DESC,capture_seq DESC LIMIT 1",
                            ("cx", "quota", "cx:7d")).fetchall()
    assert any("idx_obs_session_quota" in row[-1] for row in plan)


def test_started_receipt_and_counters_roll_back_with_certainty(tmp_path):
    h = BridgeHarness(tmp_path / "ws")
    (rec,) = bseed(h)
    token = h.acquire_claim("b", "s", h.bridge.owner_id)
    did = h.bridge.begin_attempt(token, rec)
    h.bridge._resolve_session(token, NativeRuntimeTarget(), h.store.read_records("s", 0, 1)[0], did)
    handle = h.current_mapping("b", "s")["session_handle"]
    with h.cs.transaction() as conn:
        conn.execute("CREATE TRIGGER fail_stats BEFORE UPDATE ON bridge_session_monitor_stats BEGIN SELECT RAISE(ABORT,'injected stats failure'); END")
    with pytest.raises(sqlite3.IntegrityError, match="injected stats failure"):
        h.bridge.transition(token, did, "STARTED", "started")
    assert sql(h, "SELECT certainty FROM bridge_deliveries") == [("NOT_STARTED",)]
    assert sql(h, "SELECT COUNT(*) FROM bridge_evidence WHERE kind='session_call'") == [(0,)]
    assert counts(h, handle) == (0, 0, 1)


def test_taint_uses_bound_delivery_instead_of_repointed_mru(tmp_path):
    h = BridgeHarness(tmp_path / "ws")
    bseed(h)
    rt = NativeRuntimeTarget()
    rt.runtime_kind = "cx"
    first = h.delivery_cycle("b", "s", rt)
    rt.set_binding("other")
    add(h, "second")
    def repoint():
        with h.cs.transaction() as conn:
            conn.execute("UPDATE bridge_sessions SET session_handle=?,session_generation=1", (first.session["handle"],))
    rt.script_deliver(("started", "exec"), ("call", repoint), ("runtime_error", "disconnect"))
    second = h.delivery_cycle("b", "s", rt)
    assert second.status == "uncertain" and second.session["generation"] == 2
    assert history(h)[0][2] == "ACTIVE" and history(h)[1][2] == "LOST"
    assert sql(h, "SELECT session_handle,session_generation FROM bridge_sessions") == [(first.session["handle"], 1)]
    calls = list(rt.calls)
    assert h.delivery_cycle("b", "s", rt).status == "blocked_uncertain" and rt.calls == calls


@pytest.mark.parametrize("loss", ["uncertain", "context_lost", "cancel"])
@pytest.mark.parametrize("replacement", [False, True])
def test_loss_repairs_or_deletes_pointer_before_any_resolution(tmp_path, loss, replacement):
    h = BridgeHarness(tmp_path / "ws")
    bseed(h)
    rt = NativeRuntimeTarget()
    first = h.delivery_cycle("b", "s", rt)
    expected = []
    if replacement:
        expected = [(first.session["handle"], 1)]
        h.clock.advance(1)
        rt.set_binding("other")
        add(h, "second")
        h.delivery_cycle("b", "s", rt)
    rec = add(h, "inflight")
    token = h.acquire_claim("b", "s", h.bridge.owner_id)
    did = h.bridge.begin_attempt(token, rec)
    h.bridge._resolve_session(token, rt, h.store.read_records("s", rec.position - 1, 1)[0], did)
    affected = h.current_mapping("b", "s")
    if loss == "uncertain":
        h.bridge.transition(token, did, "MAY_HAVE_STARTED", "start_uncertain")
    elif loss == "context_lost":
        h.bridge._mark_lost(token, "context_lost")
    else:
        h.bridge.transition(token, did, "STARTED", "started")
        control = ctl(h, "control.cancel", "cancel")
        h.handle_control(control.record_id, rt, "b")
    assert sql(h, "SELECT state,vendor_session_id FROM bridge_session_history WHERE session_handle=?",
               (affected["session_handle"],)) == [("LOST", None)]
    assert sql(h, "SELECT session_handle,session_generation FROM bridge_sessions") == expected
    assert sql(h, "SELECT next_generation FROM bridge_session_counters") == [(affected["session_generation"] + 1,)]


@pytest.mark.parametrize("loss", ["uncertain", "context_lost", "cancel"])
def test_pointer_repair_failure_rolls_back_loss_in_the_same_transaction(tmp_path, loss):
    h = BridgeHarness(tmp_path / "ws")
    bseed(h)
    rt = NativeRuntimeTarget()
    first = h.delivery_cycle("b", "s", rt)
    rec = add(h, "inflight")
    token = h.acquire_claim("b", "s", h.bridge.owner_id)
    did = h.bridge.begin_attempt(token, rec)
    h.bridge._resolve_session(token, rt, h.store.read_records("s", rec.position - 1, 1)[0], did)
    if loss != "uncertain":
        h.bridge.transition(token, did, "STARTED", "started")
    with h.cs.transaction() as conn:
        conn.execute("CREATE TRIGGER fail_pointer BEFORE DELETE ON bridge_sessions BEGIN SELECT RAISE(ABORT,'injected pointer failure'); END")
    with pytest.raises(sqlite3.IntegrityError, match="injected pointer failure"):
        if loss == "uncertain":
            h.bridge.transition(token, did, "MAY_HAVE_STARTED", "start_uncertain")
        elif loss == "context_lost":
            h.bridge._mark_lost(token, "context_lost")
        else:
            control = ctl(h, "control.cancel", "cancel")
            h.handle_control(control.record_id, rt, "b")
    assert sql(h, "SELECT state,vendor_session_id FROM bridge_session_history") == [("ACTIVE", "native-ext-1")]
    assert sql(h, "SELECT session_handle,session_generation FROM bridge_sessions") == [(first.session["handle"], 1)]
    assert sql(h, "SELECT certainty FROM bridge_deliveries WHERE delivery_id=?", (did,)) == [("NOT_STARTED" if loss == "uncertain" else "STARTED",)]
    assert sql(h, "SELECT event FROM bridge_session_events WHERE event='session_lost'") == []
    assert sql(h, "SELECT kind FROM bridge_evidence WHERE kind='session_tainted'") == []
    assert sql(h, "SELECT record_id FROM bridge_controls") == []


def test_resume_rejection_repairs_pointer_before_fresh_allocation(tmp_path):
    crash = CrashInjector()
    h = BridgeHarness(tmp_path / "ws", fault_hook=crash)
    bseed(h)
    rt = NativeRuntimeTarget()
    h.delivery_cycle("b", "s", rt)
    add(h, "second")
    rt.script_resume("missing")
    crash.arm("bridge.after_resume_failure")
    with pytest.raises(CrashInjected):
        h.delivery_cycle("b", "s", rt)
    assert sql(h, "SELECT state,vendor_session_id FROM bridge_session_history") == [("LOST", None)]
    assert sql(h, "SELECT session_handle,session_generation FROM bridge_sessions") == []
    assert sql(h, "SELECT next_generation FROM bridge_session_counters") == [(2,)]


def test_expiry_repairs_pointer_to_eligible_retained_history(tmp_path):
    h = BridgeHarness(tmp_path / "ws")
    bseed(h)
    rt = NativeRuntimeTarget()
    first = h.delivery_cycle("b", "s", rt)
    h.clock.advance(1)
    rt.set_binding("other")
    add(h, "second")
    second = h.delivery_cycle("b", "s", rt)
    with h.cs.transaction() as conn:
        conn.execute("UPDATE bridge_session_history SET last_seen=? WHERE session_handle=?",
                     (h.clock.now() - 30 * 86400 - 1, second.session["handle"]))
    rt.set_binding("model-a/profile-1")
    add(h, "third")
    resumed = h.delivery_cycle("b", "s", rt)
    assert resumed.session["handle"] == first.session["handle"]
    assert resumed.session["mode"] == "resumed"
    assert history(h)[1][2] == "TOMBSTONED"
    assert sql(h, "SELECT session_handle FROM bridge_sessions") == [(first.session["handle"],)]


def test_new_calls_preserve_unknown_legacy_totals_and_coverage(tmp_path):
    db, store, claims = legacy_store(tmp_path)
    bridge = Bridge(store, claims)
    rt = FakeRuntimeTarget()
    rt.sessions.add("local-4")
    store.append_record(stream_id="s", author_peer_id="a", kind="prompt", body="new", targets=["b"],
                        idempotency_key="new", metadata={"session_selector": "latest"}, created_at="2026-10-14T00:00:00Z")
    with claims.transaction() as conn:
        coverage = conn.execute("SELECT coverage_start_seq FROM bridge_session_monitor_scope").fetchone()[0]
    token = claims.acquire("b", "s", bridge.owner_id, 30)
    result = bridge.run_cycle(token, rt)
    assert result.status == "delivered" and result.session["mode"] == "resumed"
    with claims.transaction() as conn:
        for table in ("bridge_session_monitor_stats", "bridge_session_monitor_scope"):
            assert tuple(conn.execute(f"SELECT reuse_count,fresh_count,counts_complete FROM {table}").fetchone()) == (None, None, 0)
        assert conn.execute("SELECT coverage_start_seq FROM bridge_session_monitor_scope").fetchone()[0] == coverage
        assert conn.execute("SELECT COUNT(*) FROM bridge_evidence WHERE kind='session_call'").fetchone()[0] == 1


def test_terminal_telemetry_and_result_roll_back_together(tmp_path):
    h = BridgeHarness(tmp_path / "ws")
    (rec,) = bseed(h)
    token = h.acquire_claim("b", "s", h.bridge.owner_id)
    did = h.bridge.begin_attempt(token, rec)
    h.bridge._resolve_session(token, NativeRuntimeTarget(), h.store.read_records("s", 0, 1)[0], did)
    h.bridge.transition(token, did, "STARTED", "started")
    with h.cs.transaction() as conn:
        conn.execute("CREATE TRIGGER fail_telemetry BEFORE UPDATE OF latest_telemetry_seq ON bridge_session_monitor_stats BEGIN SELECT RAISE(ABORT,'injected telemetry failure'); END")
    result = {"response": "ok", "vendor_session": {"id": "native-ext-1"}}
    with pytest.raises(sqlite3.IntegrityError, match="injected telemetry failure"):
        h.bridge.finalize_terminal(token, result, did)
    assert sql(h, "SELECT certainty,result_json FROM bridge_deliveries") == [("STARTED", None)]
    assert sql(h, "SELECT COUNT(*) FROM bridge_evidence WHERE kind IN ('terminal','session_telemetry')") == [(0,)]
    assert sql(h, "SELECT vendor_session_id,context_watermark FROM bridge_session_history") == [(None, None)]
    assert sql(h, "SELECT COUNT(*) FROM records WHERE kind='response'") == [(0,)]
    with h.cs.transaction() as conn:
        conn.execute("DROP TRIGGER fail_telemetry")
    h.bridge.finalize_terminal(token, result, did)
    assert sql(h, "SELECT COUNT(*) FROM bridge_evidence WHERE kind='session_telemetry'") == [(1,)]
