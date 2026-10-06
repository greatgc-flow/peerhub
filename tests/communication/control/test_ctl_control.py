"""Wave 4 CTL-001..005, BRG-001/008/009: control intent is durable before any runtime effect (TD-05), honoured before side effects."""
import json
import multiprocessing as mp
import threading

import pytest

from peerhub.extensions.bridge import RuntimeTargetError
from peerhub.extensions.bridge_claims import ClaimHeldError
from peerhub.extensions.direction import DirectionError, apply_direction, decide_direction
from peerhub.core.store import StreamClosedError
from tests.communication.control_helpers import (all_record_rows, bseed, control_evidence, control_rows, ctl, delivery_rows, kinds, msg, offset_row,
                                      records, responses, running, sql)
from tests.communication.fakes import CrashInjected, CrashInjector, FakeRuntimeTarget
from tests.communication.harness.bridge import BridgeHarness
from tests.communication.harness.bridge_workers import control_crash_worker

pytestmark = [pytest.mark.control, pytest.mark.integration]


def _snapshot(h, record_id):
    """Durable state seen at the instant a runtime effect is invoked (raw SQL on a fresh connection)."""
    return {"record": sql(h, "SELECT kind FROM records WHERE record_id=?", (record_id,)),
            "control": sql(h, "SELECT kind, outcome FROM bridge_controls WHERE record_id=?", (record_id,))}


# ------------------------------------------------------------------ BRG-001
@pytest.mark.catalog_id("BRG-001")
def test_brg_001_only_unread_records_are_offered_in_position_order(bridge_h):
    recs = bseed(bridge_h, n=5)  # positions 1..5
    bridge_h.store.advance_offset_cas("b", "s", 2, 1)  # peer b already read through position 2
    rt = FakeRuntimeTarget()
    offered = []
    for _ in range(6):
        res = bridge_h.delivery_cycle("b", "s", rt)
        if res.status == "idle":
            break
        assert res.status == "delivered"
        offered.append(res.record_id)
    assert offered == [r.record_id for r in recs[2:]]  # literal oracle: 3, 4, 5 in order
    assert [c[2] for c in rt.calls if c[0] == "deliver"] == offered
    assert recs[0].record_id not in offered and recs[1].record_id not in offered
    assert bridge_h.delivery_cycle("b", "s", rt).status == "idle"  # control: nothing left unread
    assert [r[2] for r in responses(bridge_h)] == ["b"] * 3


@pytest.mark.catalog_id("BRG-001")
def test_brg_001_control_records_are_consumed_not_offered_as_prompts(bridge_h):
    (m1,) = bseed(bridge_h, n=1)
    c = ctl(bridge_h, "control.resume", "c2")  # position 2: subject to control semantics
    m3 = msg(bridge_h, "m3")
    rt = FakeRuntimeTarget()
    offered = []
    for _ in range(4):
        res = bridge_h.delivery_cycle("b", "s", rt)
        if res.status == "idle":
            break
        offered.append(res.record_id)
    assert offered == [m1.record_id, m3.record_id] and c.record_id not in [x[2] for x in rt.calls if x[0] == "deliver"]
    assert offset_row(bridge_h)[0] == max(r[1] for r in records(bridge_h))  # the Offset reached the head: control did not block
    assert [r[0] for r in control_rows(bridge_h)] == [c.record_id]


# ------------------------------------------------------------------ CTL-001 / BRG-008
@pytest.mark.catalog_id("CTL-001")
def test_ctl_001_cancel_intent_is_durable_before_terminate_is_invoked(bridge_h):
    bseed(bridge_h)
    rt, _ = running(bridge_h)
    cancel = ctl(bridge_h, "control.cancel", "cx")
    seen = []
    rt.script_effect("terminate", lambda: seen.append(_snapshot(bridge_h, cancel.record_id)))
    assert [c[0] for c in rt.calls].count("terminate") == 0  # control: no terminate before the cancel is handled
    res = bridge_h.handle_control(cancel.record_id, rt, "b")
    assert res.status == "applied" and res.effect == "terminate" and res.runtime_outcome == "done"
    assert [c for c in rt.calls if c[0] == "terminate"] == [("terminate", "ext-1")]
    # at the instant terminate ran, both the cancel Record and the control intent were already committed
    assert seen == [{"record": [("control.cancel",)], "control": [("control.cancel", None)]}]
    assert control_rows(bridge_h, cancel.record_id)[0][6] == "done"


