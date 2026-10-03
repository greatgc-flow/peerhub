"""Wave 3 BRG-006/007/011..015/019: delivery cycle, execution evidence, terminal idempotency (TD-11, TD-25, STM-030..037)."""
import json
import multiprocessing as mp
import threading

import pytest

from peerhub.extensions.bridge import TerminalConflictError
from peerhub.extensions.bridge_claims import StaleClaimError
from tests.m1.bridge_helpers import (bseed, delivery_rows, evidence, kinds, more_stream, offset_row, records, responses, sql)
from tests.m1.fakes import CrashInjected, CrashInjector, FakeRuntimeTarget
from tests.m1.harness.bridge import BridgeHarness
from tests.m1.harness.bridge_workers import crash_cycle_worker
from tests.m1.harness.mp import run_threads

pytestmark = [pytest.mark.integration, pytest.mark.bridge]


def _delivery_id(h, rec):
    return delivery_rows(h, rec.record_id)[-1][0]


@pytest.mark.m1_id("BRG-006")
def test_brg_006_response_record_commits_before_offset_ack(tmp_path):
    seen = {}

    def store_hook(point):
        if point == "offset.begin":  # snapshot the DURABLE state at the instant the Offset write begins
            seen.setdefault("resp_at_ack", []).append(len(responses(h)))

    h = BridgeHarness(tmp_path / "ws", store_hook=store_hook)
    (rec,) = bseed(h)
    rt = FakeRuntimeTarget()
    rt.script_deliver(("started", "e1"), ("terminal", {"response": "pong"}))
    res = h.delivery_cycle("b", "s", rt)
    assert res.status == "delivered" and res.certainty == "TERMINAL"
    did = _delivery_id(h, rec)
    assert kinds(h, did) == ["attempt_created", "about_to_invoke", "started", "terminal", "response_appended", "offset_acked"]  # event-log order
    assert seen["resp_at_ack"] == [1]  # the response Record was already durable when the ack began
    (resp,) = responses(h)
    assert resp[2:4] == ("b", "response") and json.loads(resp[4]) == "pong" and resp[5] == rec.record_id
    assert offset_row(h) == (rec.position, 2)
    # restart recoverability: crash after the response commit, before the ack
    crash = CrashInjector("bridge.after_terminal_evidence_before_offset_ack")
    h2 = BridgeHarness(tmp_path / "ws2", fault_hook=crash)
    (rec2,) = bseed(h2)
    with pytest.raises(CrashInjected):
        h2.delivery_cycle("b", "s", FakeRuntimeTarget())
    assert len(responses(h2)) == 1 and offset_row(h2) == (0, 1)  # response durable, ack missing: complete old-or-new state
    rt2 = FakeRuntimeTarget()
    h2 = BridgeHarness(tmp_path / "ws2", h2.clock)  # restart
    assert h2.delivery_cycle("b", "s", rt2).status == "recovered_terminal"
    assert rt2.calls == [] and len(responses(h2)) == 1 and offset_row(h2) == (rec2.position, 2)


@pytest.mark.m1_id("BRG-007")
def test_brg_007_execution_evidence_records_certainty_and_runtime_ids(bridge_h):
    (rec,) = bseed(bridge_h)
    rt = FakeRuntimeTarget()
    rt.script_deliver(("started", "exec-9"), ("output", "par"), ("output", "tial"), ("terminal", {"response": "done"}))
    bridge_h.delivery_cycle("b", "s", rt)
    ev = bridge_h.execution_evidence(rec.record_id, "b")
    (d,) = ev["deliveries"]
    assert (d["certainty"], d["execution_id"], d["external_session_id"], d["session_generation"], d["attempt"]) == \
        ("TERMINAL", "exec-9", "ext-1", 1, 1)
    # independent oracle: the scripted lifecycle NOT_STARTED -> STARTED -> TERMINAL, in order
    assert [(e["kind"], e["certainty"]) for e in ev["events"]] == [
        ("attempt_created", "NOT_STARTED"), ("about_to_invoke", "MAY_HAVE_STARTED"), ("started", "STARTED"), ("output", "STARTED"), ("output", "STARTED"),
        ("terminal", "TERMINAL"), ("response_appended", "TERMINAL"), ("offset_acked", "TERMINAL")]
    started = [e for e in ev["events"] if e["kind"] == "started"][0]["detail"]
    assert started == {"execution_id": "exec-9", "external_session_id": "ext-1", "session_generation": 1}
    assert [e["detail"]["chunk"] for e in ev["events"] if e["kind"] == "output"] == ["par", "tial"]
    assert d["response_record_id"] == responses(bridge_h)[0][0]  # recovery correlation to the durable response
    assert bridge_h.execution_evidence("rec-nonexistent", "b") == {"deliveries": [], "events": []}  # control: other record has none


