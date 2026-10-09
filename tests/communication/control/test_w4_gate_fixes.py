"""Wave 4 gate fixes (cx.pro BLOCK on d1945ca, rulings D-W4-1..10): fencing of control effects, gating up to the invoke,
stranded controls, redirect scope/budget, boundary Record validation, direction atomicity, reconcile/cancel precedence."""
import json

import pytest

from peerhub.extensions.bridge import BoundaryConflictError, SessionError
from peerhub.extensions.catchup import CatchUpBudget, build_catch_up
from peerhub.extensions.direction import DirectionError, apply_direction
from peerhub.core.store import CasMismatchError
from tests.communication.control_helpers import (all_record_rows, bseed, control_evidence, control_rows, ctl, delivery_rows, kinds, msg, offset_row,
                                      records, responses, running, sql)
from tests.communication.fakes import CrashInjected, CrashInjector, FakeRuntimeTarget
from tests.communication.harness.bridge import BridgeHarness
from tests.communication.helpers import req

pytestmark = [pytest.mark.control, pytest.mark.integration]


# ------------------------------------------------------------------ 1. control fencing (D-W4-1)
@pytest.mark.catalog_id("BRG-008")
def test_brg_008_stale_delivery_token_cannot_interrupt_the_runtime(tmp_path):
    armed = []

    def hook(p):
        if p == "claim.after_acquire" and armed:
            armed.clear()
            h.clock.advance(31)
            h.cs.acquire("b", "s", "other", 30)  # takeover: this cycle's token is now stale

    h = BridgeHarness(tmp_path / "ws", fault_hook=hook)
    bseed(h)
    rt, _ = running(h)
    pause = ctl(h, "control.pause", "p1")
    armed.append(1)
    res = h.delivery_cycle("b", "s", rt)
    assert res.status == "paused" and rt.count("interrupt") == 0  # the stale cycle never touched the runtime
    assert control_rows(h, pause.record_id)[0][6] == None and control_evidence(h, pause.record_id) == []  # noqa: E711 - SQL NULL
    h.clock.advance(31)  # recovery by the CURRENT owner completes the intent exactly once
    assert h.new_bridge("other").delivery_cycle("b", "s", rt).status == "paused"
    # the takeover superseded the delivery the pause targeted: the old session is NOT interrupted (D-W4-1); gating still honours the pause
    assert rt.count("interrupt") == 0 and control_rows(h, pause.record_id)[0][6] == "stale_target"


@pytest.mark.catalog_id("BRG-008")
def test_brg_008_expired_lease_cannot_commit_done(bridge_h):
    bseed(bridge_h)
    rt, _ = running(bridge_h)
    pause = ctl(bridge_h, "control.pause", "p1")
    rt.script_effect("interrupt", lambda: bridge_h.clock.advance(31))  # the lease lapses while the effect runs
    a = bridge_h.handle_control(pause.record_id, rt, "b")
    assert a.status == "superseded" and control_rows(bridge_h, pause.record_id)[0][6] is None and control_evidence(bridge_h, pause.record_id) == []
    b = bridge_h.handle_control(pause.record_id, rt, "b")  # control: a fresh lease completes it
    assert b.status == "applied" and control_rows(bridge_h, pause.record_id)[0][6:10:3] == ("done", 2)


@pytest.mark.catalog_id("BRG-008")
def test_brg_008_superseded_handler_does_not_execute_its_effect(tmp_path):
    fired = []

    def hook(p):
        if p == "control.after_intent_commit" and not fired:
            fired.append(p)
            h.clock.advance(31)  # A's lease lapses before A reaches its effect
            fired.append(nb.handle_control(pause.record_id, rt, "b"))

    h = BridgeHarness(tmp_path / "ws", fault_hook=hook)
    bseed(h)
    rt, _ = running(h)
    pause = ctl(h, "control.pause", "p1")
    nb = h.new_bridge("B")
    a = h.handle_control(pause.record_id, rt, "b")
    assert fired[1].status == "applied" and a.status == "superseded"
    assert rt.count("interrupt") == 1  # only B executed; A was fenced BEFORE its effect
    assert control_rows(h, pause.record_id)[0][6:10:3] == ("done", 2)


@pytest.mark.catalog_id("CTL-004")
def test_ctl_004_crash_recovery_never_retargets_a_redirect_to_a_newer_delivery(tmp_path):
    crash = CrashInjector()
    h = BridgeHarness(tmp_path / "ws", fault_hook=crash)
    bseed(h)
    rt, first = running(h)
    d1 = first.delivery_id
    redirect = ctl(h, "control.redirect", "rd", body={"goal": "g", "instruction": "B"})
    crash.arm("control.after_intent_commit")
    with pytest.raises(CrashInjected):
        h.handle_control(redirect.record_id, rt, "b")
    assert sql(h, "SELECT target_delivery_id, target_session_id FROM bridge_controls") == [(d1, "ext-1")]  # target persisted BEFORE the effect
    crash.disarm()
    h.clock.advance(31)
    tok = h.acquire_claim("b", "s", "X")
    h.finalize_terminal(tok, {"response": "late"}, delivery_id=d1)  # d1 completes; a NEWER delivery starts
    msg(h, "m2")
    h.clock.advance(31)
    rt.script_deliver(("started", "exec-2"), ("output", "p"))
    assert h.delivery_cycle("b", "s", rt).delivery_id != d1
    h.clock.advance(31)
    res = h.handle_control(redirect.record_id, rt, "b")
    assert res.runtime_outcome == "stale_target" and rt.count("steer") == 0  # never retargeted to the newer delivery
    assert sql(h, "SELECT target_delivery_id FROM bridge_controls") == [(d1,)]