@pytest.mark.catalog_id("CTL-001")
@pytest.mark.concurrency
def test_ctl_001_cancel_while_a_delivery_is_really_running_in_another_thread(bridge_h):
    (rec,) = bseed(bridge_h)
    started, release, box, seen = threading.Event(), threading.Event(), {}, []
    rt = FakeRuntimeTarget()
    rt.script_deliver(("started", "exec-1"), ("call", lambda: (started.set(), release.wait(20))), ("output", "late"))

    def deliver():
        box["res"] = bridge_h.new_bridge("runner").delivery_cycle("b", "s", rt)

    t = threading.Thread(target=deliver, daemon=True)
    t.start()
    assert started.wait(20), "runtime never started"
    cancel = ctl(bridge_h, "control.cancel", "cx")

    def on_terminate():
        seen.append((_snapshot(bridge_h, cancel.record_id), "res" in box, sql(bridge_h, "SELECT certainty FROM bridge_deliveries")))
        release.set()

    rt.script_effect("terminate", on_terminate)
    res = bridge_h.new_bridge("controller").handle_control(cancel.record_id, rt, "b")  # a different owner/connection: no delivery claim needed
    t.join(20)
    assert not t.is_alive()
    assert res.runtime_outcome == "done"
    snap, finished, cert = seen[0]
    assert snap["record"] == [("control.cancel",)] and snap["control"] == [("control.cancel", None)]  # durable before the effect
    assert finished is False and cert == [("STARTED",)]  # the delivery was genuinely in flight
    assert box["res"].status == "uncertain" and box["res"].certainty == "STARTED"  # terminated run: no terminal -> never replayed
    follow = FakeRuntimeTarget()
    with pytest.raises(ClaimHeldError):  # the runner's lease is still live: the controller never took the delivery claim
        bridge_h.delivery_cycle("b", "s", follow)
    bridge_h.clock.advance(31)
    assert bridge_h.delivery_cycle("b", "s", follow).status == "blocked_uncertain" and follow.count("deliver") == 0
    assert delivery_rows(bridge_h, rec.record_id)[0][7] == "STARTED"


@pytest.mark.catalog_id("CTL-001")
def test_ctl_001_cancel_with_nothing_running_makes_no_runtime_call_but_stays_durable(bridge_h):
    bseed(bridge_h)
    rt = FakeRuntimeTarget()
    assert bridge_h.delivery_cycle("b", "s", rt).status == "delivered"  # terminal, nothing in flight
    cancel = ctl(bridge_h, "control.cancel", "cx")
    res = bridge_h.handle_control(cancel.record_id, rt, "b")
    assert res.runtime_outcome == "nothing_running" and rt.count("terminate") == 0
    assert control_rows(bridge_h, cancel.record_id)[0][6] == "nothing_running"


