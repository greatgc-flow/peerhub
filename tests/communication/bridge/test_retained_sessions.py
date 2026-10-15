"""PR2 retained identity, fenced writers and independent durable SQL oracles."""
import json
import re
import sqlite3

import pytest

from peerhub.core.models import Peer, Stream
from peerhub.core.store import CoreStore, IdempotencyConflictError
from peerhub.extensions.ask import ask
from peerhub.extensions.bridge import Bridge, _DDL
from peerhub.extensions import bridge_registry, session_receipts
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
    assert telemetry["schema_version"] == 3
    assert "model" not in telemetry
    assert telemetry["provider_kind"] == "unknown"
    assert telemetry["configured_binding"] == {"profile": None, "model": None, "effort": None}
    assert telemetry["usage"] == {
        "schema_version": 1, "scope": None, "source": None, "attribution": "unattributed",
        "reported": {}, "normalized": {}, "field_evidence": {},
        "coverage": {"state": "UNKNOWN", "reason": "not_captured"},
    }
    assert telemetry["steps"] == []
    assert telemetry["step_coverage"] == {"state": "UNKNOWN", "measured": 0, "dropped": 0}
    assert telemetry["last_input_tokens"] is telemetry["context_window"] is telemetry["context_pct"] is None
    assert telemetry["source"] == "unknown"
    stored = json.loads(sql(h, "SELECT result_json FROM bridge_deliveries")[0][0])
    assert stored["session"] == result.session
    token = h.acquire_claim("b", "s", h.bridge.owner_id)
    h.finalize_terminal(token, {"response": "ok", "vendor_session": {"id": "native-ext-1"}}, result.delivery_id)
    assert sql(h, "SELECT COUNT(*) FROM bridge_evidence WHERE kind IN ('session_call','session_telemetry')") == [(2,)]


def test_session_timing_uses_process_monotonic_clock(tmp_path, monkeypatch):
    h = BridgeHarness(tmp_path / "ws")
    bseed(h)
    ticks = iter([1_000_000, 1_012_345_678])
    monkeypatch.setattr(session_receipts, "PROCESS_CLOCK_DOMAIN", "process-a")
    monkeypatch.setattr(session_receipts, "monotonic_ns", lambda: next(ticks))
    result = h.delivery_cycle("b", "s", FakeRuntimeTarget())
    start = sql(h, "SELECT detail FROM bridge_evidence WHERE delivery_id=? AND kind='session_call'", (result.delivery_id,))[0][0]
    terminal = sql(h, "SELECT detail FROM bridge_evidence WHERE delivery_id=? AND kind='terminal'", (result.delivery_id,))[0][0]
    start_timing = json.loads(start)["peerhub_timing"]
    terminal_timing = json.loads(terminal)["peerhub_timing"]
    assert json.loads(start)["schema_version"] == 2
    assert start_timing == {"schema_version": 1, "clock_domain": "process-a", "started_monotonic_ns": 1_000_000}
    assert terminal_timing["elapsed_ns"] == 1_011_345_678
    assert terminal_timing["latency_reason"] is None
    assert sql(h, "SELECT latency_sum_ns,latency_count,missing_start_count,domain_mismatch_count,invalid_ticks_count,terminal_before_start_count FROM bridge_session_latency_daily") == [
        (1_011_345_678, 1, 0, 0, 0, 0)
    ]