# ------------------------------------------------------------------ 2. gating up to the invoke (D-W4-2)
@pytest.mark.catalog_id("BRG-008")
@pytest.mark.parametrize("kind", ["control.pause", "control.cancel"])
def test_brg_008_control_committed_at_the_invoke_hook_stops_the_delivery(tmp_path, kind):
    fired = []

    def hook(p):
        if p == "bridge.before_runtime_invoke" and not fired:
            fired.append(p)
            ctl(h, kind, "late")

    h = BridgeHarness(tmp_path / "ws", fault_hook=hook)
    (m0,) = bseed(h)
    rt = FakeRuntimeTarget()
    res = h.delivery_cycle("b", "s", rt)
    assert res.status == ("paused" if kind == "control.pause" else "cancelled") and rt.count("deliver") == 0
    assert responses(h) == []
    (d,) = delivery_rows(h, m0.record_id)
    assert d[7] == "NOT_STARTED" and "control_halt" in kinds(h)  # the marker was explicitly reverted: nothing was invoked
    if kind == "control.pause":
        ctl(h, "control.resume", "r")
        assert h.delivery_cycle("b", "s", rt).status == "delivered"  # control: after resume the SAME attempt runs
    else:
        assert offset_row(h)[0] == m0.position and h.delivery_cycle("b", "s", rt).status == "idle" and rt.count("deliver") == 0


@pytest.mark.catalog_id("BRG-008")
def test_brg_008_pause_during_session_creation_retries_stops_further_attempts(tmp_path):
    fired = []

    def hook(p):
        if p == "bridge.after_create_failure" and not fired:
            fired.append(p)
            ctl(h, "control.pause", "late")

    h = BridgeHarness(tmp_path / "ws", fault_hook=hook)
    bseed(h)
    rt = FakeRuntimeTarget().script_create(SessionError("boom1"), SessionError("boom2"), "ok")
    res = h.delivery_cycle("b", "s", rt)
    assert res.status == "paused" and rt.count("create") == 1  # no second creation attempt after the durable pause
    assert rt.count("deliver") == 0


# ------------------------------------------------------------------ 3. stranded controls (D-W4-3)
@pytest.mark.catalog_id("BRG-008")
def test_brg_008_control_stranded_in_progress_is_recovered_after_lease_expiry(tmp_path):
    crash = CrashInjector()
    h = BridgeHarness(tmp_path / "ws", fault_hook=crash)
    bseed(h)
    rt = FakeRuntimeTarget()
    assert h.delivery_cycle("b", "s", rt).status == "delivered"
    cancel = ctl(h, "control.cancel", "cx")
    crash.arm("control.after_intent_commit")
    with pytest.raises(CrashInjected):
        h.handle_control(cancel.record_id, rt, "b")
    crash.disarm()
    h.delivery_cycle("b", "s", rt)  # the lease is live: the cycle sees in_progress and consumes the Record
    assert offset_row(h)[0] >= cancel.position and control_rows(h, cancel.record_id)[0][6] is None
    h.clock.advance(31)
    h.delivery_cycle("b", "s", rt)  # the unread scan alone would never revisit it
    assert control_rows(h, cancel.record_id)[0][6] == "nothing_running" and control_rows(h, cancel.record_id)[0][9] == 2


# ------------------------------------------------------------------ 4. redirect catch-up scope and budget (D-W4-4)
def _redirect(h, key, targets=None):
    extra = {"targets": targets} if targets else {}
    return ctl(h, "control.redirect", key, body={"goal": "g", "instruction": key}, **extra)


@pytest.mark.catalog_id("CTL-004")
def test_ctl_004_redirect_for_another_peer_is_not_handed_to_this_peer(bridge_h):
    bseed(bridge_h, peers=("a", "b", "c"))
    rt = FakeRuntimeTarget()
    assert bridge_h.delivery_cycle("b", "s", rt).status == "delivered"
    other = _redirect(bridge_h, "for-c", targets=["c"])
    mine = _redirect(bridge_h, "for-b", targets=["b"])
    msg(bridge_h, "n1")
    assert bridge_h.delivery_cycle("b", "s", rt).status == "delivered"
    assert rt.catch_ups[-1] == [mine.record_id] and other.record_id not in rt.catch_ups[-1]


@pytest.mark.catalog_id("CTL-004")
def test_ctl_004_redirect_handoff_honours_the_budget_and_reports_boundary(tmp_path):
    h = BridgeHarness(tmp_path / "ws", catch_up_budget=CatchUpBudget(max_records=0))
    bseed(h)
    rt = FakeRuntimeTarget()
    assert h.delivery_cycle("b", "s", rt).status == "delivered"
    _redirect(h, "rd")
    msg(h, "n1")
    assert h.delivery_cycle("b", "s", rt).status == "delivered"
    assert rt.catch_ups[-1] == []  # max_records=0 yields none
    meta = rt.catch_up_meta[-1]
    assert meta is not None and meta["truncated"] is True and meta["omitted_count"] == 1 and meta["budget"]["max_records"] == 0


