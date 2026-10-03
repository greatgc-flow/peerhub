"""Wave 3 gate fixes (cx.pro, D-W3-1..7): unsafe replay marker, scope/callback binding, forged reconcile, false ack,
trigger bypass, control records, binding compatibility. Every test names the catalog id it hardens."""
import multiprocessing as mp
import sqlite3

import pytest

from peerhub.extensions.bridge import (IllegalCertaintyTransition, InvalidTerminalResultError, ReconcileRejectedError,
                                       TerminalConflictError)
from peerhub.extensions.bridge_claims import ClaimScopeError
from peerhub.m1.store import CoreStore
from tests.m1.bridge_helpers import (bseed, delivery_rows, evidence, kinds, more_stream, offset_row, records, responses, sql)
from tests.m1.fakes import CrashInjector, FakeRuntimeTarget
from tests.m1.harness.bridge import BridgeHarness
from tests.m1.harness.bridge_workers import crash_cycle_worker
from tests.m1.helpers import req

pytestmark = [pytest.mark.integration, pytest.mark.bridge]
MAY, ST, TERM, NS = "MAY_HAVE_STARTED", "STARTED", "TERMINAL", "NOT_STARTED"


def _crash_child(ws, point, script, clock):
    p = mp.get_context("spawn").Process(target=crash_cycle_worker, args=(str(ws), "b", "s", point, script, clock), daemon=True)
    p.start()
    p.join(90)
    alive = p.is_alive()
    if alive:
        p.terminate()
    assert not alive
    return p.exitcode


# ---------------------------------------------------------------- 1. TD-11 invoke marker
@pytest.mark.restart
@pytest.mark.m1_id("BRG-012")
def test_brg_012_crash_after_invoke_before_start_evidence_never_replays(tmp_path):
    h = BridgeHarness(tmp_path / "ws")
    (rec,) = bseed(h)
    assert _crash_child(tmp_path / "ws", "bridge.before_runtime_invoke", [["started", "e1"], ["terminal", {"response": "r"}]],
                        h.clock.now()) == 17  # real process death at the exact point
    h2 = BridgeHarness(tmp_path / "ws", h.clock)
    (d,) = delivery_rows(h2, rec.record_id)
    assert d[7] == MAY and "about_to_invoke" in kinds(h2, d[0])  # durable marker already says MAY_HAVE_STARTED
    rt = FakeRuntimeTarget()
    for _ in range(2):
        res = h2.delivery_cycle("b", "s", rt)
        assert res.status == "blocked_uncertain" and res.certainty == MAY
    assert rt.calls == [] and responses(h2) == [] and offset_row(h2) == (0, 1)  # never blindly re-executed


@pytest.mark.restart
@pytest.mark.m1_id("BRG-012")
def test_brg_012_marker_is_durable_before_runtime_acts_and_pre_marker_crash_is_retryable(tmp_path):
    h = BridgeHarness(tmp_path / "ws")
    (rec,) = bseed(h)
    seen = {}
    rt = FakeRuntimeTarget()
    rt.script_deliver(("call", lambda: seen.update(certainty=delivery_rows(h, rec.record_id)[0][7])), ("started", "e1"),
                      ("terminal", {"response": "ok"}))
    assert h.delivery_cycle("b", "s", rt).status == "delivered"
    assert seen["certainty"] == MAY  # observed from inside the runtime invocation
    # positive control: a crash BEFORE the marker (claim acquired) leaves NOT_STARTED and recovery executes normally
    h2 = BridgeHarness(tmp_path / "ws2")
    bseed(h2)
    assert _crash_child(tmp_path / "ws2", "claim.after_acquire", [["started", "e1"]], h2.clock.now()) == 17
    rt2 = FakeRuntimeTarget()
    assert BridgeHarness(tmp_path / "ws2", h2.clock).delivery_cycle("b", "s", rt2).status == "delivered" and rt2.count("deliver") == 1
    # prespawn failure after the marker still ends NOT_STARTED and retryable (BRG-011 contract)
    h3 = BridgeHarness(tmp_path / "ws3")
    (r3,) = bseed(h3)
    rt3 = FakeRuntimeTarget()
    rt3.script_deliver(("prespawn_error", "no binary"))
    assert h3.delivery_cycle("b", "s", rt3).certainty == NS and delivery_rows(h3, r3.record_id)[0][7] == NS
    assert h3.delivery_cycle("b", "s", rt3).status == "delivered"