@pytest.mark.parametrize(("start_tick", "end_tick", "change_domain", "reason"), [
    (100, 200, True, "domain_mismatch"),
    (200, 100, False, "terminal_before_start"),
    (-1, 100, False, "invalid_ticks"),
    (100, True, False, "invalid_ticks"),
])
def test_session_timing_refuses_invalid_clock_pairs(tmp_path, monkeypatch, start_tick, end_tick, change_domain, reason):
    h = BridgeHarness(tmp_path / "ws")
    bseed(h)
    ticks = iter([start_tick, end_tick])
    monkeypatch.setattr(session_receipts, "PROCESS_CLOCK_DOMAIN", "process-a")

    def next_tick():
        tick = next(ticks)
        if change_domain and tick == end_tick:
            monkeypatch.setattr(session_receipts, "PROCESS_CLOCK_DOMAIN", "process-b")
        return tick

    monkeypatch.setattr(session_receipts, "monotonic_ns", next_tick)
    result = h.delivery_cycle("b", "s", FakeRuntimeTarget())
    terminal = sql(h, "SELECT detail FROM bridge_evidence WHERE delivery_id=? AND kind='terminal'", (result.delivery_id,))[0][0]
    timing = json.loads(terminal)["peerhub_timing"]
    assert timing["elapsed_ns"] is None
    assert timing["latency_reason"] == reason
    counters = sql(h, "SELECT latency_sum_ns,latency_count,missing_start_count,domain_mismatch_count,invalid_ticks_count,terminal_before_start_count FROM bridge_session_latency_daily")[0]
    assert counters[0:2] == (0, 0)
    assert counters[{"missing_start": 2, "domain_mismatch": 3, "invalid_ticks": 4, "terminal_before_start": 5}[reason]] == 1


def test_terminal_without_session_call_records_missing_start_without_latency(tmp_path, monkeypatch):
    h = BridgeHarness(tmp_path / "ws")
    (record,) = bseed(h)
    monkeypatch.setattr(session_receipts, "monotonic_ns", lambda: 100)
    token = h.acquire_claim("b", "s", h.bridge.owner_id)
    delivery_id = h.bridge.begin_attempt(token, record)
    h.bridge.transition(token, delivery_id, "STARTED", "started")
    h.bridge.finalize_terminal(token, {"response": "ok"}, delivery_id)
    timing = json.loads(sql(h, "SELECT detail FROM bridge_evidence WHERE kind='terminal'", ())[0][0])["peerhub_timing"]
    assert timing["elapsed_ns"] is None and timing["latency_reason"] == "missing_start"
    assert sql(h, "SELECT latency_sum_ns,latency_count,missing_start_count FROM bridge_session_latency_daily") == [(0, 0, 1)]


def test_session_latency_projection_retains_only_thirty_utc_days(tmp_path, monkeypatch):
    h = BridgeHarness(tmp_path / "ws")
    bseed(h)
    ticks = iter([1, 2, 3, 5])
    monkeypatch.setattr(session_receipts, "monotonic_ns", lambda: next(ticks))
    h.delivery_cycle("b", "s", FakeRuntimeTarget())
    first_day = sql(h, "SELECT day_utc FROM bridge_session_latency_daily")[0][0]
    with h.cs.transaction() as conn:
        conn.execute("INSERT INTO bridge_session_latency_daily VALUES ('idle-stream','idle-peer','1969-01-01',7,1,0,0,0,0)")
    h.clock.advance(31 * 24 * 60 * 60)
    add(h, "next-day-window")
    h.delivery_cycle("b", "s", FakeRuntimeTarget())
    rows = sql(h, "SELECT day_utc,latency_count FROM bridge_session_latency_daily")
    assert len(rows) == 1 and rows[0][1] == 1
    assert first_day != rows[0][0]


def test_session_latency_projection_splits_utc_days(tmp_path, monkeypatch):
    h = BridgeHarness(tmp_path / "ws")
    bseed(h)
    ticks = iter([10, 20, 30, 50])
    monkeypatch.setattr(session_receipts, "monotonic_ns", lambda: next(ticks))
    h.delivery_cycle("b", "s", FakeRuntimeTarget())
    first_day = sql(h, "SELECT day_utc FROM bridge_session_latency_daily")[0][0]
    h.clock.advance(24 * 60 * 60)
    add(h, "tomorrow")
    h.delivery_cycle("b", "s", FakeRuntimeTarget())
    rows = sql(h, "SELECT day_utc,latency_sum_ns,latency_count FROM bridge_session_latency_daily ORDER BY day_utc")
    assert len(rows) == 2
    assert rows[0] == (first_day, 10, 1)
    assert rows[1][1:] == (20, 1)