# ------------------------------------------------------------------ 5. boundary Record validation (D-W4-5)
@pytest.mark.catalog_id("CTX-003")
def test_ctx_003_foreign_record_at_the_boundary_key_is_an_explicit_error(bridge_h):
    bseed(bridge_h)
    rt = FakeRuntimeTarget()
    assert bridge_h.delivery_cycle("b", "s", rt).status == "delivered"
    msg(bridge_h, "n1")
    squat = bridge_h.append_record(req(body="squatter", key="context-boundary:b:2", author="b"))  # unrelated Record at the key
    rt.lose_context()
    before = (all_record_rows(bridge_h), delivery_rows(bridge_h), offset_row(bridge_h))
    with pytest.raises(BoundaryConflictError, match="context-boundary:b:2"):
        bridge_h.delivery_cycle("b", "s", rt)
    assert [c[0] for c in rt.calls].count("deliver") == 1  # runtime never invoked for the new generation
    assert all_record_rows(bridge_h) == before[0] and squat.kind == "message"
    assert [d[7] for d in delivery_rows(bridge_h)] == ["TERMINAL", "NOT_STARTED"]  # the new attempt never reached the runtime
    assert sql(bridge_h, "SELECT COUNT(*) FROM records WHERE kind='context.boundary'") == [(0,)]


@pytest.mark.catalog_id("CTX-003")
def test_ctx_003_existing_valid_boundary_is_accepted_idempotently(tmp_path):
    crash = CrashInjector()
    h = BridgeHarness(tmp_path / "ws", fault_hook=crash)
    bseed(h)
    rt = FakeRuntimeTarget()
    h.delivery_cycle("b", "s", rt)
    msg(h, "n1")
    rt.lose_context()
    crash.arm("bridge.before_marker")
    with pytest.raises(CrashInjected):
        h.delivery_cycle("b", "s", rt)  # boundary already written, delivery not yet invoked
    assert sql(h, "SELECT COUNT(*) FROM records WHERE kind='context.boundary'") == [(1,)]
    res = BridgeHarness(tmp_path / "ws", h.clock).delivery_cycle("b", "s", rt)
    assert res.status == "delivered" and sql(h, "SELECT COUNT(*) FROM records WHERE kind='context.boundary'") == [(1,)]


# ------------------------------------------------------------------ 6. direction atomicity (D-W4-6)
class _CasBlocked:
    """Store proxy whose Stream CAS always conflicts (a competing writer)."""

    def __init__(self, store):
        self._s = store

    def __getattr__(self, n):
        return getattr(self._s, n)

    def cas_stream(self, *a, **k):
        raise CasMismatchError("competing writer")


def _kw(**o):
    return {**dict(author_peer_id="a", proposed_goal="g2", instruction="go", idempotency_key="nd1",
                   created_at="2026-10-01T00:00:00Z", new_stream_id="s2"), **o}


def _gs(h):
    for p in ("a", "b"):
        h.create_peer({"peer_id": p})
    h.create_stream({"stream_id": "s", "members": ["a", "b"], "metadata": {"goal": "g1"}})


@pytest.mark.catalog_id("CTL-005")
def test_ctl_005_close_cas_exhaustion_is_not_reported_as_success(bridge_h):
    _gs(bridge_h)
    with pytest.raises(DirectionError, match="close"):
        apply_direction(_CasBlocked(bridge_h.store), "s", **_kw())
    assert sql(bridge_h, "SELECT state FROM streams WHERE stream_id='s'") == [("OPEN",)]
    assert sql(bridge_h, "SELECT COUNT(*) FROM records WHERE stream_id='s2'") == [(1,)]  # consistent partial state, idempotent to finish
    out = apply_direction(bridge_h.store, "s", **_kw())  # control: the retry completes
    assert out.decision == "NEW_STREAM" and sql(bridge_h, "SELECT state FROM streams WHERE stream_id='s'") == [("CLOSED",)]
    assert sql(bridge_h, "SELECT COUNT(*) FROM records WHERE stream_id='s2'") == [(1,)]


@pytest.mark.catalog_id("CTL-005")
@pytest.mark.parametrize("bad", [{"instruction": float("nan")}, {"created_at": "yesterday"}, {"author_peer_id": "stranger"},
                                 {"idempotency_key": ""}])
def test_ctl_005_invalid_payload_leaves_no_new_stream(bridge_h, bad):
    _gs(bridge_h)
    before = (sql(bridge_h, "SELECT * FROM streams ORDER BY 1"), sql(bridge_h, "SELECT COUNT(*) FROM records"))
    with pytest.raises(DirectionError):
        apply_direction(bridge_h.store, "s", **_kw(**bad))
    assert (sql(bridge_h, "SELECT * FROM streams ORDER BY 1"), sql(bridge_h, "SELECT COUNT(*) FROM records")) == before
    assert apply_direction(bridge_h.store, "s", **_kw()).stream_id == "s2"  # control: the valid request succeeds


# ------------------------------------------------------------------ 7. precedence by Record position (D-W4-7/8/9)
def _uncertain(h, rt):
    rt.script_deliver(("timeout",))
    res = h.delivery_cycle("b", "s", rt)
    assert res.certainty == "MAY_HAVE_STARTED"
    return res.delivery_id


def _retry(h, key, did):
    rec = h.append_record({**req(body={"decision": "RETRY", "delivery_id": did}, key=key, author="a"), "kind": "control.reconcile"})
    return rec


@pytest.mark.catalog_id("CERT-002")
def test_cert_002_older_retry_never_overrides_a_newer_cancel(bridge_h):
    (m0,) = bseed(bridge_h)
    rt = FakeRuntimeTarget()
    did = _uncertain(bridge_h, rt)
    r = _retry(bridge_h, "rc", did)
    bridge_h.reconcile_uncertain(m0.record_id, r)
    ctl(bridge_h, "control.cancel", "cx")  # NEWER than the RETRY
    follow = FakeRuntimeTarget()
    res = bridge_h.delivery_cycle("b", "s", follow)
    assert res.status == "blocked_uncertain" and follow.count("deliver") == 0 and len(delivery_rows(bridge_h, m0.record_id)) == 1
    assert sql(bridge_h, "SELECT consumed_attempt FROM bridge_reconciliations") == [(None,)]  # authorization not consumed
    # control: a RETRY NEWER than the cancel wins
    h2 = BridgeHarness(bridge_h.workspace.parent / "ws2")
    (m1,) = bseed(h2)
    did2 = _uncertain(h2, FakeRuntimeTarget())
    ctl(h2, "control.cancel", "cx")
    h2.reconcile_uncertain(m1.record_id, _retry(h2, "rc", did2))
    assert h2.delivery_cycle("b", "s", FakeRuntimeTarget()).status == "delivered"