@pytest.mark.m1_id("BRG-012")
def test_brg_012_takeover_between_marker_and_invocation_never_replays(tmp_path):
    state = {}

    def hook(p):
        if p == "bridge.before_runtime_invoke" and not state:
            h.clock.advance(31)
            state["b"] = h.acquire_claim("b", "s", "B", 30)
            raise_crash()

    from tests.m1.fakes import CrashInjected

    def raise_crash():
        raise CrashInjected("died after marker")

    h = BridgeHarness(tmp_path / "ws", fault_hook=hook)
    (rec,) = bseed(h)
    with pytest.raises(CrashInjected):
        h.delivery_cycle("b", "s", FakeRuntimeTarget())
    rt = FakeRuntimeTarget()
    res = h.new_bridge("B").delivery_cycle("b", "s", rt)  # the new owner recovers
    assert res.status == "blocked_uncertain" and rt.calls == []


# ---------------------------------------------------------------- 2. scope / callback binding / result shape
def _two_scopes(h):
    bseed(h)
    more_stream(h, "s2")
    rt = FakeRuntimeTarget()
    rt.script_deliver(("started", "e1"), ("output", "p"))
    assert h.delivery_cycle("b", "s", rt).status == "uncertain"
    tok_s = h.acquire_claim("b", "s", "bridge-1")
    tok_s2 = h.acquire_claim("b", "s2", "other", 1000)  # valid token, FOREIGN scope
    return tok_s, tok_s2, delivery_rows(h)[0][0]


@pytest.mark.m1_id("CERT-001")
def test_cert_001_foreign_scope_token_cannot_mutate_another_delivery(bridge_h):
    tok_s, tok_s2, did = _two_scopes(bridge_h)
    before = delivery_rows(bridge_h), evidence(bridge_h), records(bridge_h), offset_row(bridge_h)
    with pytest.raises(ClaimScopeError):
        bridge_h.bridge.transition(tok_s2, did, TERM)
    with pytest.raises(ClaimScopeError):
        bridge_h.bridge.transition(tok_s2, did, MAY)
    with pytest.raises(ClaimScopeError):
        bridge_h.finalize_terminal(tok_s2, {"response": "evil"}, delivery_id=did)
    assert (delivery_rows(bridge_h), evidence(bridge_h), records(bridge_h), offset_row(bridge_h)) == before
    assert bridge_h.finalize_terminal(tok_s, {"response": "good"}, delivery_id=did).outcome == "first"  # control: right scope


@pytest.mark.m1_id("BRG-014")
def test_brg_014_delayed_callback_is_bound_to_its_own_delivery(bridge_h):
    bseed(bridge_h, n=2)
    rt = FakeRuntimeTarget()
    rt.script_deliver(("started", "e1"), ("terminal", {"response": "R-1"}))
    d1 = bridge_h.delivery_cycle("b", "s", rt).delivery_id
    rt.script_deliver(("started", "e2"), ("output", "partial"))
    d2 = bridge_h.delivery_cycle("b", "s", rt).delivery_id
    assert d1 != d2 and delivery_rows(bridge_h)[1][7] == ST
    tok = bridge_h.acquire_claim("b", "s", "bridge-1")
    before = delivery_rows(bridge_h), responses(bridge_h), offset_row(bridge_h)
    with pytest.raises(TerminalConflictError):  # a delayed, different callback for delivery 1
        bridge_h.finalize_terminal(tok, {"response": "late-for-1"}, delivery_id=d1)
    assert (delivery_rows(bridge_h), responses(bridge_h), offset_row(bridge_h)) == before  # delivery 2 NOT finalized
    assert bridge_h.finalize_terminal(tok, {"response": "R-1"}, delivery_id=d1).outcome == "duplicate"  # control: same truth
    assert bridge_h.finalize_terminal(tok, {"response": "R-2"}, delivery_id=d2).outcome == "first"


