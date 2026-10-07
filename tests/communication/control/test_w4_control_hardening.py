"""Wave 4 hardening: pending-control semantics (replaces the Wave-3 interim), honour-before-side-effect, cancel scope, replay/duplicate safety,
authentication against the persisted Record, immutability of control rows, and the control state machine (allowed AND forbidden)."""
import json
import sqlite3
import threading

import pytest

from peerhub.extensions.bridge import ControlRejectedError
from tests.communication.control_helpers import (all_record_rows, bseed, control_evidence, control_rows, ctl, delivery_rows, evidence, kinds, msg,
                                      offset_row, records, responses, running, sql)
from tests.communication.fakes import CrashInjected, CrashInjector, FakeRuntimeTarget
from tests.communication.harness.bridge import BridgeHarness
from tests.communication.harness.mp import run_threads

pytestmark = [pytest.mark.control, pytest.mark.integration]


def _cycles(h, rt, n=8):
    out = []
    for _ in range(n):
        r = h.delivery_cycle("b", "s", rt)
        out.append(r)
        if r.status in ("idle", "paused"):
            break
    return out


# ------------------------------------------------------------------ pending controls no longer block (replaces D-W3-7 interim)
@pytest.mark.catalog_id("BRG-010")
def test_brg_010_pending_controls_are_consumed_and_never_block_later_records(bridge_h):
    (m0,) = bseed(bridge_h)
    rt = FakeRuntimeTarget()
    assert bridge_h.delivery_cycle("b", "s", rt).status == "delivered"
    ctl(bridge_h, "context.boundary", "cb", body={"note": "external"})  # foreign-authored boundary marker: noted, never a prompt
    ctl(bridge_h, "control.frobnicate", "cf")  # unknown control kind: explicit unsupported outcome, not a prompt, not a blocker
    after = msg(bridge_h, "after")
    delivered = [c for c in rt.calls if c[0] == "deliver"]
    res = _cycles(bridge_h, rt)
    assert [r.status for r in res][:1] == ["delivered"] and res[0].record_id == after.record_id
    assert [c[2] for c in rt.calls if c[0] == "deliver"][len(delivered):] == [after.record_id]  # only the real message reached the runtime
    outcomes = {r[3]: r[6] for r in control_rows(bridge_h)}
    assert outcomes == {"context.boundary": "noted", "control.frobnicate": "unsupported_kind"}
    assert offset_row(bridge_h)[0] == max(r[1] for r in records(bridge_h))  # nothing is left pending


@pytest.mark.catalog_id("BRG-010")
def test_brg_010_pause_gates_delivery_until_resume_then_everything_flows(bridge_h):
    (m0,) = bseed(bridge_h)
    ctl(bridge_h, "control.pause", "p1")
    rt = FakeRuntimeTarget()
    for _ in range(2):
        res = bridge_h.delivery_cycle("b", "s", rt)
        assert res.status == "paused"
    assert rt.calls == [] and offset_row(bridge_h)[0] == 0 and delivery_rows(bridge_h) == [] and responses(bridge_h) == []
    ctl(bridge_h, "control.resume", "r1")
    res = _cycles(bridge_h, rt)
    assert res[0].status == "delivered" and res[0].record_id == m0.record_id
    assert offset_row(bridge_h)[0] == max(r[1] for r in records(bridge_h)) and len(responses(bridge_h)) == 1


@pytest.mark.catalog_id("BRG-010")
def test_brg_010_cycle_applied_control_is_replayed_not_reapplied_by_explicit_handle(bridge_h):
    bseed(bridge_h)
    rt, _ = running(bridge_h)
    pause = ctl(bridge_h, "control.pause", "p1")
    before_delivery = delivery_rows(bridge_h)
    res = bridge_h.delivery_cycle("b", "s", rt)  # lookahead applies the pause (interrupt) before anything else
    assert res.status == "paused" and rt.count("interrupt") == 1
    again = bridge_h.handle_control(pause.record_id, rt, "b")
    assert again.status == "replayed" and again.runtime_outcome == "done" and rt.count("interrupt") == 1
    assert len(control_rows(bridge_h)) == 1 and len(control_evidence(bridge_h, pause.record_id)) == 1
    assert [d[7] for d in delivery_rows(bridge_h)] == [d[7] for d in before_delivery] == ["STARTED"]  # certainty ledger untouched