@pytest.mark.catalog_id("CERT-002")
@pytest.mark.fault
def test_cert_002_newer_retry_keeps_its_override_after_a_crash_before_session_resolution(tmp_path):
    crash = CrashInjector()
    h = BridgeHarness(tmp_path / "ws", fault_hook=crash)
    (m0,) = bseed(h)
    rt = FakeRuntimeTarget()
    did = _uncertain(h, rt)
    ctl(h, "control.cancel", "cx")
    h.reconcile_uncertain(m0.record_id, _retry(h, "rc", did))  # RETRY newer than the cancel
    crash.arm("bridge.before_session_resolve")
    with pytest.raises(CrashInjected):
        h.delivery_cycle("b", "s", rt)
    assert [d[6] for d in delivery_rows(h, m0.record_id)] == [1, 2]  # new attempt created, nothing resolved yet
    res = BridgeHarness(tmp_path / "ws", h.clock).delivery_cycle("b", "s", rt)
    assert res.status == "delivered" and res.certainty == "TERMINAL" and len(responses(h)) == 1


@pytest.mark.catalog_id("CTL-001")
@pytest.mark.parametrize("control", ["control.cancel", "control.resume"])
def test_ctl_001_cancel_does_not_skip_earlier_unread_records(tmp_path, control):
    h = BridgeHarness(tmp_path / "ws")
    pre = bseed(h, n=2)
    ctl(h, control, "c1")
    post = msg(h, "post")
    rt = FakeRuntimeTarget()
    for _ in range(6):
        if h.delivery_cycle("b", "s", rt).status == "idle":
            break
    assert [c[2] for c in rt.calls if c[0] == "deliver"] == [pre[0].record_id, pre[1].record_id, post.record_id]  # D-W4-7
    assert "cancelled_skip" not in [e[0] for e in sql(h, "SELECT kind FROM bridge_evidence")]


# ================================================================== re-gate (D-W4-8b, 2b, 5b, 9b)
def _history(h, n_msgs=3):
    bseed(h, n=1)
    rt = FakeRuntimeTarget()
    assert h.delivery_cycle("b", "s", rt).status == "delivered"
    for i in range(n_msgs - 1):
        msg(h, f"hist{i}")
        assert h.delivery_cycle("b", "s", rt).status == "delivered"
    return rt  # n_msgs messages + n_msgs responses durable


@pytest.mark.catalog_id("CTX-003")
@pytest.mark.parametrize("budget,expected,truncated", [(3, [4, 5, 6], True), (100, [1, 2, 3, 4, 5, 6], False)])
def test_ctx_003_fresh_generation_bootstraps_from_stream_history_within_budget(tmp_path, budget, expected, truncated):
    h = BridgeHarness(tmp_path / "ws", catch_up_budget=CatchUpBudget(max_records=budget))
    rt = _history(h)
    rows = records(h)
    assert [r[1] for r in rows] == [1, 2, 3, 4, 5, 6]  # 3 messages + 3 responses, all already read (Offset passed them)
    rt.lose_context()
    last = msg(h, "after-loss")
    assert h.delivery_cycle("b", "s", rt).status == "delivered"
    assert rt.catch_ups[-1] == [r[0] for r in rows if r[1] in expected]  # ordered suffix of the Stream (literal oracle)
    meta = rt.catch_up_meta[-1]
    assert meta["truncated"] is truncated and meta["omitted_count"] == 6 - len(expected) and meta["after_position"] == 0
    assert meta["before_position"] == last.position and meta["included_count"] == len(expected)
    assert meta["omitted_through_position"] == (None if not truncated else 3)
    (b,) = [r for r in records(h) if r[3] == "context.boundary"]
    assert json.loads(b[4])["catch_up"]["omitted_count"] == 6 - len(expected)  # boundary Record persists the same metadata


@pytest.mark.catalog_id("BRG-008")
def test_brg_008_pause_during_a_failed_resume_stops_every_create_session(tmp_path):
    fired = []

    def hook(p):
        if p == "bridge.after_resume_failure" and not fired:
            fired.append(p)
            ctl(h, "control.pause", "late")

    h = BridgeHarness(tmp_path / "ws", fault_hook=hook)
    bseed(h)
    rt = FakeRuntimeTarget()
    assert h.delivery_cycle("b", "s", rt).status == "delivered"
    msg(h, "n1")
    rt.lose_context()  # resume will report "missing"
    res = h.delivery_cycle("b", "s", rt)
    assert fired and res.status == "paused" and rt.count("create") == 1  # no creation attempt at all after the durable pause
    ctl(h, "control.resume", "r")  # control: after resume the fresh generation is created
    assert h.delivery_cycle("b", "s", rt).status == "delivered" and rt.count("create") == 2


_VALID_CU = {"after_position": 0, "before_position": 3, "candidates": 2, "included_count": 2, "first_position": 1, "last_position": 2,
             "omitted_count": 0, "omitted_through_position": None, "truncated": False, "used_bytes": 10, "used_tokens": 0,
             "budget": {"max_records": 100, "max_bytes": 24_000, "max_tokens": None}}