@pytest.mark.m1_id("BRG-014")
@pytest.mark.parametrize("bad", ["scalar", 7, None, ["response"], {"no_response": 1}, {"response": float("nan")}, {"response": object()}])
def test_brg_014_invalid_terminal_result_is_rejected_before_any_commit(bridge_h, bad):
    bseed(bridge_h)
    rt = FakeRuntimeTarget()
    rt.script_deliver(("started", "e1"), ("output", "p"))
    d = bridge_h.delivery_cycle("b", "s", rt).delivery_id
    tok = bridge_h.acquire_claim("b", "s", "bridge-1")
    before = delivery_rows(bridge_h), evidence(bridge_h), records(bridge_h), offset_row(bridge_h)
    with pytest.raises(InvalidTerminalResultError):
        bridge_h.finalize_terminal(tok, bad, delivery_id=d)
    assert (delivery_rows(bridge_h), evidence(bridge_h), records(bridge_h), offset_row(bridge_h)) == before
    assert delivery_rows(bridge_h)[0][7] == ST
    assert bridge_h.finalize_terminal(tok, {"response": None}, delivery_id=d).outcome == "first"  # control: valid shape commits


@pytest.mark.m1_id("CERT-001")
def test_cert_001_late_terminal_on_may_have_started_commits_evidence_backed_forward_edge(bridge_h):
    bseed(bridge_h)
    rt = FakeRuntimeTarget()
    rt.script_deliver(("timeout",))
    res = bridge_h.delivery_cycle("b", "s", rt)
    assert res.certainty == MAY
    tok = bridge_h.acquire_claim("b", "s", "bridge-1")
    assert bridge_h.finalize_terminal(tok, {"response": "late"}, delivery_id=res.delivery_id).outcome == "first"
    assert delivery_rows(bridge_h)[0][7] == TERM and len(responses(bridge_h)) == 1 and offset_row(bridge_h)[0] == 1
    with pytest.raises(IllegalCertaintyTransition):  # but a TERMINAL truth never moves again
        bridge_h.bridge.transition(tok, res.delivery_id, ST)


# ---------------------------------------------------------------- 3. forged reconcile
@pytest.mark.m1_id("CERT-002")
def test_cert_002_reconcile_is_authenticated_against_the_persisted_record(bridge_h):
    (rec,) = bseed(bridge_h)
    rt = FakeRuntimeTarget()
    rt.script_deliver(("timeout",))
    d = bridge_h.delivery_cycle("b", "s", rt).delivery_id
    msg = bridge_h.append_record(req(body={"decision": "RETRY", "delivery_id": d}, key="plain", author="a"))
    accept = bridge_h.append_record({**req(body={"decision": "ACCEPT", "delivery_id": d}, key="acc", author="a"), "kind": "control.reconcile"})
    by_bridge = bridge_h.append_record({**req(body={"decision": "RETRY", "delivery_id": d}, key="self", author="b"), "kind": "control.reconcile"})
    state = lambda: (sql(bridge_h, "SELECT * FROM bridge_reconciliations"), delivery_rows(bridge_h), evidence(bridge_h))
    before = state()
    forged = [
        msg.model_copy(update={"kind": "control.reconcile"}),  # edited copy of an ordinary message
        accept.model_copy(update={"body": {"decision": "RETRY", "delivery_id": d}}),  # edited body of a persisted ACCEPT
        msg.model_copy(update={"kind": "control.reconcile", "record_id": "rec-never-persisted"}),  # not durable at all
        by_bridge,  # persisted and well-formed, but authored by the bridged peer itself
    ]
    for f in forged:
        with pytest.raises(ReconcileRejectedError):
            bridge_h.reconcile_uncertain(rec.record_id, f)
    assert state() == before
    good = bridge_h.append_record({**req(body={"decision": "RETRY", "delivery_id": d}, key="good", author="a"), "kind": "control.reconcile"})
    assert bridge_h.reconcile_uncertain(rec.record_id, good)["reconcile_record_id"] == good.record_id  # positive control