# ------------------------------------------------------------------ intent honoured BEFORE any side effect
@pytest.mark.catalog_id("BRG-008")
def test_brg_008_pause_present_before_first_cycle_produces_no_side_effect_at_all(bridge_h):
    (m0,) = bseed(bridge_h)
    ctl(bridge_h, "control.pause", "p1")
    rt = FakeRuntimeTarget()
    res = bridge_h.delivery_cycle("b", "s", rt)
    assert res.status == "paused"
    assert rt.calls == []  # no session create/resume, no deliver
    assert sql(bridge_h, "SELECT COUNT(*) FROM bridge_sessions") == [(0,)] and delivery_rows(bridge_h) == []
    assert offset_row(bridge_h) == (0, 1) and responses(bridge_h) == []
    # positive control: without the pause the same Record is delivered
    ok = BridgeHarness(bridge_h.workspace.parent / "ws-ok")
    bseed(ok)
    assert ok.delivery_cycle("b", "s", FakeRuntimeTarget()).status == "delivered"


@pytest.mark.catalog_id("BRG-008")
@pytest.mark.parametrize("point,session_calls", [("bridge.before_session_resolve", 0), ("bridge.before_marker", 1)])
def test_brg_008_pause_appended_mid_cycle_is_honoured_before_the_runtime_is_invoked(tmp_path, point, session_calls):
    fired = []

    def hook(p):
        if p == point and not fired:
            fired.append(p)
            ctl(h, "control.pause", "late-pause")  # a durable pause lands while the cycle is between steps

    h = BridgeHarness(tmp_path / "ws", fault_hook=hook)
    (m0,) = bseed(h)
    rt = FakeRuntimeTarget()
    res = h.delivery_cycle("b", "s", rt)
    assert fired == [point] and res.status == "paused"
    assert rt.count("deliver") == 0 and rt.count("create") == session_calls  # the runtime was never invoked
    (d,) = delivery_rows(h, m0.record_id)
    assert d[7] == "NOT_STARTED" and "about_to_invoke" not in kinds(h) and responses(h) == []  # no marker: still safely retryable
    ctl(h, "control.resume", "late-resume")
    res2 = h.delivery_cycle("b", "s", rt)
    assert res2.status == "delivered" and res2.delivery_id == d[0] and len(delivery_rows(h, m0.record_id)) == 1  # same attempt reused


@pytest.mark.catalog_id("BRG-008")
def test_brg_008_non_pause_control_mid_cycle_does_not_stop_delivery(tmp_path):
    fired = []

    def hook(p):
        if p == "bridge.before_marker" and not fired:
            fired.append(p)
            ctl(h, "control.resume", "late-resume")

    h = BridgeHarness(tmp_path / "ws", fault_hook=hook)
    bseed(h)
    assert h.delivery_cycle("b", "s", FakeRuntimeTarget()).status == "delivered" and fired  # control for the pause tests above


@pytest.mark.catalog_id("CTL-003")
def test_ctl_003_pause_is_scoped_to_its_stream_and_peer(bridge_h):
    bseed(bridge_h, peers=("a", "b", "c"))
    bridge_h.create_stream({"stream_id": "s2", "members": ["a", "b", "c"]})
    msg(bridge_h, "n2", stream="s2")
    ctl(bridge_h, "control.pause", "p1", targets=["b"])
    rt = FakeRuntimeTarget()
    assert bridge_h.delivery_cycle("b", "s", rt).status == "paused"
    assert bridge_h.delivery_cycle("b", "s2", rt).status == "delivered"  # other Stream unaffected
    other = FakeRuntimeTarget()
    assert bridge_h.delivery_cycle("c", "s", other).status == "delivered"  # other peer unaffected (pause targets b)


# ------------------------------------------------------------------ cancel scope (D-W4-7: no skipping of earlier Records)
@pytest.mark.catalog_id("CTL-001")
def test_ctl_001_cancel_never_skips_a_delivery_that_already_started(bridge_h):
    (m0,) = bseed(bridge_h)
    rt, _ = running(bridge_h)
    cancel = ctl(bridge_h, "control.cancel", "cx")
    assert bridge_h.handle_control(cancel.record_id, rt, "b").runtime_outcome == "done"
    follow = FakeRuntimeTarget()
    res = bridge_h.delivery_cycle("b", "s", follow)
    assert res.status == "blocked_uncertain" and follow.calls == []  # STARTED truth is preserved, never skipped or replayed
    assert delivery_rows(bridge_h, m0.record_id)[0][7] == "STARTED" and "cancelled_skip" not in kinds(bridge_h)