@pytest.mark.catalog_id("CTX-003")
@pytest.mark.parametrize("body", [
    {"peer_id": "b", "session_generation": 2, "reason": "forged-reason", "catch_up": _VALID_CU},  # everything valid except the reason
    {"peer_id": "b", "session_generation": 2, "reason": "missing", "catch_up": {**_VALID_CU, "truncated": True}},  # flag vs counts
    {"peer_id": "b", "session_generation": 2, "reason": "missing", "catch_up": {**_VALID_CU, "candidates": 9}},  # counts do not add up
    {"peer_id": "b", "session_generation": 2, "reason": "missing", "catch_up": {**_VALID_CU, "budget": {"max_records": 1}}},
    {"peer_id": "b", "session_generation": 2, "reason": "missing", "catch_up": _VALID_CU, "extra": 1},
    {"peer_id": "b", "session_generation": 2, "reason": "forged-reason", "catch_up": None},
    {"peer_id": "b", "session_generation": 2, "reason": "missing", "catch_up": {"truncated": "yes"}},
    {"peer_id": "b", "session_generation": 2, "reason": "missing", "catch_up": "garbage"},
])
def test_ctx_003_forged_boundary_payload_with_matching_peer_and_generation_is_rejected(bridge_h, body):
    bseed(bridge_h)
    rt = FakeRuntimeTarget()
    assert bridge_h.delivery_cycle("b", "s", rt).status == "delivered"
    msg(bridge_h, "n1")
    bridge_h.append_record({**req(body=body, key="context-boundary:b:2", author="b"), "kind": "context.boundary"})
    rt.lose_context()
    with pytest.raises(BoundaryConflictError, match="context-boundary:b:2"):
        bridge_h.delivery_cycle("b", "s", rt)
    assert [c[0] for c in rt.calls].count("deliver") == 1  # the new generation never reached the runtime


@pytest.mark.catalog_id("CERT-002")
def test_cert_002_latest_authorized_decision_by_position_wins(tmp_path):
    crash = CrashInjector()
    h = BridgeHarness(tmp_path / "ws", fault_hook=crash)
    (m0,) = bseed(h)
    did = _uncertain(h, FakeRuntimeTarget())
    h.reconcile_uncertain(m0.record_id, _retry(h, "rc1", did))
    ctl(h, "control.cancel", "cx")
    blocked = FakeRuntimeTarget()
    assert h.delivery_cycle("b", "s", blocked).status == "blocked_uncertain" and blocked.count("deliver") == 0  # RETRY1 < cancel
    r2 = _retry(h, "rc2", did)
    h.reconcile_uncertain(m0.record_id, r2)  # RETRY2 is newer than the cancel
    assert sql(h, "SELECT reconcile_record_id FROM bridge_reconciliations") == [(r2.record_id,)]  # no retention of RETRY1
    crash.arm("bridge.before_session_resolve")
    with pytest.raises(CrashInjected):
        h.delivery_cycle("b", "s", blocked)
    res = BridgeHarness(tmp_path / "ws", h.clock).delivery_cycle("b", "s", FakeRuntimeTarget())  # after restart: follows RETRY2
    assert res.status == "delivered" and res.certainty == "TERMINAL"


@pytest.mark.catalog_id("CERT-002")
def test_cert_002_replaying_an_older_retry_never_replaces_a_newer_authorization(bridge_h):
    (m0,) = bseed(bridge_h)
    did = _uncertain(bridge_h, FakeRuntimeTarget())
    r1, r2 = _retry(bridge_h, "rc1", did), _retry(bridge_h, "rc2", did)
    bridge_h.reconcile_uncertain(m0.record_id, r2)
    bridge_h.reconcile_uncertain(m0.record_id, r1)  # older Record registered late: ignored
    assert sql(bridge_h, "SELECT reconcile_record_id FROM bridge_reconciliations") == [(r2.record_id,)]


@pytest.mark.catalog_id("CERT-001")
def test_cert_001_authorization_can_only_move_to_a_newer_reconcile_record_never_consumed(bridge_h):
    import sqlite3

    (m0,) = bseed(bridge_h)
    did = _uncertain(bridge_h, FakeRuntimeTarget())
    r1, r2 = _retry(bridge_h, "rc1", did), _retry(bridge_h, "rc2", did)
    plain = msg(bridge_h, "plain")
    bridge_h.reconcile_uncertain(m0.record_id, r2)
    before = sql(bridge_h, "SELECT * FROM bridge_reconciliations")
    for target in (r1.record_id, plain.record_id, "rec-nope"):  # older / not a reconcile Record / nonexistent
        with pytest.raises(sqlite3.IntegrityError):
            with bridge_h.cs._tx() as c:
                c.execute("UPDATE bridge_reconciliations SET reconcile_record_id=?", (target,))
        assert sql(bridge_h, "SELECT * FROM bridge_reconciliations") == before
    r3 = _retry(bridge_h, "rc3", did)
    with bridge_h.cs._tx() as c:  # control: a NEWER reconcile Record is a legal move while unconsumed
        c.execute("UPDATE bridge_reconciliations SET reconcile_record_id=?", (r3.record_id,))
    with bridge_h.cs._tx() as c:
        c.execute("UPDATE bridge_reconciliations SET consumed_attempt=2")
    r4 = _retry(bridge_h, "rc4", did)
    with pytest.raises(sqlite3.IntegrityError):  # consumed: frozen
        with bridge_h.cs._tx() as c:
            c.execute("UPDATE bridge_reconciliations SET reconcile_record_id=?", (r4.record_id,))