def test_session_latency_projection_accumulates_samples_within_scope_day(tmp_path, monkeypatch):
    h = BridgeHarness(tmp_path / "ws")
    bseed(h)
    ticks = iter([10, 20, 30, 50])
    monkeypatch.setattr(session_receipts, "monotonic_ns", lambda: next(ticks))
    h.delivery_cycle("b", "s", FakeRuntimeTarget())
    add(h, "same-day-second")
    h.delivery_cycle("b", "s", FakeRuntimeTarget())
    assert sql(h, "SELECT latency_sum_ns,latency_count FROM bridge_session_latency_daily") == [(30, 2)]


def test_registry_reopen_repairs_latest_telemetry_pointer_to_terminal_receipt(tmp_path):
    h = BridgeHarness(tmp_path / "ws")
    bseed(h)
    h.delivery_cycle("b", "s", NativeRuntimeTarget())
    add(h, "second-receipt")
    second = h.delivery_cycle("b", "s", NativeRuntimeTarget())
    handle = second.session["handle"]
    expected = sql(h, "SELECT seq FROM bridge_evidence WHERE delivery_id=? AND kind='session_telemetry'", (second.delivery_id,))[0][0]
    pending = add(h, "uncommitted-third")
    token = h.acquire_claim("b", "s", h.bridge.owner_id)
    pending_delivery = h.bridge.begin_attempt(token, pending)
    runtime = NativeRuntimeTarget()
    h.bridge._resolve_session(token, runtime, pending, pending_delivery)
    h.bridge.transition(token, pending_delivery, "STARTED", "started")
    with h.cs.transaction() as conn:
        conn.execute("INSERT INTO bridge_evidence(delivery_id,stream_id,peer_id,kind,certainty,detail) "
                     "VALUES (?, 's','b','session_telemetry','STARTED','{}')", (pending_delivery,))
        bad = conn.execute("SELECT MAX(seq) FROM bridge_evidence WHERE delivery_id=?", (pending_delivery,)).fetchone()[0]
        conn.execute("UPDATE bridge_session_monitor_stats SET latest_telemetry_seq=? WHERE session_handle=?", (bad, handle))
        conn.execute("UPDATE bridge_session_monitor_meta SET version=0 WHERE name='latest_terminal_telemetry_v1'")

    h.reopen()

    assert sql(h, "SELECT latest_telemetry_seq FROM bridge_session_monitor_stats WHERE session_handle=?", (handle,)) == [(expected,)]


@pytest.mark.parametrize(("second_time", "expect_second"), [(999.0, False), (1000.0, True)])
def test_latest_telemetry_pointer_advances_by_observed_time_then_sequence(tmp_path, second_time, expect_second):
    h = BridgeHarness(tmp_path / "ws")
    bseed(h)
    runtime = NativeRuntimeTarget()
    first = h.delivery_cycle("b", "s", runtime)
    first_seq = sql(h, "SELECT seq FROM bridge_evidence WHERE delivery_id=? AND kind='session_telemetry'", (first.delivery_id,))[0][0]
    add(h, "pointer-order")
    with h.cs.transaction() as conn:
        conn.execute("UPDATE bridge_session_history SET last_seen=? WHERE session_handle=?", (second_time, first.session["handle"]))
    h.clock.set(second_time)
    second = h.delivery_cycle("b", "s", runtime)
    second_seq = sql(h, "SELECT seq FROM bridge_evidence WHERE delivery_id=? AND kind='session_telemetry'", (second.delivery_id,))[0][0]
    assert second.status == "delivered" and second_seq > first_seq
    assert second.session["handle"] == first.session["handle"]
    assert sql(h, "SELECT json_extract(detail,'$.observed_at') FROM bridge_evidence WHERE seq=?", (second_seq,)) == [(second_time,)]
    expected = second_seq if expect_second else first_seq
    assert sql(h, "SELECT latest_telemetry_seq FROM bridge_session_monitor_stats WHERE session_handle=?", (first.session["handle"],)) == [(expected,)]


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
        prior_user_version = conn.execute("PRAGMA user_version").fetchone()[0]
        prior_evidence = conn.execute("SELECT COUNT(*) FROM bridge_evidence").fetchone()[0]
        conn.execute("INSERT INTO bridge_evidence(stream_id,peer_id,kind,certainty,detail) "
                     "VALUES ('overwritten','b','migration-sentinel','STARTED','{}')")
        prior_evidence += 1
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
        assert conn.execute("PRAGMA user_version").fetchone()[0] == prior_user_version
        assert conn.execute("SELECT COUNT(*) FROM bridge_evidence").fetchone()[0] == prior_evidence
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