@pytest.mark.fault
@pytest.mark.m1_id("BRG-011")
def test_brg_011_prespawn_failure_stays_not_started_and_is_retryable(bridge_h):
    (rec,) = bseed(bridge_h)
    rt = FakeRuntimeTarget()
    rt.script_deliver(("prespawn_error", "binary not found"))
    res = bridge_h.delivery_cycle("b", "s", rt)
    assert res.status == "failed_not_started" and res.certainty == "NOT_STARTED" and res.detail == {"error": "binary not found"}
    ev = bridge_h.execution_evidence(rec.record_id, "b")
    assert ev["deliveries"][0]["certainty"] == "NOT_STARTED" and ev["deliveries"][0]["execution_id"] is None
    fail = [e for e in ev["events"] if e["kind"] == "prespawn_failure"]
    assert len(fail) == 1 and fail[0]["detail"] == {"error": "binary not found"} and fail[0]["certainty"] == "NOT_STARTED"
    assert offset_row(bridge_h) == (0, 1) and responses(bridge_h) == []  # Offset unchanged
    # retry is safe: same attempt row is reused, runtime is called again, and it completes
    res2 = bridge_h.delivery_cycle("b", "s", rt)
    assert res2.status == "delivered" and rt.count("deliver") == 2
    rows = delivery_rows(bridge_h, rec.record_id)
    assert len(rows) == 1 and rows[0][6] == 1 and rows[0][7] == "TERMINAL"
    assert offset_row(bridge_h) == (rec.position, 2)


@pytest.mark.fault
@pytest.mark.m1_id("BRG-012")
@pytest.mark.parametrize("script,certainty,ev_kind", [
    ([("started", "e1"), ("timeout",)], "STARTED", "runtime_timeout"),
    ([("timeout",)], "MAY_HAVE_STARTED", "start_uncertain"),
    ([("started", "e1"), ("disconnect",)], "STARTED", "runtime_disconnect"),
    ([("runtime_error", "pipe broke")], "MAY_HAVE_STARTED", "start_uncertain"),
])
def test_brg_012_post_start_timeout_is_never_not_started_and_blocks_replay(bridge_h, script, certainty, ev_kind):
    (rec,) = bseed(bridge_h)
    rt = FakeRuntimeTarget()
    rt.script_deliver(*script)
    res = bridge_h.delivery_cycle("b", "s", rt)
    assert res.status == "uncertain" and res.certainty == certainty != "NOT_STARTED"
    d = bridge_h.execution_evidence(rec.record_id, "b")["deliveries"][0]
    assert d["certainty"] == certainty and ev_kind in kinds(bridge_h, d["delivery_id"])
    assert offset_row(bridge_h) == (0, 1) and responses(bridge_h) == []
    before = delivery_rows(bridge_h, rec.record_id)
    for _ in range(2):  # automatic replay stays blocked (STM-034), the runtime is never called again
        again = bridge_h.delivery_cycle("b", "s", rt)
        assert again.status == "blocked_uncertain" and again.certainty == certainty
    assert rt.count("deliver") == 1 and delivery_rows(bridge_h, rec.record_id) == before and offset_row(bridge_h) == (0, 1)


@pytest.mark.fault
@pytest.mark.m1_id("BRG-013")
def test_brg_013_partial_output_then_crash_fabricates_no_terminal(tmp_path):
    crash = CrashInjector("bridge.after_partial_output")
    h = BridgeHarness(tmp_path / "ws", fault_hook=crash)
    (rec,) = bseed(h)
    rt = FakeRuntimeTarget()
    rt.script_deliver(("started", "e1"), ("output", "chunk-1"), ("output", "chunk-2"), ("terminal", {"response": "never"}))
    with pytest.raises(CrashInjected):
        h.delivery_cycle("b", "s", rt)
    did = _delivery_id(h, rec)
    assert kinds(h, did) == ["attempt_created", "about_to_invoke", "started", "output"]  # start + first partial persisted, nothing after the crash
    assert [r[3] for r in records(h)] == ["message"] and offset_row(h) == (0, 1)  # no terminal response Record
    assert delivery_rows(h, rec.record_id)[0][7] == "STARTED"
    h2 = BridgeHarness(tmp_path / "ws", h.clock)  # recovery
    rt2 = FakeRuntimeTarget()
    res = h2.delivery_cycle("b", "s", rt2)
    assert res.status == "blocked_uncertain" and res.certainty == "STARTED"  # certainty not downgraded, no replay
    assert rt2.calls == [] and delivery_rows(h2, rec.record_id)[0][7] == "STARTED" and responses(h2) == []
    assert "terminal" not in kinds(h2, did)
    # positive control: crash at a point BEFORE start leaves NOT_STARTED and recovery redelivers
    crash2 = CrashInjector("claim.after_acquire")
    h3 = BridgeHarness(tmp_path / "ws3", fault_hook=crash2)
    bseed(h3)
    with pytest.raises(CrashInjected):
        h3.delivery_cycle("b", "s", FakeRuntimeTarget())
    assert BridgeHarness(tmp_path / "ws3", h3.clock).delivery_cycle("b", "s", FakeRuntimeTarget()).status == "delivered"