# ================================================================== re-gate 3 (boundary vs real projection, authorization trigger, stale triggers on reopen)
def _scripted_timeout():
    rt = FakeRuntimeTarget()
    rt.script_deliver(("timeout",))
    return rt


def _loss_scenario(h, rt):
    """History: 3 messages + 3 responses (positions 1..6), then a new message; returns the message that will be delivered on gen 2."""
    bseed(h, n=1)
    assert h.delivery_cycle("b", "s", rt).status == "delivered"
    for i in range(2):
        msg(h, f"hist{i}")
        assert h.delivery_cycle("b", "s", rt).status == "delivered"
    nxt = msg(h, "after-loss")
    rt.lose_context()
    return nxt


@pytest.mark.catalog_id("CTX-003")
@pytest.mark.parametrize("lie", [
    {"candidates": 0, "included_count": 0, "first_position": None, "last_position": None, "used_bytes": 0},  # claims zero history
    {"candidates": 6, "included_count": 6, "omitted_count": 0, "first_position": 2, "last_position": 6},  # wrong positions
    {"before_position": 4, "candidates": 3, "included_count": 3, "first_position": 1, "last_position": 3},  # wrong delivered position
    {"used_bytes": 1},
])
def test_ctx_003_boundary_metadata_must_match_the_real_projection(bridge_h, lie):
    rt = FakeRuntimeTarget()
    nxt = _loss_scenario(bridge_h, rt)
    real = {"after_position": 0, "before_position": nxt.position, "candidates": 6, "included_count": 6, "first_position": 1,
            "last_position": 6, "omitted_count": 0, "omitted_through_position": None, "truncated": False, "used_bytes": 0,
            "used_tokens": 0, "budget": {"max_records": 100, "max_bytes": 24_000, "max_tokens": None}}
    forged = {**real, **lie}
    bridge_h.append_record({**req(body={"peer_id": "b", "session_generation": 2, "reason": "missing", "catch_up": forged},
                                  key="context-boundary:b:2", author="b"), "kind": "context.boundary"})
    with pytest.raises(BoundaryConflictError, match="context-boundary:b:2"):
        bridge_h.delivery_cycle("b", "s", rt)
    assert [c[0] for c in rt.calls].count("deliver") == 3  # the new generation never reached the runtime


@pytest.mark.catalog_id("CERT-001")
def test_cert_001_authorization_move_and_insert_require_a_valid_retry_for_the_same_delivery(bridge_h):
    import sqlite3

    (m0,) = bseed(bridge_h)
    bridge_h.create_stream({"stream_id": "s2", "members": ["a", "b"]})
    did = _uncertain(bridge_h, FakeRuntimeTarget())
    r1 = _retry(bridge_h, "rc1", did)
    bridge_h.reconcile_uncertain(m0.record_id, r1)

    def rec(key, body, author="a", stream="s"):
        return bridge_h.append_record({**req(body=body, key=key, author=author, stream=stream), "kind": "control.reconcile"}).record_id

    bad = {
        "self-authored RETRY": rec("k1", {"decision": "RETRY", "delivery_id": did}, author="b"),
        "ACCEPT decision": rec("k2", {"decision": "ACCEPT", "delivery_id": did}),
        "wrong delivery": rec("k3", {"decision": "RETRY", "delivery_id": "s:b:rec-other:1"}),
        "other stream": rec("k4", {"decision": "RETRY", "delivery_id": did}, stream="s2"),
        "no delivery_id": rec("k5", {"decision": "RETRY"}),
        "non-object body": rec("k6", "RETRY"),
    }
    before = sql(bridge_h, "SELECT * FROM bridge_reconciliations")
    for name, rid in bad.items():
        with pytest.raises(sqlite3.IntegrityError):
            with bridge_h.cs._tx() as c:
                c.execute("UPDATE bridge_reconciliations SET reconcile_record_id=?", (rid,))
        assert sql(bridge_h, "SELECT * FROM bridge_reconciliations") == before, name
    with pytest.raises(sqlite3.IntegrityError):  # a raw INSERT of an authorization is held to the same rule
        with bridge_h.cs._tx() as c:
            c.execute("INSERT INTO bridge_reconciliations (delivery_id, reconcile_record_id, decision) VALUES ('s:b:rec-other:1', ?, 'RETRY')",
                      (bad["self-authored RETRY"],))
    assert sql(bridge_h, "SELECT * FROM bridge_reconciliations") == before
    msg(bridge_h, "m2s2", stream="s2")  # a second uncertain delivery (stream s2) without any authorization row
    d2 = BridgeHarness.delivery_cycle(bridge_h, "b", "s2", _scripted_timeout()).delivery_id
    cross = rec("k8", {"decision": "RETRY", "delivery_id": d2})  # a perfect RETRY for d2 but living in stream s
    with pytest.raises(sqlite3.IntegrityError):
        with bridge_h.cs._tx() as c:
            c.execute("INSERT INTO bridge_reconciliations (delivery_id, reconcile_record_id, decision) VALUES (?, ?, 'RETRY')", (d2, cross))
    assert sql(bridge_h, "SELECT * FROM bridge_reconciliations") == before
    good = rec("k7", {"decision": "RETRY", "delivery_id": did})  # control: a valid newer RETRY is a legal move
    with bridge_h.cs._tx() as c:
        c.execute("UPDATE bridge_reconciliations SET reconcile_record_id=?", (good,))
    assert sql(bridge_h, "SELECT reconcile_record_id FROM bridge_reconciliations") == [(good,)]