def test_v2_telemetry_does_not_promote_unvalidated_result_usage(tmp_path):
    h = BridgeHarness(tmp_path / "ws")
    (rec,) = bseed(h)
    token = h.acquire_claim("b", "s", h.bridge.owner_id)
    did = h.bridge.begin_attempt(token, rec)
    h.bridge._resolve_session(token, NativeRuntimeTarget(), h.store.read_records("s", 0, 1)[0], did)
    h.bridge.transition(token, did, "STARTED", "started")
    h.bridge.finalize_terminal(token, {
        "response": "ok", "usage": {"input_tokens": 41, "private_sentinel": "must-not-enter-ledger"},
    }, did)
    raw = sql(h, "SELECT detail FROM bridge_evidence WHERE kind='session_telemetry'")[0][0]
    detail = json.loads(raw)
    assert detail["usage"]["coverage"] == {"state": "UNKNOWN", "reason": "not_captured"}
    assert "private_sentinel" not in raw and "must-not-enter-ledger" not in raw


def test_terminal_rolls_back_when_telemetry_projection_row_is_missing(tmp_path):
    h = BridgeHarness(tmp_path / "ws")
    (rec,) = bseed(h)
    token = h.acquire_claim("b", "s", h.bridge.owner_id)
    did = h.bridge.begin_attempt(token, rec)
    h.bridge._resolve_session(token, NativeRuntimeTarget(), h.store.read_records("s", 0, 1)[0], did)
    h.bridge.transition(token, did, "STARTED", "started")
    handle = sql(h, "SELECT session_handle FROM bridge_session_history")[0][0]
    with h.cs.transaction() as conn:
        conn.execute("DELETE FROM bridge_session_monitor_stats WHERE session_handle=?", (handle,))

    with pytest.raises(sqlite3.IntegrityError, match="session telemetry projection missing"):
        h.bridge.finalize_terminal(token, {"response": "ok", "vendor_session": {"id": "native-ext-1"}}, did)

    assert sql(h, "SELECT certainty,result_json FROM bridge_deliveries") == [("STARTED", None)]
    assert sql(h, "SELECT COUNT(*) FROM bridge_evidence WHERE kind IN ('terminal','session_telemetry')") == [(0,)]
    assert sql(h, "SELECT COUNT(*) FROM bridge_session_latency_daily") == [(0,)]
    assert sql(h, "SELECT vendor_session_id,context_watermark FROM bridge_session_history") == [(None, None)]
    assert sql(h, "SELECT COUNT(*) FROM records WHERE kind='response'") == [(0,)]


def test_registry_reopen_backfills_missing_session_stats_projection(tmp_path):
    h = BridgeHarness(tmp_path / "ws")
    bseed(h)
    assert h.delivery_cycle("b", "s", FakeRuntimeTarget()).status == "delivered"
    handle = sql(h, "SELECT session_handle FROM bridge_session_history")[0][0]
    with h.cs.transaction() as conn:
        conn.execute("DELETE FROM bridge_session_monitor_stats WHERE session_handle=?", (handle,))

    Bridge(h.store, h.cs)

    # A fake runtime without exact external-session attribution cannot prove
    # old counts; the row is repaired but remains explicitly incomplete.
    assert sql(h, "SELECT reuse_count,fresh_count,counts_complete FROM bridge_session_monitor_stats WHERE session_handle=?",
               (handle,)) == [(None, None, 0)]


def test_registry_reopen_with_complete_projections_skips_historical_recount(tmp_path, monkeypatch):
    h = BridgeHarness(tmp_path / "ws")
    bseed(h)
    assert h.delivery_cycle("b", "s", FakeRuntimeTarget()).status == "delivered"

    monkeypatch.setattr(bridge_registry.session_receipts, "historical_counts",
                        lambda *args, **kwargs: pytest.fail("ordinary open must not recount history"))
    monkeypatch.setattr(bridge_registry.session_receipts, "historical_counts_by_generation",
                        lambda *args, **kwargs: pytest.fail("ordinary open must not scan historical scopes"))
    Bridge(h.store, h.cs)