def _child(ws, crash_point, script, clock):
    ctx = mp.get_context("spawn")
    p = ctx.Process(target=crash_cycle_worker, args=(str(ws), "b", "s", crash_point, script, clock), daemon=True)
    p.start()
    p.join(90)
    alive = p.is_alive()
    if alive:
        p.terminate()
    assert not alive, "child hung"
    return p.exitcode


@pytest.mark.fault
@pytest.mark.m1_id("BRG-013")
def test_brg_013_real_process_death_after_partial_output(tmp_path):
    h = BridgeHarness(tmp_path / "ws")
    (rec,) = bseed(h)
    assert _child(tmp_path / "ws", "bridge.after_partial_output",
                  [["started", "e1"], ["output", "x"], ["terminal", {"response": "r"}]], h.clock.now()) == 17  # died at the crash point
    h2 = BridgeHarness(tmp_path / "ws", h.clock)
    assert [r[3] for r in records(h2)] == ["message"] and offset_row(h2) == (0, 1)
    rt = FakeRuntimeTarget()
    assert h2.delivery_cycle("b", "s", rt).status == "blocked_uncertain" and rt.calls == []
    assert delivery_rows(h2, rec.record_id)[0][7] == "STARTED"


@pytest.mark.concurrency
@pytest.mark.m1_id("BRG-014")
def test_brg_014_duplicate_terminal_callbacks_finalize_once(bridge_h):
    bseed(bridge_h)
    for i in range(1, 8):
        more_stream(bridge_h, f"s{i}")
    for rnd, st in enumerate(["s"] + [f"s{i}" for i in range(1, 8)]):
        rec = records(bridge_h, st)[0]
        rt = FakeRuntimeTarget()
        rt.script_deliver(("started", "e1"), ("output", "partial"))
        bridge_h.delivery_cycle("b", st, rt)
        tok = bridge_h.acquire_claim("b", st, "bridge-1")
        assert tok.generation == 1
        did = delivery_rows(bridge_h, rec[0])[0][0]
        n = 3 if rnd % 2 else 2
        barrier = threading.Barrier(n, timeout=30)
        bridges = [bridge_h.new_bridge("bridge-1") for _ in range(n)]

        def cb(b):
            def run():
                barrier.wait()
                return b.finalize_terminal(tok, {"response": "R1"}, did)
            return run

        outs = run_threads([cb(b) for b in bridges])
        assert all(not isinstance(o, Exception) for o in outs), outs
        assert sorted(o.outcome for o in outs) == ["duplicate"] * (n - 1) + ["first"]
        assert len({o.response_record_id for o in outs}) == 1
        assert len(responses(bridge_h, st)) == 1 and json.loads(responses(bridge_h, st)[0][4]) == "R1"
        assert offset_row(bridge_h, "b", st) == (rec[1], 2)  # Offset advanced exactly once
        did = delivery_rows(bridge_h, rec[0])[0][0]
        k = kinds(bridge_h, did)
        assert k.count("terminal") == 1 and k.count("response_appended") == 1 and k.count("offset_acked") == 1
        assert k.count("duplicate_terminal") == n - 1
        assert delivery_rows(bridge_h, rec[0])[0][7] == "TERMINAL"
    # positive control: a later identical callback (after the ack) is still an idempotent no-op
    again = bridge_h.finalize_terminal(tok, {"response": "R1"})
    assert again.outcome == "duplicate" and len(responses(bridge_h, "s7")) == 1