@pytest.mark.catalog_id("CERT-002")
def test_cert_002_reopening_a_db_with_the_previous_trigger_definitions_installs_the_current_ones(tmp_path):
    h = BridgeHarness(tmp_path / "ws")
    (m0,) = bseed(h)
    did = _uncertain(h, FakeRuntimeTarget())
    with h.cs._tx() as c:  # simulate a DB created by the previous schema: strict immutable trigger, no move trigger
        c.execute("DROP TRIGGER bridge_reconciliations_immutable")
        c.execute("DROP TRIGGER bridge_reconciliations_consumed_immutable")
        c.execute("CREATE TRIGGER bridge_reconciliations_immutable BEFORE UPDATE OF delivery_id, reconcile_record_id, decision "
                  "ON bridge_reconciliations BEGIN SELECT RAISE(ABORT, 'authorization is immutable'); END")
    h2 = BridgeHarness(tmp_path / "ws", h.clock)  # reopen: current definitions must replace the stale ones
    h2.reconcile_uncertain(m0.record_id, _retry(h2, "rc1", did))
    ctl(h2, "control.cancel", "cx")
    assert h2.delivery_cycle("b", "s", FakeRuntimeTarget()).status == "blocked_uncertain"
    r2 = _retry(h2, "rc2", did)
    h2.reconcile_uncertain(m0.record_id, r2)  # RETRY1 -> cancel -> RETRY2 on a reopened DB
    assert sql(h2, "SELECT reconcile_record_id FROM bridge_reconciliations") == [(r2.record_id,)]
    assert h2.delivery_cycle("b", "s", FakeRuntimeTarget()).status == "delivered"


@pytest.mark.catalog_id("CERT-001")
def test_cert_001_reopen_restores_every_extension_trigger_including_claim_tables(tmp_path):
    import sqlite3

    h = BridgeHarness(tmp_path / "ws")
    bseed(h)
    q = "SELECT name FROM sqlite_master WHERE type='trigger' AND name LIKE 'bridge_%' ORDER BY 1"
    names = [r[0] for r in sql(h, q)]
    assert "bridge_finalizations_no_update" in names and "bridge_evidence_no_update" in names
    with h.cs._tx() as c:  # tamper: neutralise one trigger of each extension module (as a stale/weakened schema would)
        for n, t in (("bridge_finalizations_no_update", "bridge_finalizations"), ("bridge_evidence_no_update", "bridge_evidence")):
            c.execute(f"DROP TRIGGER {n}")
            c.execute(f"CREATE TRIGGER {n} BEFORE UPDATE ON {t} WHEN 0 BEGIN SELECT 1; END")
    h2 = BridgeHarness(tmp_path / "ws", h.clock)
    assert [r[0] for r in sql(h2, q)] == names
    tok = h2.acquire_claim("b", "s", "X", 1000)
    h2.cs.finalize_terminal(tok, {"x": 1})
    with pytest.raises(sqlite3.IntegrityError):  # the reinstalled definition is the strict one again
        with h2.cs._tx() as c:
            c.execute("UPDATE bridge_finalizations SET result_json='{}'")


@pytest.mark.catalog_id("CTX-003")
def test_ctx_003_boundary_claiming_a_delivered_position_that_does_not_exist_is_rejected(bridge_h):
    rt = FakeRuntimeTarget()
    _loss_scenario(bridge_h, rt)
    consistent = build_catch_up(bridge_h.store, "s", CatchUpBudget(max_records=100, max_bytes=24_000), after_position=0, before_position=99).boundary()
    bridge_h.append_record({**req(body={"peer_id": "b", "session_generation": 2, "reason": "missing", "catch_up": consistent},
                                  key="context-boundary:b:2", author="b"), "kind": "context.boundary"})  # self-consistent but no Record 99
    with pytest.raises(BoundaryConflictError, match="context-boundary:b:2"):
        bridge_h.delivery_cycle("b", "s", rt)


# ================================================================== re-gate 4 (ag.pro claims 1-4; claim 5 is covered by test_brg_008_control_stranded_in_progress_is_recovered_after_lease_expiry)
@pytest.mark.catalog_id("CERT-001")
def test_cert_001_consumed_attempt_is_write_once(bridge_h):
    import sqlite3

    (m0,) = bseed(bridge_h)
    did = _uncertain(bridge_h, FakeRuntimeTarget())
    bridge_h.reconcile_uncertain(m0.record_id, _retry(bridge_h, "rc1", did))
    assert sql(bridge_h, "SELECT consumed_attempt FROM bridge_reconciliations") == [(None,)]
    assert bridge_h.delivery_cycle("b", "s", FakeRuntimeTarget()).status == "delivered"  # control: NULL -> 2 is the one legal write
    assert sql(bridge_h, "SELECT consumed_attempt FROM bridge_reconciliations") == [(2,)]
    for q in ("UPDATE bridge_reconciliations SET consumed_attempt = NULL", "UPDATE bridge_reconciliations SET consumed_attempt = 7"):
        with pytest.raises(sqlite3.IntegrityError):
            with bridge_h.cs._tx() as c:
                c.execute(q)
        assert sql(bridge_h, "SELECT consumed_attempt FROM bridge_reconciliations") == [(2,)], q
    # the retry cannot be re-authorized: no further attempt is created
    again = FakeRuntimeTarget()
    assert bridge_h.delivery_cycle("b", "s", again).status == "idle" and again.count("deliver") == 0