def test_registry_rebuild_scales_with_scope_not_session_delivery_product(tmp_path, monkeypatch):
    from peerhub.extensions import session_receipts

    h = BridgeHarness(tmp_path / "ws")
    session_count, deliveries_per_session = 5_000, 10
    with h.cs.transaction() as conn:
        sessions = [
            (f"rebuild-{i}", "rebuild", "cc", "cc", f"external-{i}", i + 1, "adapter:cc:v1", "{}", 1,
             "LOST", 100.0, "lineage", f"vendor-{i}", 0, 0, None, 1, 100.0, None)
            for i in range(session_count)
        ]
        conn.executemany(
            "INSERT INTO bridge_session_history(session_handle,stream_id,peer_id,runtime_kind,external_session_id,"
            "session_generation,adapter_fingerprint,binding,resumable,state,last_seen,workspace_generation,"
            "vendor_session_id,context_watermark,bootstrap_truncated,usage_json,context_watermark_version,created_at,tombstoned_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", sessions)
        deliveries = [
            (f"delivery-{generation}-{item}", generation * deliveries_per_session + item + 1, "rebuild", "cc",
             f"record-{generation}-{item}", generation * deliveries_per_session + item + 1, 1, "NOT_STARTED", 1,
             f"external-{generation}", generation + 1, None, None, None, None, None, 0, None, None, None)
            for generation in range(session_count)
            for item in range(deliveries_per_session)
        ]
        conn.executemany(
            "INSERT INTO bridge_deliveries(delivery_id,seq,stream_id,peer_id,record_id,record_position,attempt,certainty,"
            "claim_generation,external_session_id,session_generation,execution_id,result_json,result_digest,terminal_at,"
            "response_record_id,acked,reconcile_record_id,session_handle,session_snapshot_json) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", deliveries)
        conn.execute("UPDATE bridge_deliveries SET certainty='STARTED' WHERE stream_id='rebuild'")
        conn.execute("INSERT INTO bridge_session_monitor_scope VALUES ('rebuild','cc',NULL,NULL,0,0)")

    count_calls = 0

    def count_legacy_reads(conn, stream, peer, generation=None):
        nonlocal count_calls
        count_calls += 1
        return None, None, 0

    def count_scope_reads(conn, stream, peer):
        nonlocal count_calls
        count_calls += 1
        return {}, (None, None, 0)

    monkeypatch.setattr(session_receipts, "historical_counts_by_generation", count_scope_reads)
    Bridge(h.store, h.cs)
    assert count_calls <= 2, "5k per-session projections must be backfilled with bounded scope queries"
    plan = sql(h, "EXPLAIN QUERY PLAN SELECT delivery_id FROM bridge_deliveries "
                  "WHERE stream_id='rebuild' AND peer_id='cc' AND session_generation=1")
    assert any("SEARCH bridge_deliveries USING INDEX idx_bd_scope_generation" in str(row) for row in plan)


def test_historical_scope_with_only_prestart_deliveries_is_known_zero():
    import sqlite3
    from peerhub.extensions.session_receipts import historical_counts_by_generation

    conn = sqlite3.connect(":memory:")
    conn.executescript("""
        CREATE TABLE bridge_deliveries(delivery_id TEXT, stream_id TEXT, peer_id TEXT,
            session_generation INTEGER, external_session_id TEXT, certainty TEXT);
        CREATE TABLE bridge_evidence(delivery_id TEXT, kind TEXT, detail TEXT);
        INSERT INTO bridge_deliveries VALUES ('d1','s','cc',7,'external','NOT_STARTED');
    """)

    generations, scope = historical_counts_by_generation(conn, "s", "cc")

    assert generations == {7: (0, 0, 1)}
    assert scope == (0, 0, 1)


class _ProviderTelemetryRuntime(NativeRuntimeTarget):
    runtime_kind = "cc"

    def __init__(self, kind="cc"):
        super().__init__(fingerprint=f"adapter:{kind}:v2")
        self.runtime_kind = kind