@pytest.mark.concurrency
@pytest.mark.m1_id("BRG-019")
def test_brg_019_conflicting_duplicate_terminal_does_not_overwrite_truth(bridge_h):
    (rec,) = bseed(bridge_h)
    rt = FakeRuntimeTarget()
    rt.script_deliver(("started", "e1"), ("terminal", {"response": "R1"}))
    bridge_h.delivery_cycle("b", "s", rt)
    tok = bridge_h.acquire_claim("b", "s", "bridge-1")
    committed = (delivery_rows(bridge_h, rec.record_id), responses(bridge_h), offset_row(bridge_h))
    assert bridge_h.finalize_terminal(tok, {"response": "R1"}).outcome == "duplicate"  # control: identical repeat is idempotent
    with pytest.raises(TerminalConflictError):
        bridge_h.finalize_terminal(tok, {"response": "R2"})
    assert (delivery_rows(bridge_h, rec.record_id), responses(bridge_h), offset_row(bridge_h)) == committed  # truth immutable
    did = committed[0][0][0]
    conflicts = sql(bridge_h, "SELECT detail FROM bridge_evidence WHERE delivery_id=? AND kind='conflicting_terminal'", (did,))
    assert len(conflicts) == 1 and json.loads(conflicts[0][0])["result"] == {"response": "R2"}  # explicit evidence


@pytest.mark.concurrency
@pytest.mark.m1_id("BRG-019")
def test_brg_019_concurrent_conflicting_terminals_exactly_one_wins(bridge_h):
    bseed(bridge_h)
    streams = ["s"] + [f"s{i}" for i in range(1, 8)]
    for st in streams[1:]:
        more_stream(bridge_h, st)
    for st in streams:
        rec = records(bridge_h, st)[0]
        rt = FakeRuntimeTarget()
        rt.script_deliver(("started", "e1"), ("output", "partial"))
        bridge_h.delivery_cycle("b", st, rt)
        tok = bridge_h.acquire_claim("b", st, "bridge-1")
        did = delivery_rows(bridge_h, rec[0])[0][0]
        barrier = threading.Barrier(2, timeout=30)
        bridges = [bridge_h.new_bridge("bridge-1") for _ in range(2)]

        def cb(b, val):
            def run():
                barrier.wait()
                return b.finalize_terminal(tok, {"response": val}, did)
            return run

        out = run_threads([cb(bridges[0], "R1"), cb(bridges[1], "R2")])
        wins = [(v, o) for v, o in zip(("R1", "R2"), out) if not isinstance(o, Exception)]
        losses = [o for o in out if isinstance(o, Exception)]
        assert len(wins) == 1 and len(losses) == 1 and isinstance(losses[0], TerminalConflictError), out
        win_val = wins[0][0]
        assert json.loads(delivery_rows(bridge_h, rec[0])[0][12]) == {"response": win_val}  # result_json
        assert [json.loads(r[4]) for r in responses(bridge_h, st)] == [win_val]
        assert offset_row(bridge_h, "b", st) == (rec[1], 2)


@pytest.mark.restart
@pytest.mark.m1_id("BRG-015")
@pytest.mark.parametrize("point", ["bridge.after_terminal_evidence_before_response_append",
                                   "bridge.after_terminal_evidence_before_offset_ack"])
def test_brg_015_real_process_restart_after_terminal_persistence_is_idempotent(tmp_path, point):
    h = BridgeHarness(tmp_path / "ws")
    (rec,) = bseed(h)
    assert _child(tmp_path / "ws", point, [["started", "e1"], ["terminal", {"response": "R"}]], h.clock.now()) == 17
    h2 = BridgeHarness(tmp_path / "ws", h.clock)
    # state the dying process left: terminal truth durable, ack not yet applied (complete old-or-new, never half a delivery)
    assert delivery_rows(h2, rec.record_id)[0][7] == "TERMINAL" and offset_row(h2) == (0, 1)
    assert len(responses(h2)) == (0 if point.endswith("response_append") else 1)
    rt = FakeRuntimeTarget()
    res = h2.delivery_cycle("b", "s", rt)
    assert res.status == "recovered_terminal"
    assert rt.calls == []  # no runtime restart: no create, resume or deliver
    assert len(responses(h2)) == 1 and json.loads(responses(h2)[0][4]) == "R"  # no duplicate response
    assert offset_row(h2) == (rec.position, 2)
    assert h2.delivery_cycle("b", "s", rt).status == "idle" and rt.calls == []  # second cycle: nothing more happens
    assert len(delivery_rows(h2, rec.record_id)) == 1


@pytest.mark.m1_id("BRG-015")
def test_brg_015_control_crash_before_terminal_evidence_is_not_recovered_as_terminal(tmp_path):
    h = BridgeHarness(tmp_path / "ws")
    (rec,) = bseed(h)
    assert _child(tmp_path / "ws", "bridge.after_runtime_start", [["started", "e1"], ["terminal", {"response": "R"}]], h.clock.now()) == 17
    h2 = BridgeHarness(tmp_path / "ws", h.clock)
    rt = FakeRuntimeTarget()
    assert h2.delivery_cycle("b", "s", rt).status == "blocked_uncertain"  # no terminal evidence, so no fabricated recovery
    assert responses(h2) == [] and rt.calls == []