@pytest.mark.catalog_id("BRG-008")
def test_brg_008_pause_record_commits_before_interrupt_and_failure_keeps_intent(bridge_h):
    bseed(bridge_h)
    rt, _ = running(bridge_h)
    pause = ctl(bridge_h, "control.pause", "p1")
    seen = []
    rt.script_effect("interrupt", lambda: seen.append(_snapshot(bridge_h, pause.record_id)))
    res = bridge_h.handle_control(pause.record_id, rt, "b")
    assert seen == [{"record": [("control.pause",)], "control": [("control.pause", None)]}]  # commit precedes the interrupt call
    assert res.runtime_outcome == "done" and res.paused is True
    # failure of interrupt does not erase intent (second pause, interrupt raises)
    pause2 = ctl(bridge_h, "control.pause", "p2")
    rt.script_effect("interrupt", RuntimeTargetError("interrupt exploded"))
    res2 = bridge_h.handle_control(pause2.record_id, rt, "b")
    assert res2.runtime_outcome == "failed" and res2.paused is True
    row = control_rows(bridge_h, pause2.record_id)[0]
    assert row[6] == "failed" and "interrupt exploded" in row[7]
    assert sql(bridge_h, "SELECT COUNT(*) FROM records WHERE kind='control.pause'") == [(2,)]


@pytest.mark.catalog_id("BRG-008")
@pytest.mark.fault
def test_brg_008_crash_between_intent_commit_and_interrupt_never_loses_intent(tmp_path):
    crash = CrashInjector()
    h = BridgeHarness(tmp_path / "ws", fault_hook=crash)
    bseed(h)
    rt, _ = running(h)
    pause = ctl(h, "control.pause", "p1")
    crash.arm("control.after_intent_commit")
    with pytest.raises(CrashInjected):
        h.handle_control(pause.record_id, rt, "b")
    assert crash.fired == ["control.after_intent_commit"] and rt.count("interrupt") == 0  # died before the effect
    assert control_rows(h, pause.record_id)[0][6] is None and sql(h, "SELECT COUNT(*) FROM records WHERE kind='control.pause'") == [(1,)]
    assert h.bridge.control_state("b", "s")["paused"] is True  # the durable intent already gates delivery
    h2 = BridgeHarness(tmp_path / "ws", h.clock)  # restart
    assert h2.handle_control(pause.record_id, rt, "b").status == "in_progress"  # crashed handler's lease still live: no double effect
    assert rt.count("interrupt") == 0
    h2.clock.advance(31)  # lease expired (TD-19 now >= expires_at)
    res = h2.handle_control(pause.record_id, rt, "b")
    assert res.status == "applied" and res.runtime_outcome == "done" and rt.count("interrupt") == 1
    assert [r[6:10:3] for r in control_rows(h2, pause.record_id)] == [("done", 2)]  # outcome once, attempts==2
    assert h2.handle_control(pause.record_id, rt, "b").status == "replayed" and rt.count("interrupt") == 1


@pytest.mark.catalog_id("BRG-008")
@pytest.mark.fault
@pytest.mark.restart
def test_brg_008_real_process_death_after_interrupt_before_outcome_replays_at_least_once(tmp_path):
    h = BridgeHarness(tmp_path / "ws")
    bseed(h)
    rt, _ = running(h)
    pause = ctl(h, "control.pause", "p1")
    ctx = mp.get_context("spawn")
    p = ctx.Process(target=control_crash_worker, args=(str(tmp_path / "ws"), pause.record_id, "b", "control.after_effect_before_outcome",
                                                       h.clock.now()), daemon=True)
    p.start()
    p.join(90)
    alive = p.is_alive()
    if alive:
        p.terminate()
    assert not alive and p.exitcode == 17  # the child really died at the crash point
    assert control_rows(h, pause.record_id)[0][6] is None  # old state: intent durable, outcome absent (complete, not torn)
    h.clock.advance(31)
    res = h.reopen().handle_control(pause.record_id, rt, "b")
    assert res.status == "applied" and rt.count("interrupt") == 1  # re-issued (interrupt is idempotent best-effort)
    assert [r[6:10:3] for r in control_rows(h, pause.record_id)] == [("done", 2)]
    assert len(control_evidence(h, pause.record_id)) == 1  # one outcome evidence row, no duplicates