# ------------------------------------------------------------------ replay / duplicate safety
@pytest.mark.catalog_id("BRG-008")
def test_brg_008_applying_the_same_control_twice_has_one_effect_one_row_one_evidence(bridge_h):
    bseed(bridge_h)
    rt, _ = running(bridge_h)
    pause = ctl(bridge_h, "control.pause", "p1")
    first = bridge_h.handle_control(pause.record_id, rt, "b")
    second = bridge_h.handle_control(pause.record_id, rt, "b")
    assert (first.status, second.status) == ("applied", "replayed") and first.runtime_outcome == second.runtime_outcome == "done"
    assert rt.count("interrupt") == 1 and len(control_rows(bridge_h)) == 1 and len(control_evidence(bridge_h, pause.record_id)) == 1
    assert control_rows(bridge_h)[0][9] == 1  # attempts


@pytest.mark.catalog_id("BRG-008")
@pytest.mark.concurrency
def test_brg_008_concurrent_duplicate_control_application_runs_the_effect_once(bridge_h):
    bseed(bridge_h)
    rt, _ = running(bridge_h)
    pause = ctl(bridge_h, "control.pause", "p1")
    n = 4
    barrier = threading.Barrier(n)

    def worker(i):
        def run():
            nb = bridge_h.new_bridge(f"o{i}")
            barrier.wait(timeout=20)
            return nb.handle_control(pause.record_id, rt, "b")
        return run

    out = run_threads([worker(i) for i in range(n)])
    assert all(not isinstance(o, BaseException) for o in out), out
    statuses = sorted(o.status for o in out)
    assert statuses.count("applied") == 1 and set(statuses) <= {"applied", "replayed", "in_progress"}
    assert rt.count("interrupt") == 1
    assert [r[6:10:3] for r in control_rows(bridge_h)] == [("done", 1)] and len(control_evidence(bridge_h, pause.record_id)) == 1
    assert bridge_h.handle_control(pause.record_id, rt, "b").status == "replayed"


@pytest.mark.catalog_id("BRG-008")
@pytest.mark.fault
def test_brg_008_superseded_handler_cannot_write_an_outcome(bridge_h):
    bseed(bridge_h)
    rt, _ = running(bridge_h)
    pause = ctl(bridge_h, "control.pause", "p1")
    nb, inner = bridge_h.new_bridge("B"), []

    def takeover_while_a_is_inside_the_effect():
        bridge_h.clock.advance(31)  # A's lease lapses while A is still inside its runtime call
        inner.append(nb.handle_control(pause.record_id, rt, "b"))

    rt.script_effect("interrupt", takeover_while_a_is_inside_the_effect)
    a = bridge_h.handle_control(pause.record_id, rt, "b")
    assert inner[0].status == "applied" and inner[0].runtime_outcome == "done"
    assert a.status == "superseded" and a.runtime_outcome is None  # A's late completion is fenced out
    assert [r[6:10:3] for r in control_rows(bridge_h)] == [("done", 2)] and len(control_evidence(bridge_h, pause.record_id)) == 1
    assert rt.count("interrupt") == 2
    # positive control: an uncontested handler on a fresh Record writes its own outcome
    p2 = ctl(bridge_h, "control.pause", "p2")
    assert bridge_h.handle_control(p2.record_id, rt, "b").status == "applied" and control_rows(bridge_h, p2.record_id)[0][6] == "done"