# ---------------------------------------------------------------- 4. false ack
@pytest.mark.concurrency
@pytest.mark.m1_id("BRG-006")
@pytest.mark.parametrize("conflicts,status", [(8, "ack_failed"), (3, "delivered")])
def test_brg_006_exhausted_offset_cas_conflicts_never_report_acked(tmp_path, conflicts, status):
    st = {"armed": False, "left": conflicts, "hits": 0}

    def bridge_hook(p):
        if p == "bridge.after_terminal_evidence_before_offset_ack":
            st["armed"] = True

    def store_hook(p):
        if p == "offset.begin" and st["armed"] and st["left"] > 0:
            st["left"] -= 1
            st["hits"] += 1
            other = CoreStore(h.db_path)  # a REAL competing writer bumps the revision without moving the position
            cur = other.get_offset("b", "s")
            other.advance_offset_cas("b", "s", cur.read_through_position, cur.revision)

    h = BridgeHarness(tmp_path / "ws", fault_hook=bridge_hook, store_hook=store_hook)
    (rec,) = bseed(h)
    rt = FakeRuntimeTarget()
    res = h.delivery_cycle("b", "s", rt)
    assert st["hits"] == conflicts
    assert res.status == status
    if status == "ack_failed":
        (d,) = delivery_rows(h, rec.record_id)
        assert d[7] == TERM and d[16] == 0  # terminal truth kept, NOT acked
        assert offset_row(h)[0] == 0 and len(responses(h)) == 1 and "offset_acked" not in kinds(h, d[0])
        rt2 = FakeRuntimeTarget()
        again = h.delivery_cycle("b", "s", rt2)  # conflicts are over: next cycle recovers without the runtime
        assert again.status == "recovered_terminal" and rt2.calls == []
        assert delivery_rows(h, rec.record_id)[0][16] == 1 and offset_row(h)[0] == rec.position and len(responses(h)) == 1
    else:
        assert delivery_rows(h, rec.record_id)[0][16] == 1 and offset_row(h)[0] == rec.position