# ------------------------------------------------------------------ CTL-002
@pytest.mark.catalog_id("CTL-002")
@pytest.mark.fault
def test_ctl_002_terminate_failure_leaves_cancel_durable_with_explicit_evidence(bridge_h):
    bseed(bridge_h)
    rt, _ = running(bridge_h)
    before_off = offset_row(bridge_h)
    prior = all_record_rows(bridge_h)
    cancel = ctl(bridge_h, "control.cancel", "cx")
    rt.script_effect("terminate", RuntimeTargetError("kill failed"))
    res = bridge_h.handle_control(cancel.record_id, rt, "b")  # failure is reported, not raised, not rolled back
    assert res.status == "applied" and res.runtime_outcome == "failed"
    assert all_record_rows(bridge_h)[: len(prior)] == prior and len(all_record_rows(bridge_h)) == len(prior) + 1  # history untouched
    assert offset_row(bridge_h) == before_off
    row = control_rows(bridge_h, cancel.record_id)[0]
    assert row[6] == "failed" and "kill failed" in row[7]
    (ev,) = control_evidence(bridge_h, cancel.record_id)
    assert ev[0] == "control_applied" and json.loads(ev[2])["runtime_outcome"] == "failed"
    again = bridge_h.handle_control(cancel.record_id, rt, "b")  # idempotent replay: failure is not retried behind the user's back
    assert again.status == "replayed" and again.runtime_outcome == "failed" and rt.count("terminate") == 1


@pytest.mark.catalog_id("CTL-002")
@pytest.mark.fault
def test_ctl_002_unexpected_effect_error_propagates_and_replay_after_lease_completes_once(bridge_h):
    bseed(bridge_h)
    rt, _ = running(bridge_h)
    cancel = ctl(bridge_h, "control.cancel", "cx")
    rt.script_effect("terminate", ValueError("boom"))
    with pytest.raises(ValueError, match="boom"):  # a bug is not swallowed as a runtime failure
        bridge_h.handle_control(cancel.record_id, rt, "b")
    assert control_rows(bridge_h, cancel.record_id)[0][6:10:3] == (None, 1)  # intent durable, no outcome, attempt counted
    assert bridge_h.handle_control(cancel.record_id, rt, "b").status == "in_progress" and rt.count("terminate") == 1
    bridge_h.clock.advance(31)
    res = bridge_h.handle_control(cancel.record_id, rt, "b")
    assert res.status == "applied" and res.runtime_outcome == "done" and rt.count("terminate") == 2
    assert control_rows(bridge_h, cancel.record_id)[0][6:10:3] == ("done", 2)


# ------------------------------------------------------------------ CTL-003
@pytest.mark.catalog_id("CTL-003")
def test_ctl_003_pause_cancel_resume_do_not_roll_back_records_or_offset(bridge_h):
    bseed(bridge_h, n=3)
    rt = FakeRuntimeTarget()
    for _ in range(3):
        assert bridge_h.delivery_cycle("b", "s", rt).status == "delivered"
    prior, off = all_record_rows(bridge_h), offset_row(bridge_h)
    assert off[0] == 3 and len(prior) == 6
    kinds_applied = []
    for kind, key in (("control.pause", "p"), ("control.cancel", "c"), ("control.resume", "r")):
        c = ctl(bridge_h, kind, key)
        bridge_h.handle_control(c.record_id, rt, "b")
        kinds_applied.append(kind)
        assert all_record_rows(bridge_h)[:6] == prior  # every earlier Record byte-identical
        assert offset_row(bridge_h) == off  # Offset position AND revision unchanged by control handling
    assert [r[3] for r in records(bridge_h)][6:] == kinds_applied  # controls are additional Records, in order
    assert bridge_h.bridge.control_state("b", "s")["paused"] is False  # resume lifted the pause
    after = msg(bridge_h, "after")  # control: delivery works again and the Offset only moves forward
    res = bridge_h.delivery_cycle("b", "s", rt)
    assert res.status == "delivered" and res.record_id == after.record_id  # the 3 controls were consumed, not blocking
    for _ in range(4):
        if bridge_h.delivery_cycle("b", "s", rt).status == "idle":
            break
    assert offset_row(bridge_h)[0] == max(r[1] for r in records(bridge_h)) and offset_row(bridge_h)[1] > off[1]
    assert all_record_rows(bridge_h)[:6] == prior