@pytest.mark.catalog_id("BRG-008")
@pytest.mark.fault
def test_brg_008_superseded_handler_cannot_complete_while_the_new_lease_holder_is_undecided(bridge_h):
    bseed(bridge_h)
    rt, _ = running(bridge_h)
    pause = ctl(bridge_h, "control.pause", "p1")
    crash = CrashInjector()
    nb, hit = bridge_h.new_bridge("B", fault_hook=crash), []

    def takeover_then_b_dies_before_its_outcome():
        bridge_h.clock.advance(31)
        crash.arm("control.after_effect_before_outcome")
        try:
            nb.handle_control(pause.record_id, rt, "b")
        except CrashInjected:
            hit.append(1)

    rt.script_effect("interrupt", takeover_then_b_dies_before_its_outcome)
    a = bridge_h.handle_control(pause.record_id, rt, "b")
    assert hit == [1] and a.status == "superseded"
    assert control_rows(bridge_h, pause.record_id)[0][6:10:3] == (None, 2) and control_evidence(bridge_h, pause.record_id) == []  # A wrote nothing
    crash.disarm()
    bridge_h.clock.advance(31)
    assert nb.handle_control(pause.record_id, rt, "b").status == "applied"  # the lease holder's successor completes it
    assert control_rows(bridge_h, pause.record_id)[0][6:10:3] == ("done", 3)


# ------------------------------------------------------------------ authenticate against the persisted Record
@pytest.fixture
def three(bridge_h):
    bseed(bridge_h, peers=("a", "b", "c"))
    return bridge_h


def _state(h, rt):
    return (control_rows(h), all_record_rows(h), offset_row(h), list(rt.calls), len(evidence(h)))


@pytest.mark.catalog_id("CTL-003")
def test_ctl_003_forged_or_unauthorised_control_input_has_no_effect(three):
    h = three
    rt, _ = running(h)
    plain = msg(h, "plain")  # an ordinary message; a caller can copy/edit it into something control-shaped
    forged = plain.model_copy(update={"kind": "control.cancel"})
    own = ctl(h, "control.cancel", "own", author="b")
    elsewhere = ctl(h, "control.cancel", "tgt", targets=["c"])
    cases = [
        ("rec-never-persisted", "b", "not durable"),
        (forged.record_id, "b", "not a control"),  # the edited copy's id still resolves to the persisted MESSAGE
        (own.record_id, "b", "bridged peer"),
        (elsewhere.record_id, "b", "not addressed"),
        (elsewhere.record_id, "zz", "not a member"),
    ]
    for rid, peer, match in cases:
        before = _state(h, rt)
        with pytest.raises(ControlRejectedError, match=match):
            h.handle_control(rid, rt, peer)
        assert _state(h, rt) == before, match
    amb = ctl(h, "control.pause", "amb")  # three members, no targets, no peer_id: cannot tell who is addressed
    before = _state(h, rt)
    with pytest.raises(ControlRejectedError, match="cannot resolve"):
        h.handle_control(amb.record_id, rt)
    assert _state(h, rt) == before
    with pytest.raises(TypeError, match="record_id"):
        h.handle_control(forged, rt, "b")  # caller copies are not accepted as input at all
    assert _state(h, rt) == before
    ok = h.handle_control(elsewhere.record_id, rt, "c")  # positive control: the addressed peer is accepted
    assert ok.status == "applied" and ok.peer_id == "c"
    assert h.handle_control(amb.record_id, rt, "b").status == "applied"  # explicit peer resolves the ambiguity


@pytest.mark.catalog_id("CTL-003")
def test_ctl_003_two_member_stream_resolves_the_addressed_peer_from_the_persisted_record(bridge_h):
    bseed(bridge_h)
    rt, _ = running(bridge_h)
    c = ctl(bridge_h, "control.pause", "p1")
    assert bridge_h.handle_control(c.record_id, rt).peer_id == "b"