# ---------------------------------------------------------------- 5. immutability bypass via REPLACE
@pytest.mark.m1_id("CERT-001")
def test_cert_001_insert_or_replace_cannot_bypass_append_only_or_terminal_immutability(bridge_h):
    bseed(bridge_h)
    more_stream(bridge_h, "s2")
    more_stream(bridge_h, "s3")
    rt = FakeRuntimeTarget()
    rt.script_deliver(("timeout",))
    d_may = bridge_h.delivery_cycle("b", "s", rt).delivery_id
    bridge_h.reconcile_uncertain(records(bridge_h)[0][0], bridge_h.append_record(
        {**req(body={"decision": "RETRY", "delivery_id": d_may}, key="rc", author="a"), "kind": "control.reconcile"}))
    bridge_h.delivery_cycle("b", "s2", FakeRuntimeTarget())  # TERMINAL delivery, evidence, session events
    tok = bridge_h.acquire_claim("b", "s3", "A", 1000)
    bridge_h.cs.finalize_terminal(tok, {"x": 1})
    cases = [("bridge_evidence", "kind", "forged"), ("bridge_deliveries", "certainty", ST), ("bridge_session_events", "event", "forged"),
             ("bridge_reconciliations", "reconcile_record_id", "rec-forged"), ("bridge_finalizations", "result_json", '{"forged":1}')]
    for table, col, val in cases:
        where = " WHERE certainty='TERMINAL'" if table == "bridge_deliveries" else ""
        with bridge_h.cs._tx() as c:  # the claim connection (recursive_triggers ON)
            cols = [r[1] for r in c.execute(f"PRAGMA table_info({table})")]
            row = dict(zip(cols, c.execute(f"SELECT * FROM {table}{where} LIMIT 1").fetchone()))
        before = sql(bridge_h, f"SELECT * FROM {table} ORDER BY 1")
        forged = {**row, col: val}
        with pytest.raises(sqlite3.IntegrityError):
            with bridge_h.cs._tx() as c:
                c.execute(f"INSERT OR REPLACE INTO {table} ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                          [forged[k] for k in cols])
        assert sql(bridge_h, f"SELECT * FROM {table} ORDER BY 1") == before, table
    # control: the mutable mapping table accepts REPLACE (immutability is targeted, not blanket)
    with bridge_h.cs._tx() as c:
        c.execute("INSERT OR REPLACE INTO bridge_sessions SELECT stream_id, peer_id, runtime_kind, external_session_id, "
                  "session_generation, adapter_fingerprint, binding, resumable, state, last_seen + 1 FROM bridge_sessions LIMIT 1")


@pytest.mark.m1_id("CERT-001")
def test_cert_001_terminal_digest_and_timestamp_cannot_be_rewritten(bridge_h):
    bseed(bridge_h)
    bridge_h.delivery_cycle("b", "s", FakeRuntimeTarget())
    before = delivery_rows(bridge_h)
    for col, val in (("result_digest", "0" * 64), ("terminal_at", "1999-01-01T00:00:00Z"), ("result_json", "{}"), ("certainty", ST)):
        with pytest.raises(sqlite3.IntegrityError):
            with bridge_h.cs._tx() as c:
                c.execute(f"UPDATE bridge_deliveries SET {col}=?", (val,))
        assert delivery_rows(bridge_h) == before
    with bridge_h.cs._tx() as c:
        c.execute("UPDATE bridge_deliveries SET acked=1")  # control: bookkeeping column stays writable


# ---------------------------------------------------------------- 6. control records / binding
@pytest.mark.m1_id("BRG-010")
@pytest.mark.parametrize("kind,status,consumed", [("control.pause", "paused", False), ("control.cancel", "idle", True),
                                                  ("context.boundary", "idle", True)])
def test_brg_010_control_records_are_never_delivered_as_prompts(bridge_h, kind, status, consumed):
    """Wave 4 replaces the Wave-3 interim 'pending_control' (D-W3-7): control Records get real semantics (see tests/m1/control)."""
    bseed(bridge_h)
    rt = FakeRuntimeTarget()
    assert bridge_h.delivery_cycle("b", "s", rt).status == "delivered"
    calls = list(rt.calls)
    ctl = bridge_h.append_record({**req(body={"x": 1}, key="ctl", author="a"), "kind": kind})
    for _ in range(2):
        res = bridge_h.delivery_cycle("b", "s", rt)
        assert res.status == status
    assert [c for c in rt.calls if c[0] == "deliver"] == [c for c in calls if c[0] == "deliver"]  # never offered to the runtime
    assert (offset_row(bridge_h)[0] >= ctl.position) is consumed  # consumed (not blocking) unless a pause gates delivery
    assert [r[0] for r in sql(bridge_h, "SELECT record_id FROM bridge_controls")] == [ctl.record_id]


@pytest.mark.m1_id("BRG-005")
def test_brg_005_binding_change_forces_fresh_generation(bridge_h):
    bseed(bridge_h)
    rt = FakeRuntimeTarget(binding="model-a/profile-1")
    bridge_h.delivery_cycle("b", "s", rt)
    assert bridge_h.current_mapping("b", "s")["binding"] == "model-a/profile-1"
    bridge_h.append_record(req(body="n", key="n", author="a"))
    rt.set_binding("model-b/profile-1")
    res = bridge_h.delivery_cycle("b", "s", rt)
    assert res.session_generation == 2 and ("resume", "ext-1") not in rt.calls
    assert bridge_h.current_mapping("b", "s")["binding"] == "model-b/profile-1"
    assert sql(bridge_h, "SELECT event FROM bridge_session_events ORDER BY seq")[1] == ("binding_change",)
    bridge_h.append_record(req(body="n2", key="n2", author="a"))  # control: same binding resumes
    bridge_h.delivery_cycle("b", "s", rt)
    assert ("resume", "ext-2") in rt.calls and rt.count("create") == 2


@pytest.mark.m1_id("CERT-001")
def test_cert_001_foreign_scope_token_cannot_touch_terminal_delivery_evidence(bridge_h):
    bseed(bridge_h)
    more_stream(bridge_h, "s2")
    res = bridge_h.delivery_cycle("b", "s", FakeRuntimeTarget(), )
    tok_s2 = bridge_h.acquire_claim("b", "s2", "other", 1000)
    before = delivery_rows(bridge_h), evidence(bridge_h)
    for result in ({"response": "ok"}, {"response": "different"}):  # identical-repeat and conflicting shapes
        with pytest.raises(ClaimScopeError):
            bridge_h.finalize_terminal(tok_s2, result, delivery_id=res.delivery_id)
    assert (delivery_rows(bridge_h), evidence(bridge_h)) == before  # no duplicate/conflict evidence written for a foreign scope
    tok_s = bridge_h.acquire_claim("b", "s", "bridge-1")
    assert bridge_h.finalize_terminal(tok_s, {"response": "ok"}, delivery_id=res.delivery_id).outcome == "duplicate"  # control


@pytest.mark.m1_id("CERT-001")
def test_cert_001_every_append_only_table_rejects_update_and_delete(bridge_h):
    bseed(bridge_h)
    more_stream(bridge_h, "s2")
    more_stream(bridge_h, "s3")
    rt = FakeRuntimeTarget()
    rt.script_deliver(("timeout",))
    d = bridge_h.delivery_cycle("b", "s", rt).delivery_id
    bridge_h.reconcile_uncertain(records(bridge_h)[0][0], bridge_h.append_record(
        {**req(body={"decision": "RETRY", "delivery_id": d}, key="rc", author="a"), "kind": "control.reconcile"}))
    bridge_h.delivery_cycle("b", "s2", FakeRuntimeTarget())
    bridge_h.cs.finalize_terminal(bridge_h.acquire_claim("b", "s3", "A", 1000), {"x": 1})
    stmts = {
        "bridge_session_events": ["UPDATE bridge_session_events SET event='x'", "DELETE FROM bridge_session_events"],
        "bridge_finalizations": ["UPDATE bridge_finalizations SET result_json='{}'", "DELETE FROM bridge_finalizations"],
        "bridge_reconciliations": ["UPDATE bridge_reconciliations SET reconcile_record_id='x'", "UPDATE bridge_reconciliations SET decision='X'",
                                   "DELETE FROM bridge_reconciliations"],
        "bridge_evidence": ["UPDATE bridge_evidence SET kind='x'", "DELETE FROM bridge_evidence"],
        "bridge_deliveries": ["DELETE FROM bridge_deliveries"],
    }
    for table, lst in stmts.items():
        before = sql(bridge_h, f"SELECT * FROM {table} ORDER BY 1")
        assert before, table
        for stmt in lst:
            with pytest.raises(sqlite3.IntegrityError):
                with bridge_h.cs._tx() as c:
                    c.execute(stmt)
        assert sql(bridge_h, f"SELECT * FROM {table} ORDER BY 1") == before, table
    with bridge_h.cs._tx() as c:  # control: the consumption marker is bookkeeping and stays writable
        c.execute("UPDATE bridge_reconciliations SET consumed_attempt=2")