# ------------------------------------------------------------------ CTL-004 / CTL-005
def _gseed(h, goal="g1"):
    for p in ("a", "b"):
        h.create_peer({"peer_id": p})
    h.create_stream({"stream_id": "s", "members": ["a", "b"], "metadata": {"goal": goal}})
    return [msg(h, f"h{i}") for i in range(2)]


@pytest.mark.catalog_id("CTL-004")
@pytest.mark.parametrize("cur,new,expected", [("g1", "g1", "REDIRECT"), ("g1", "g2", "NEW_STREAM"), ("g1", "G1", "NEW_STREAM"),
                                              ("", "g1", None), (None, "g1", None), ("g1", "", None)])
def test_ctl_004_decision_helper_literal_table(cur, new, expected):
    if expected is None:
        with pytest.raises(DirectionError):
            decide_direction(cur, new)
    else:
        assert decide_direction(cur, new) == expected


@pytest.mark.catalog_id("CTL-004")
def test_ctl_004_same_goal_redirect_is_another_record_in_the_same_stream(bridge_h):
    hist = _gseed(bridge_h)
    prior = all_record_rows(bridge_h)
    streams_before = sql(bridge_h, "SELECT stream_id, state, revision FROM streams")
    out = apply_direction(bridge_h.store, "s", author_peer_id="a", proposed_goal="g1", instruction="use approach B",
                          idempotency_key="rd1", created_at="2026-10-01T00:00:00Z")
    assert out.decision == "REDIRECT" and out.stream_id == "s" and out.prior_stream_id is None
    rows = all_record_rows(bridge_h)
    assert rows[:2] == prior and len(rows) == 3  # old history intact and addressable
    assert sql(bridge_h, "SELECT kind FROM records WHERE record_id=?", (out.record.record_id,)) == [("control.redirect",)]
    assert json.loads(sql(bridge_h, "SELECT body_json FROM records WHERE record_id=?", (out.record.record_id,))[0][0]) == {
        "goal": "g1", "instruction": "use approach B"}
    assert sql(bridge_h, "SELECT stream_id, state, revision FROM streams") == streams_before  # no new Stream, no state change
    assert [r.record_id for r in bridge_h.read_records("s")][:2] == [h.record_id for h in hist]
    again = apply_direction(bridge_h.store, "s", author_peer_id="a", proposed_goal="g1", instruction="use approach B",
                            idempotency_key="rd1", created_at="2026-10-01T00:00:00Z")
    assert again.record.record_id == out.record.record_id and len(all_record_rows(bridge_h)) == 3  # idempotent retry


@pytest.mark.catalog_id("CTL-004")
def test_ctl_004_redirect_reaches_running_session_via_steer_and_unsupported_is_explicit(bridge_h):
    _gseed(bridge_h)
    rt, _ = running(bridge_h)
    out = apply_direction(bridge_h.store, "s", author_peer_id="a", proposed_goal="g1", instruction="B", idempotency_key="rd1",
                          created_at="2026-10-01T00:00:00Z")
    res = bridge_h.handle_control(out.record.record_id, rt, "b")
    assert res.effect == "steer" and res.runtime_outcome == "done" and [c for c in rt.calls if c[0] == "steer"] == [
        ("steer", "ext-1", out.record.record_id)]
    out2 = apply_direction(bridge_h.store, "s", author_peer_id="a", proposed_goal="g1", instruction="C", idempotency_key="rd2",
                           created_at="2026-10-01T00:00:00Z")
    rt.capabilities(steer=False)
    res2 = bridge_h.handle_control(out2.record.record_id, rt, "b")
    assert res2.runtime_outcome == "unsupported" and rt.count("steer") == 1  # durable intent, explicit non-success