def _start_telemetry_delivery(h, runtime):
    (prompt,) = bseed(h)
    token = h.acquire_claim("b", "s", h.bridge.owner_id)
    did = h.bridge.begin_attempt(token, prompt)
    h.bridge._resolve_session(token, runtime, h.store.read_records("s", 0, 1)[0], did)
    h.bridge.transition(token, did, "STARTED", "started")
    return token, did


def test_provider_telemetry_is_receipt_only_and_bound_to_fenced_delivery(tmp_path):
    h = BridgeHarness(tmp_path / "ws")
    token, did = _start_telemetry_delivery(h, _ProviderTelemetryRuntime())
    candidate = {
        "schema_version": 1, "scope": "session_cumulative", "source": "cc.result.usage",
        "reported": {"input_tokens": 9, "cache_read_input_tokens": 2,
                     "cache_creation_input_tokens": 1, "output_tokens": 3},
        "normalized": {"provider_input_tokens": 12, "uncached_input_tokens": 9,
                       "cache_read_input_tokens": 2, "cache_creation_input_tokens": 1, "output_tokens": 3},
        "coverage": {"state": "MEASURED", "reason": None},
    }

    h.finalize_terminal(token, {"response": "ok", "provider_telemetry": candidate}, did)

    result_json, = sql(h, "SELECT result_json FROM bridge_deliveries WHERE delivery_id=?", (did,))[0]
    response_metadata, = sql(h, "SELECT metadata_json FROM records WHERE kind='response'")[0]
    telemetry_json, = sql(h, "SELECT detail FROM bridge_evidence WHERE kind='session_telemetry' AND delivery_id=?", (did,))[0]
    telemetry = json.loads(telemetry_json)
    assert "provider_telemetry" not in result_json
    assert "provider_telemetry" not in response_metadata
    assert telemetry["usage"]["scope"] == "session_cumulative"
    assert telemetry["usage"]["attribution"] == "unattributed"
    assert telemetry["usage"]["normalized"] == {"provider_input_tokens": 12, "uncached_input_tokens": 9,
                                                  "cache_read_input_tokens": 2, "cache_creation_input_tokens": 1,
                                                  "output_tokens": 3}
    assert sql(h, "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name LIKE '%usage%fact%'") == [(0,)]
    replacement = {**candidate, "normalized": {"input_tokens": 900, "output_tokens": 900}}
    assert h.finalize_terminal(token, {"response": "ok", "provider_telemetry": replacement}, did).outcome == "duplicate"
    reread, = sql(h, "SELECT detail FROM bridge_evidence WHERE kind='session_telemetry' AND delivery_id=?", (did,))[0]
    assert json.loads(reread)["usage"]["normalized"] == {
        "provider_input_tokens": 12, "uncached_input_tokens": 9, "cache_read_input_tokens": 2,
        "cache_creation_input_tokens": 1, "output_tokens": 3}


def test_provider_telemetry_scope_and_spoofed_delivery_id_fail_closed(tmp_path):
    h = BridgeHarness(tmp_path / "ws")
    token, did = _start_telemetry_delivery(h, _ProviderTelemetryRuntime())
    candidate = {
        "schema_version": 1, "scope": "session_cumulative", "source": "cc.result.usage", "delivery_id": "spoofed-delivery",
        "reported": {"input_tokens": 12, "output_tokens": 3},
        "normalized": {"provider_input_tokens": 12, "uncached_input_tokens": 12, "output_tokens": 3},
        "coverage": {"state": "MEASURED", "reason": None},
    }

    h.finalize_terminal(token, {"response": "still delivered", "provider_telemetry": candidate}, did)

    telemetry_json, = sql(h, "SELECT detail FROM bridge_evidence WHERE kind='session_telemetry' AND delivery_id=?", (did,))[0]
    usage = json.loads(telemetry_json)["usage"]
    assert usage["scope"] is None
    assert usage["attribution"] == "unattributed"
    assert usage["normalized"] == {}
    assert usage["coverage"]["state"] == "UNKNOWN"
    assert "spoofed-delivery" not in telemetry_json
    assert sql(h, "SELECT certainty FROM bridge_deliveries WHERE delivery_id=?", (did,)) == [("TERMINAL",)]