@pytest.mark.catalog_id("CTX-003")
def test_ctx_003_boundary_accurate_for_another_prefix_but_contradicting_the_delivered_position_is_rejected(bridge_h):
    rt = FakeRuntimeTarget()
    nxt = _loss_scenario(bridge_h, rt)
    prefix = build_catch_up(bridge_h.store, "s", CatchUpBudget(max_records=100, max_bytes=24_000), after_position=0, before_position=5).boundary()
    assert prefix["before_position"] == 5 != nxt.position  # describes SOME prefix accurately
    bridge_h.append_record({**req(body={"peer_id": "b", "session_generation": 2, "reason": "missing", "catch_up": prefix},
                                  key="context-boundary:b:2", author="b"), "kind": "context.boundary"})
    with pytest.raises(BoundaryConflictError, match="context-boundary:b:2"):
        bridge_h.delivery_cycle("b", "s", rt)
    assert [c[0] for c in rt.calls].count("deliver") == 3


@pytest.mark.catalog_id("CERT-002")
def test_cert_002_reopen_drops_legacy_triggers_that_no_longer_exist_in_the_source(tmp_path):
    h = BridgeHarness(tmp_path / "ws")
    bseed(h)
    with h.cs._tx() as c:  # triggers of a previous schema version, absent from the current DDL
        c.execute("CREATE TRIGGER bridge_legacy_deliveries BEFORE INSERT ON bridge_deliveries BEGIN SELECT RAISE(ABORT, 'legacy fired'); END")
        c.execute("CREATE TRIGGER bridge_legacy_final BEFORE INSERT ON bridge_finalizations BEGIN SELECT RAISE(ABORT, 'legacy final'); END")
        c.execute("CREATE TRIGGER bridge_legacy_ctl BEFORE INSERT ON bridge_controls BEGIN SELECT RAISE(ABORT, 'legacy ctl'); END")
    h2 = BridgeHarness(tmp_path / "ws", h.clock)
    names = [r[0] for r in sql(h2, "SELECT name FROM sqlite_master WHERE type='trigger' AND name LIKE 'bridge_legacy%'")]
    assert names == []  # control below: current triggers are still installed
    assert sql(h2, "SELECT COUNT(*) FROM sqlite_master WHERE type='trigger' AND name IN ('bridge_evidence_no_update','bridge_finalizations_no_update')") == [(2,)]
    assert h2.delivery_cycle("b", "s", FakeRuntimeTarget()).status == "delivered"  # inserts into bridge_deliveries work again
    h2.clock.advance(31)
    tok = h2.acquire_claim("b", "s", "X", 1000)
    h2.cs.finalize_terminal(tok, {"x": 1})  # bridge_finalizations insert works again


@pytest.mark.catalog_id("CTX-003")
def test_ctx_003_early_redirect_survives_a_fresh_bootstrap_larger_than_the_budget(tmp_path):
    h = BridgeHarness(tmp_path / "ws", catch_up_budget=CatchUpBudget(max_records=2))
    bseed(h, n=0)
    red = ctl(h, "control.redirect", "rd", body={"goal": "g", "instruction": "early"})  # position 1, long before the delivered Record
    for i in range(3):
        msg(h, f"m{i}")
    rt = FakeRuntimeTarget()
    for _ in range(8):
        if h.delivery_cycle("b", "s", rt).status == "idle":
            break
    rt.lose_context()
    last = msg(h, "after-loss")
    assert h.delivery_cycle("b", "s", rt).status == "delivered"
    rows = records(h)
    rest = [r[0] for r in rows if r[1] < last.position and r[0] != red.record_id]
    assert rt.catch_ups[-1] == [red.record_id, rest[-1]]  # pinned redirect first (budget-bounded), then the newest history; ordered by position
    meta = rt.catch_up_meta[-1]
    assert meta["truncated"] is True and meta["pinned_count"] == 1 and meta["included_count"] == 2
    assert meta["omitted_count"] == len(rest) + 1 - 2
    pos = [r[1] for r in rows if r[0] in rt.catch_ups[-1]]
    assert pos == sorted(pos)
    (b,) = [r for r in rows if r[3] == "context.boundary"]
    assert json.loads(b[4])["catch_up"]["pinned_count"] == 1  # persisted boundary carries the same truth


@pytest.mark.catalog_id("CTX-003")
def test_ctx_003_pinned_redirects_are_themselves_budget_bounded_newest_first(tmp_path):
    h = BridgeHarness(tmp_path / "ws", catch_up_budget=CatchUpBudget(max_records=2))
    bseed(h, n=0)
    reds = [ctl(h, "control.redirect", f"rd{i}", body={"goal": "g", "instruction": f"r{i}"}) for i in range(3)]
    msg(h, "m")
    rt = FakeRuntimeTarget()
    for _ in range(4):
        if h.delivery_cycle("b", "s", rt).status == "idle":
            break
    rt.lose_context()
    msg(h, "after-loss")
    assert h.delivery_cycle("b", "s", rt).status == "delivered"
    assert rt.catch_ups[-1] == [reds[1].record_id, reds[2].record_id]  # 3 pins, budget 2: the newest two, ordered
    # control: a redirect addressed to another peer is never pinned for this peer
    h2 = BridgeHarness(tmp_path / "ws2", catch_up_budget=CatchUpBudget(max_records=2))
    bseed(h2, peers=("a", "b", "c"), n=0)
    other = ctl(h2, "control.redirect", "for-c", body={"goal": "g", "instruction": "x"}, targets=["c"])
    msg(h2, "m")
    rt2 = FakeRuntimeTarget()
    for _ in range(4):
        if h2.delivery_cycle("b", "s", rt2).status == "idle":
            break
    rt2.lose_context()
    msg(h2, "after-loss")
    assert h2.delivery_cycle("b", "s", rt2).status == "delivered" and other.record_id not in rt2.catch_ups[-1]