@pytest.mark.catalog_id("CTL-004")
def test_ctl_004_redirect_history_survives_session_loss_in_catch_up(bridge_h):
    _gseed(bridge_h)
    rt = FakeRuntimeTarget()
    for _ in range(2):
        assert bridge_h.delivery_cycle("b", "s", rt).status == "delivered"
    out = apply_direction(bridge_h.store, "s", author_peer_id="a", proposed_goal="g1", instruction="B", idempotency_key="rd1",
                          created_at="2026-10-01T00:00:00Z")
    nxt = msg(bridge_h, "next")
    rt.lose_context()
    assert bridge_h.delivery_cycle("b", "s", rt).status == "delivered"
    order = [r[0] for r in records(bridge_h) if r[1] < [x[1] for x in records(bridge_h) if x[0] == nxt.record_id][0]]
    assert out.record.record_id in rt.catch_ups[-1] and rt.catch_ups[-1] == [x for x in order if x in rt.catch_ups[-1]]  # ordered


@pytest.mark.catalog_id("CTL-004")
def test_ctl_004_unseen_redirect_is_handed_once_to_a_resumed_session(bridge_h):
    _gseed(bridge_h)
    rt = FakeRuntimeTarget()
    for _ in range(2):
        assert bridge_h.delivery_cycle("b", "s", rt).status == "delivered"
    out = apply_direction(bridge_h.store, "s", author_peer_id="a", proposed_goal="g1", instruction="B", idempotency_key="rd1",
                          created_at="2026-10-01T00:00:00Z")
    msg(bridge_h, "n1")
    msg(bridge_h, "n2")
    assert bridge_h.delivery_cycle("b", "s", rt).status == "delivered"  # session resumed (no loss)
    assert rt.catch_ups[-1] == [out.record.record_id]  # the resumed session never saw the redirect: handed over exactly once
    assert bridge_h.delivery_cycle("b", "s", rt).status == "delivered" and rt.catch_ups[-1] == []


@pytest.mark.catalog_id("CTL-005")
def test_ctl_005_new_goal_opens_a_new_stream_referencing_the_prior_one(bridge_h):
    _gseed(bridge_h)
    prior = all_record_rows(bridge_h)
    out = apply_direction(bridge_h.store, "s", author_peer_id="a", proposed_goal="g2", instruction="start over on g2",
                          idempotency_key="nd1", created_at="2026-10-01T00:00:00Z", new_stream_id="s2")
    assert out.decision == "NEW_STREAM" and out.stream_id == "s2" and out.prior_stream_id == "s"
    assert all_record_rows(bridge_h) == prior  # old Stream's history preserved byte-for-byte
    old, new = (sql(bridge_h, "SELECT stream_id, state, revision, metadata_json FROM streams WHERE stream_id=?", (x,))[0] for x in ("s", "s2"))
    assert old[1] == "CLOSED" and old[2] == 2 and json.loads(old[3]) == {"goal": "g1", "superseded_by": "s2"}  # closed per default policy
    assert new[1] == "OPEN" and json.loads(new[3]) == {"goal": "g2", "prior_stream_id": "s"}  # explicit prior reference
    assert bridge_h.get_stream("s2").members == ["a", "b"] and bridge_h.get_stream("s").members == ["a", "b"]
    first = sql(bridge_h, "SELECT kind, author_peer_id, body_json, position FROM records WHERE stream_id='s2'")
    assert [(f[0], f[1], f[3]) for f in first] == [("message", "a", 1)] and json.loads(first[0][2]) == "start over on g2"
    with pytest.raises(StreamClosedError):
        msg(bridge_h, "late-on-old")
    assert all_record_rows(bridge_h) == prior
    # the bridge continues in the new Stream: normal delivery, no history leakage from the old one
    rt = FakeRuntimeTarget()
    res = bridge_h.delivery_cycle("b", "s2", rt)
    assert res.status == "delivered" and rt.catch_ups[-1] == []