def test_provider_usage_persistence_rejects_fabricated_normalization(tmp_path):
    h = BridgeHarness(tmp_path / "ws")
    token, did = _start_telemetry_delivery(h, _ProviderTelemetryRuntime())
    candidate = {
        "schema_version": 1, "scope": "session_cumulative", "source": "cc.result.usage",
        "reported": {"output_tokens": 3}, "normalized": {"output_tokens": 3, "input_tokens": 999999},
        "coverage": {"state": "MEASURED", "reason": None},
    }
    h.finalize_terminal(token, {"response": "ok", "provider_telemetry": candidate}, did)
    detail, = sql(h, "SELECT detail FROM bridge_evidence WHERE kind='session_telemetry' AND delivery_id=?", (did,))[0]
    usage = json.loads(detail)["usage"]
    assert usage["reported"] == usage["normalized"] == {}
    assert usage["coverage"] == {"state": "UNKNOWN", "reason": "unsupported_usage_field"}


def test_provider_usage_persistence_keeps_valid_partial_native_counts(tmp_path):
    h = BridgeHarness(tmp_path / "ws")
    token, did = _start_telemetry_delivery(h, _ProviderTelemetryRuntime())
    candidate = {
        "schema_version": 1, "scope": "session_cumulative", "source": "cc.result.usage",
        "reported": {"input_tokens": 7}, "normalized": {"uncached_input_tokens": 7},
        "coverage": {"state": "MEASURED", "reason": None},
    }
    h.finalize_terminal(token, {"response": "ok", "provider_telemetry": candidate}, did)
    detail, = sql(h, "SELECT detail FROM bridge_evidence WHERE kind='session_telemetry' AND delivery_id=?", (did,))[0]
    usage = json.loads(detail)["usage"]
    assert usage["reported"] == {"input_tokens": 7}
    assert usage["normalized"] == {"uncached_input_tokens": 7}
    assert usage["coverage"]["state"] == "MEASURED"


def test_session_cumulative_usage_is_generation_observation_not_delivery_usage(tmp_path):
    h = BridgeHarness(tmp_path / "ws")
    token, did = _start_telemetry_delivery(h, _ProviderTelemetryRuntime("cx"))
    candidate = {
        "schema_version": 1, "scope": "session_cumulative", "source": "cx.turn.completed.usage",
        "reported": {"input_tokens": 40, "output_tokens": 4},
        "normalized": {"provider_input_tokens": 40, "output_tokens": 4},
        "coverage": {"state": "MEASURED", "reason": None},
    }

    h.finalize_terminal(token, {"response": "ok", "provider_telemetry": candidate}, did)

    telemetry_json, = sql(h, "SELECT detail FROM bridge_evidence WHERE kind='session_telemetry' AND delivery_id=?", (did,))[0]
    telemetry = json.loads(telemetry_json)
    assert telemetry["usage"]["scope"] == "session_cumulative"
    assert telemetry["usage"]["attribution"] == "unattributed"
    assert telemetry["usage"]["normalized"] == {"provider_input_tokens": 40, "output_tokens": 4}


def test_session_telemetry_never_promotes_configured_model_to_resolved_model(tmp_path):
    class BoundRuntime(_ProviderTelemetryRuntime):
        def session_binding(self):
            return {"profile": "cc.effort", "model": "claude-sonnet-5-5", "effort": "high", "writable": False}

    h = BridgeHarness(tmp_path / "ws")
    token, did = _start_telemetry_delivery(h, BoundRuntime())
    h.finalize_terminal(token, {"response": "ok"}, did)

    detail, = sql(h, "SELECT detail FROM bridge_evidence WHERE kind='session_telemetry' AND delivery_id=?", (did,))[0]
    telemetry = json.loads(detail)
    assert telemetry["configured_model"] == "claude-sonnet-5-5"
    assert telemetry["resolved_model"] is None
    assert telemetry["schema_version"] == 3
    assert "model" not in telemetry