# ------------------------------------------------------------------ immutability / append-only
@pytest.mark.catalog_id("CERT-001")
def test_cert_001_control_rows_are_immutable_and_outcome_is_write_once(bridge_h):
    bseed(bridge_h)
    rt, _ = running(bridge_h)
    c = ctl(bridge_h, "control.pause", "p1")
    bridge_h.handle_control(c.record_id, rt, "b")
    cols = [r[1] for r in sql(bridge_h, "PRAGMA table_info(bridge_controls)")]
    row = dict(zip(cols, sql(bridge_h, "SELECT * FROM bridge_controls")[0]))
    before = sql(bridge_h, "SELECT * FROM bridge_controls")
    bad = [("UPDATE bridge_controls SET kind='control.resume'", ()), ("UPDATE bridge_controls SET position=99", ()),
           ("UPDATE bridge_controls SET peer_id='zz'", ()), ("UPDATE bridge_controls SET outcome='failed'", ()),
           ("DELETE FROM bridge_controls", ())]
    for q, args in bad:
        with pytest.raises(sqlite3.IntegrityError):
            with bridge_h.cs._tx() as conn:  # the claim connection has recursive_triggers ON
                conn.execute(q, args)
        assert sql(bridge_h, "SELECT * FROM bridge_controls") == before, q
    for col, val in (("kind", "control.resume"), ("outcome", "failed"), ("record_id", row["record_id"])):
        forged = {**row, col: val}
        with pytest.raises(sqlite3.IntegrityError):
            with bridge_h.cs._tx() as conn:
                conn.execute(f"INSERT OR REPLACE INTO bridge_controls ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                             [forged[k] for k in cols])
        assert sql(bridge_h, "SELECT * FROM bridge_controls") == before, col
    with bridge_h.cs._tx() as conn:  # controls: bookkeeping stays writable; an undecided row accepts exactly one outcome
        conn.execute("UPDATE bridge_controls SET attempts=attempts+1")
        conn.execute("INSERT INTO bridge_controls (record_id, stream_id, peer_id, kind, position, effect) VALUES ('rX','s','b','control.pause',99,'interrupt')")
        conn.execute("UPDATE bridge_controls SET outcome='done' WHERE record_id='rX'")
    assert sql(bridge_h, "SELECT outcome FROM bridge_controls WHERE record_id='rX'") == [("done",)]
    with pytest.raises(sqlite3.IntegrityError):
        with bridge_h.cs._tx() as conn:
            conn.execute("UPDATE bridge_controls SET outcome='failed' WHERE record_id='rX'")
    assert sql(bridge_h, "SELECT outcome FROM bridge_controls WHERE record_id='rX'") == [("done",)]


# ------------------------------------------------------------------ control state machine: allowed AND forbidden
SEQS = {  # literal oracle: (controls in position order) -> (paused, position of the last cancel or 0)
    ("pause",): (True, 0), ("pause", "resume"): (False, 0), ("resume", "pause"): (True, 0), ("pause", "cancel"): (True, 2),
    ("pause", "resume", "pause"): (True, 0), ("cancel",): (False, 1), ("resume",): (False, 0), ("pause", "pause"): (True, 0),
    ("cancel", "pause", "resume"): (False, 1), ("pause", "cancel", "resume", "cancel"): (False, 4),
}


@pytest.mark.catalog_id("CTL-003")
@pytest.mark.parametrize("seq", list(SEQS), ids=lambda s: "-".join(s))
@pytest.mark.parametrize("order", ["forward", "reverse"])
def test_ctl_003_control_state_is_a_function_of_positions_not_of_handling_order(tmp_path, seq, order):
    h = BridgeHarness(tmp_path / "ws")
    bseed(h, n=0)
    recs = [ctl(h, f"control.{k}", f"c{i}") for i, k in enumerate(seq)]
    rt = FakeRuntimeTarget()
    for r in (recs if order == "forward" else reversed(recs)):
        assert h.handle_control(r.record_id, rt, "b").status == "applied"
    st = h.bridge.control_state("b", "s")
    paused, fence = SEQS[seq]
    assert st["paused"] is paused and st["last_cancel_position"] == fence
    assert h.delivery_cycle("b", "s", rt).status == ("paused" if paused else "idle")
    assert rt.calls == []  # no session -> controls never reached a runtime; gating itself has no side effect
    assert len(control_rows(h)) == len(seq)  # forbidden: a second row for the same Record


@pytest.mark.catalog_id("CTL-003")
def test_ctl_003_cancel_pause_resume_never_remove_unread_work(tmp_path):
    h = BridgeHarness(tmp_path / "ws")
    pre = bseed(h, n=1)
    for k in ("cancel", "pause", "resume"):
        ctl(h, f"control.{k}", k)
    rt = FakeRuntimeTarget()
    res = _cycles(h, rt)
    assert [c[2] for c in rt.calls if c[0] == "deliver"] == [pre[0].record_id] and res[-1].status == "idle"  # D-W4-7: later Records still flow
    assert sql(h, "SELECT COUNT(*) FROM records WHERE record_id=?", (pre[0].record_id,)) == [(1,)]  # history kept