@pytest.mark.catalog_id("CTL-005")
def test_ctl_005_close_policy_replay_and_conflicts(bridge_h):
    _gseed(bridge_h)
    kw = dict(author_peer_id="a", proposed_goal="g2", instruction="go", idempotency_key="nd1", created_at="2026-10-01T00:00:00Z")
    out = apply_direction(bridge_h.store, "s", new_stream_id="s2", close_prior=False, **kw)
    assert sql(bridge_h, "SELECT state, revision FROM streams WHERE stream_id='s'") == [("OPEN", 1)]  # policy: keep old Stream open
    msg(bridge_h, "still-works")  # control: old Stream still appendable
    bridge_h.create_stream({"stream_id": "other", "members": ["a", "b"], "metadata": {"goal": "g9"}})
    bridge_h.create_stream({"stream_id": "nogoal", "members": ["a", "b"]})

    def snap():
        return (sql(bridge_h, "SELECT * FROM streams ORDER BY 1"), sql(bridge_h, "SELECT record_id FROM records ORDER BY 1"))

    before = snap()
    again = apply_direction(bridge_h.store, "s", new_stream_id="s2", close_prior=False, **kw)  # replay
    assert again.record.record_id == out.record.record_id and snap() == before
    for stream, extra, match in (("other", {"new_stream_id": "s2", "proposed_goal": "g3"}, "s2"),  # same new id, different prior
                                 ("s", {}, "new_stream_id"),  # a new goal needs an explicit new stream id
                                 ("nogoal", {"new_stream_id": "zz"}, "goal")):  # no goal metadata: cannot decide
        with pytest.raises(DirectionError, match=match):
            apply_direction(bridge_h.store, stream, close_prior=False, **{**kw, "idempotency_key": "nd-x", **extra})
        assert snap() == before


# ------------------------------------------------------------------ BRG-009
@pytest.mark.catalog_id("BRG-009")
@pytest.mark.parametrize("kind,effect,cap", [("control.pause", "interrupt", "interrupt"), ("control.cancel", "terminate", "terminate")])
def test_brg_009_unsupported_interrupt_is_durable_intent_with_explicit_evidence(bridge_h, kind, effect, cap):
    bseed(bridge_h)
    rt, _ = running(bridge_h)
    rt.capabilities(**{cap: False})
    c = ctl(bridge_h, kind, "c1")
    res = bridge_h.handle_control(c.record_id, rt, "b")
    assert res.runtime_outcome == "unsupported" and res.runtime_outcome != "done" and res.effect == effect
    assert rt.count(effect) == 0  # never invoked an unsupported capability
    assert sql(bridge_h, "SELECT kind FROM records WHERE record_id=?", (c.record_id,)) == [(kind,)]
    assert control_rows(bridge_h, c.record_id)[0][6] == "unsupported"
    (ev,) = control_evidence(bridge_h, c.record_id)
    assert ev[0] == "control_applied" and json.loads(ev[2])["runtime_outcome"] == "unsupported"
    if kind == "control.pause":
        assert bridge_h.delivery_cycle("b", "s", rt).status == "paused"  # intent is honoured bridge-side despite the missing capability
    # positive control: the same Record on a capable runtime reports done
    other = BridgeHarness(bridge_h.workspace.parent / "ws-ok")
    bseed(other)
    rt2, _ = running(other)
    c2 = ctl(other, kind, "c1")
    assert other.handle_control(c2.record_id, rt2, "b").runtime_outcome == "done" and rt2.count(effect) == 1


@pytest.mark.catalog_id("BRG-009")
def test_brg_009_no_session_is_distinct_from_unsupported(bridge_h):
    bseed(bridge_h)
    rt = FakeRuntimeTarget().capabilities(interrupt=False)
    c = ctl(bridge_h, "control.pause", "p1")
    res = bridge_h.handle_control(c.record_id, rt, "b")  # nothing was ever started
    assert res.runtime_outcome == "nothing_running" and res.paused is True and rt.calls == []
