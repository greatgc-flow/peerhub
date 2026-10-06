"""Wave 6 bridge faults: FLT-003..008, FLT-014 (fake runtime, real process death via os._exit where a crash is the point).
Oracles are raw SQL / literals. Every negative has a positive control; no sleep-as-sync."""
import hashlib
import json
import sqlite3
from contextlib import closing

import pytest

from peerhub.extensions.bridge import ReconcileRejectedError
from peerhub.extensions.bridge_claims import ClaimHeldError, ClaimToken, StaleClaimError
from tests.communication.bridge_helpers import bseed, delivery_rows, evidence, kinds, offset_row, records, responses, sql
from tests.communication.fakes import FakeRuntimeTarget
from tests.communication.harness.bridge import BridgeHarness
from tests.communication.harness.bridge_workers import crash_cycle_worker
from tests.communication.harness.crash_workers import CRASH_EXIT, cycles_worker, spawn_exitcode
from tests.communication.harness.mp import OUTQ, run_one
from tests.communication.helpers import req

pytestmark = [pytest.mark.fault, pytest.mark.bridge]
NS, MAY, ST, TERM = "NOT_STARTED", "MAY_HAVE_STARTED", "STARTED", "TERMINAL"
OK_SCRIPT = [["started", "e1"], ["terminal", {"response": "R"}]]


def crash(ws, point, script=OK_SCRIPT, clock=1000.0):
    return spawn_exitcode(crash_cycle_worker, (str(ws), "b", "s", point, script, clock))


def restart_cycles(ws, clock=1000.0, max_cycles=1, script=()):
    return run_one(cycles_worker, (str(ws), "b", "s", clock, [list(s) for s in script], max_cycles, OUTQ))


def table_hashes(h):
    """Independent per-table digest (hashlib over raw rows) of every table, for 'state unchanged' assertions."""
    out = {}
    with closing(sqlite3.connect(h.db_path)) as c:
        for (t,) in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY 1").fetchall():
            d = hashlib.sha256()
            for r in c.execute(f'SELECT * FROM "{t}" ORDER BY 1,2'):
                d.update(repr(r).encode())
            out[t] = d.hexdigest()
    return out


def reconcile(h, did, key="rc", author="a"):
    return h.append_record(req(body={"decision": "RETRY", "delivery_id": did}, key=key, author=author, kind="control.reconcile"))


# =========================================================================== FLT-003
@pytest.mark.parametrize("script", [(("disconnect",),), (("runtime_error", "transport died after spawn"),)])
@pytest.mark.catalog_id("FLT-003")
def test_flt_003_spawn_uncertainty_records_may_have_started_and_is_not_replayed(tmp_path, script):
    h = BridgeHarness(tmp_path / "ws")
    (rec,) = bseed(h)
    rt = FakeRuntimeTarget()
    rt.script_deliver(*script)
    res = h.delivery_cycle("b", "s", rt)
    assert (res.status, res.certainty) == ("uncertain", MAY)
    (row,) = delivery_rows(h, rec.record_id)
    assert row[7] == MAY and row[12] is None  # execution evidence state MAY_HAVE_STARTED, no terminal result invented
    assert [e[2] for e in evidence(h, row[0])][-1] == MAY
    assert responses(h) == [] and offset_row(h) == (0, 1)
    state = table_hashes(h)
    # not auto-replayed: repeated scheduling, also by a restarted process, never starts the runtime again
    for _ in range(3):
        assert h.delivery_cycle("b", "s", rt).status == "blocked_uncertain"
    out = restart_cycles(tmp_path / "ws", max_cycles=3)
    assert out["statuses"] == ["blocked_uncertain"] and out["calls"] == []
    assert rt.count("deliver") == 1 and table_hashes(h) == state
    # positive control: a PRE-spawn failure (NOT_STARTED) IS retried automatically, so the uncertainty block is selective
    h2 = BridgeHarness(tmp_path / "ws2")
    bseed(h2)
    rt2 = FakeRuntimeTarget()
    rt2.script_deliver(("prespawn_error", "executable missing"))
    assert h2.delivery_cycle("b", "s", rt2).status == "failed_not_started"
    assert h2.delivery_cycle("b", "s", rt2).status == "delivered" and rt2.count("deliver") == 2


# =========================================================================== FLT-004
@pytest.mark.catalog_id("FLT-004")
def test_flt_004_may_have_started_blocks_every_automatic_retry_until_explicit_reconciliation(tmp_path):
    ws = tmp_path / "ws"
    h = BridgeHarness(ws)
    (rec,) = bseed(h)
    assert crash(ws, "bridge.before_runtime_invoke") == CRASH_EXIT  # persisted ambiguous evidence (marker before invoke)
    (row,) = delivery_rows(h, rec.record_id)
    assert row[7] == NS  # D-OWN-A4: pending marker, certainty not yet promoted (recovery does that, durably)
    did = row[0]
    state = None
    for hours in (0, 1, 10 ** 6):  # restart / reschedule automatically, however much time passes
        h.clock.advance(hours * 3600)
        out = restart_cycles(ws, clock=h.clock.now(), max_cycles=2)
        assert out["statuses"] == ["blocked_uncertain"] and out["calls"] == []
        if state is None:  # first recovery promoted the unresolved marker; from now on delivery state may not move
            assert delivery_rows(h, rec.record_id)[0][7] == MAY
            state = {t: d for t, d in table_hashes(h).items() if t != "bridge_claims"}  # lease bookkeeping may move
    assert {t: d for t, d in table_hashes(h).items() if t != "bridge_claims"} == state  # nothing else written by scheduler passes
    # a forged reconciliation by the bridged peer itself does not clear the block
    forged = reconcile(h, did, key="forged", author="b")
    with pytest.raises(ReconcileRejectedError):
        h.reconcile_uncertain(rec.record_id, forged)
    rt = FakeRuntimeTarget()
    assert h.delivery_cycle("b", "s", rt).status == "blocked_uncertain" and rt.calls == []
    # explicit operator decision (durable control.reconcile RETRY by another peer) permits exactly ONE new attempt
    good = reconcile(h, did)
    h.reconcile_uncertain(rec.record_id, good)
    res = h.delivery_cycle("b", "s", rt)
    assert res.status == "delivered" and rt.count("deliver") == 1
    rows = delivery_rows(h, rec.record_id)
    assert [r[6] for r in rows] == [1, 2] and rows[0][7] == MAY and rows[1][7] == TERM  # old evidence kept; one linked retry
    assert len(responses(h)) == 1


# =========================================================================== FLT-005
@pytest.mark.parametrize("point,resp_rows,ack", [("bridge.after_terminal_evidence_before_offset_ack", 1, (0, 1)),
                                                  ("bridge.after_terminal_evidence_before_response_append", 0, (0, 1))])
@pytest.mark.catalog_id("FLT-005")
def test_flt_005_crash_after_terminal_evidence_before_offset_ack_resumes_without_reexecution(tmp_path, point, resp_rows, ack):
    ws = tmp_path / "ws"
    h = BridgeHarness(ws)
    (rec,) = bseed(h)
    assert crash(ws, point) == CRASH_EXIT
    (row,) = delivery_rows(h, rec.record_id)
    assert row[7] == TERM and row[16] == 0 and json.loads(row[12]) == {"response": "R"}  # terminal evidence durable, not acked
    assert len(responses(h)) == resp_rows and offset_row(h) == ack
    out = restart_cycles(ws, max_cycles=1)  # restart: recognises the terminal truth
    assert out["statuses"] == ["recovered_terminal"]
    assert out["calls"] == []  # the runtime is not touched at all (no create/resume/deliver)
    (row2,) = delivery_rows(h, rec.record_id)
    assert row2[7] == TERM and row2[12] == row[12] and row2[16] == 1  # same truth, now acked
    (resp,) = responses(h)
    assert json.loads(resp[4]) == "R" and resp[5] == rec.record_id  # exactly one response, linked to the request
    assert offset_row(h) == (rec.position, 2)
    assert restart_cycles(ws, max_cycles=2)["statuses"] == ["idle"]  # and nothing is replayed afterwards
    assert len(responses(h)) == 1


# =========================================================================== FLT-006
@pytest.mark.parametrize("point,expected,recovered", [("bridge.before_runtime_invoke", NS, MAY), ("bridge.after_runtime_start", ST, ST),
                                                      ("bridge.after_partial_output", ST, ST)])
@pytest.mark.catalog_id("FLT-006")
def test_flt_006_crash_after_start_never_recovers_as_not_started(tmp_path, point, expected, recovered):
    ws = tmp_path / "ws"
    h = BridgeHarness(ws)
    (rec,) = bseed(h)
    script = [["started", "e1"], ["output", "partial"], ["terminal", {"response": "R"}]]
    assert crash(ws, point, script) == CRASH_EXIT
    (row,) = delivery_rows(h, rec.record_id)
    assert row[7] == expected  # before recovery: start evidence STARTED; a bare invocation marker keeps NOT_STARTED (D-OWN-A4)
    out = restart_cycles(ws, max_cycles=2)
    assert out["statuses"] == ["blocked_uncertain"] and out["calls"] == []  # no second start
    (row2,) = delivery_rows(h, rec.record_id)
    assert row2[7] == recovered and row2[7] != NS and responses(h) == [] and offset_row(h) == (0, 1)  # recovered, never NOT_STARTED
    assert "terminal" not in kinds(h, row[0])  # no fabricated terminal response


@pytest.mark.catalog_id("FLT-006")
def test_flt_006_positive_control_crash_before_any_start_evidence_is_retried(tmp_path):
    ws = tmp_path / "ws"
    h = BridgeHarness(ws)
    bseed(h)
    assert crash(ws, "claim.after_acquire") == CRASH_EXIT  # dies before an attempt exists
    assert delivery_rows(h) == []
    out = restart_cycles(ws, clock=h.clock.now() + 3600, max_cycles=1)  # lease expired -> a fresh bridge takes over and delivers
    assert out["statuses"] == ["delivered"] and [c[0] for c in out["calls"]].count("deliver") == 1


# =========================================================================== FLT-007
def _lose_claim(h, holder, advance=31, owner="B"):
    def go():
        h.clock.advance(advance)  # A's lease lapses ...
        holder["b"] = h.acquire_claim("b", "s", owner, 30)  # ... and B takes over
    return go


@pytest.mark.catalog_id("FLT-007")
def test_flt_007_claim_expiring_mid_execution_fences_stale_owner_finalize_and_offset(tmp_path):
    h = BridgeHarness(tmp_path / "ws")
    (rec,) = bseed(h)
    holder = {}
    rt = FakeRuntimeTarget()
    rt.script_deliver(("started", "e1"), ("call", _lose_claim(h, holder)), ("terminal", {"response": "stale"}))
    res = h.delivery_cycle("b", "s", rt)
    assert (res.status, res.certainty) == ("fenced", ST)
    stale = ClaimToken(h.workspace_generation(), 1, "bridge-1", "b", "s")
    state = table_hashes(h)
    with pytest.raises(StaleClaimError):
        h.finalize_terminal(stale, {"response": "stale"}, res.delivery_id)
    with pytest.raises(StaleClaimError):  # stale Offset advancement through the Core fence
        h.store.advance_offset_cas("b", "s", rec.position, 1, guard=h.cs.guard(stale))
    # only append-only evidence may have been added by the rejected finalize; delivery/offset/records are exactly as before
    after = table_hashes(h)
    assert {t for t in after if after[t] != state[t]} <= {"bridge_evidence", "sqlite_sequence"}
    (row,) = delivery_rows(h, rec.record_id)
    assert row[7] == ST and row[16] == 0 and offset_row(h) == (0, 1) and responses(h) == []
    assert "stale_terminal_callback" in kinds(h) and "fenced_mid_delivery" in kinds(h)  # uncertainty is recorded as evidence
    # positive control: the CURRENT owner (B, generation 2) can finalize and ack the same delivery
    out = h.finalize_terminal(holder["b"], {"response": "from B"}, res.delivery_id)
    assert out.outcome == "first" and offset_row(h) == (rec.position, 2) and len(responses(h)) == 1


@pytest.mark.catalog_id("FLT-007")
def test_flt_007_exact_lease_boundary_is_the_fence(tmp_path):
    h = BridgeHarness(tmp_path / "ws")
    (rec,) = bseed(h)
    tok = h.acquire_claim("b", "s", "A", 30)
    h.clock.advance(29.999)  # still inside the lease (TD-19: expired when now >= expires_at)
    assert h.heartbeat(tok) == tok  # positive control: heartbeat extends it
    h.clock.advance(30.0)  # exactly at the (extended) expiry
    with pytest.raises(StaleClaimError):
        h.heartbeat(tok)
    with pytest.raises(StaleClaimError):
        h.store.advance_offset_cas("b", "s", rec.position, 1, guard=h.cs.guard(tok))
    assert offset_row(h) == (0, 1)


# =========================================================================== FLT-008
def _backup(db, dst):
    src, out = sqlite3.connect(db), sqlite3.connect(dst)
    try:
        src.backup(out)
    finally:
        src.close(), out.close()


@pytest.mark.catalog_id("FLT-008")
def test_flt_008_restore_generation_switch_invalidates_claim_and_session_mapping(tmp_path):
    h = BridgeHarness(tmp_path / "ws")
    (r1,) = bseed(h)
    rt = FakeRuntimeTarget()
    assert h.delivery_cycle("b", "s", rt).status == "delivered"  # creates the session under G1
    m1 = h.current_mapping("b", "s")
    assert (m1["external_session_id"], m1["session_generation"]) == ("ext-1", 1) and "ext-1" in rt.sessions
    tok1 = h.acquire_claim("b", "s", "bridge-1", 300)
    assert h.heartbeat(tok1) == tok1  # positive control: valid under G1
    snap = tmp_path / "snap.db"
    _backup(h.db_path, snap)
    g1 = h.workspace_generation()
    g2 = h.ws.restore_snapshot(snap)
    h.reopen()
    assert g2 != g1 and h.workspace_generation() == g2
    # the G1 claim cannot be used although its lease is unexpired
    before = table_hashes(h)
    for op in (lambda: h.heartbeat(tok1), lambda: h.cs.assert_current(tok1),
               lambda: h.store.append_record(guard=h.cs.guard(tok1), **req(body="late", key="late", author="b"))):
        with pytest.raises(StaleClaimError):
            op()
    assert table_hashes(h) == before
    # the G1 session mapping cannot be resumed: a fresh session generation is required (the provider still holds ext-1!)
    r2 = h.append_record(req(body="after restore", key="m-after", author="a"))
    res = h.delivery_cycle("b", "s", rt)
    assert res.status == "delivered" and res.record_id == r2.record_id
    assert ("resume", "ext-1") not in rt.calls and rt.count("resume") == 0
    assert res.session_generation == 2 and h.current_mapping("b", "s")["external_session_id"] == "ext-2"
    assert [e["event"] for e in h.bridge.session_events("b", "s")][-1] == "fresh_generation"
    assert json.loads(h.bridge.session_events("b", "s")[-1]["detail"])["reason"] == "workspace_generation_change"
    # fresh G2 claim works
    fresh = h.acquire_claim("b", "s", "bridge-1", 300)
    assert fresh.workspace_generation == g2 and h.heartbeat(fresh) == fresh


# =========================================================================== FLT-014
@pytest.mark.catalog_id("FLT-014")
def test_flt_014_stale_owner_late_terminal_is_fenced_before_response_and_offset(tmp_path):
    h = BridgeHarness(tmp_path / "ws")
    (rec,) = bseed(h)
    holder = {}
    rt = FakeRuntimeTarget()
    # A's runtime delivers its terminal result only AFTER the claim moved to B (generation 2)
    rt.script_deliver(("started", "e1"), ("call", _lose_claim(h, holder)), ("output", "late"), ("terminal", {"response": "late A"}))
    res = h.delivery_cycle("b", "s", rt)
    assert res.status == "fenced"
    assert len(records(h)) == 1 and responses(h) == [] and offset_row(h) == (0, 1)  # fenced before any response Record/Offset write
    stale = ClaimToken(h.workspace_generation(), 1, "bridge-1", "b", "s")
    before = table_hashes(h)
    with pytest.raises(StaleClaimError):  # the late terminal callback as an explicit finalize
        h.finalize_terminal(stale, {"response": "late A"}, res.delivery_id)
    with pytest.raises(StaleClaimError):  # and a direct late response append through the Core fence
        h.store.append_record(guard=h.cs.guard(stale), **req(body="late A", key="late", author="b", kind="response"))
    after = table_hashes(h)
    assert {t for t in after if after[t] != before[t]} <= {"bridge_evidence", "sqlite_sequence"}  # evidence-only, non-authoritative
    ev = sql(h, "SELECT kind, certainty, detail FROM bridge_evidence WHERE kind='stale_terminal_callback'")
    assert len(ev) == 1 and ev[0][1] is None and json.loads(ev[0][2])["claim_generation"] == 1  # carries no certainty claim
    assert sql(h, "SELECT COUNT(*) FROM bridge_finalizations") == [(0,)]
    (row,) = delivery_rows(h, rec.record_id)
    assert row[7] == ST and row[12] is None
    # positive control: B (generation 2) finalizes the SAME delivery exactly once
    assert h.finalize_terminal(holder["b"], {"response": "from B"}, res.delivery_id).outcome == "first"
    assert [json.loads(r[4]) for r in responses(h)] == ["from B"] and offset_row(h) == (rec.position, 2)
    # a second, late stale callback after B's success still changes neither response nor offset
    with pytest.raises(StaleClaimError):
        h.finalize_terminal(stale, {"response": "late A"}, res.delivery_id)
    assert [json.loads(r[4]) for r in responses(h)] == ["from B"] and offset_row(h) == (rec.position, 2)


@pytest.mark.catalog_id("FLT-014")
def test_flt_014_takeover_races_are_explicit_not_silent(tmp_path):
    h = BridgeHarness(tmp_path / "ws")
    bseed(h)
    a = h.acquire_claim("b", "s", "A", 30)
    with pytest.raises(ClaimHeldError):  # before expiry B cannot take over
        h.acquire_claim("b", "s", "B", 30)
    h.clock.advance(30)
    b = h.acquire_claim("b", "s", "B", 30)
    assert (a.generation, b.generation) == (1, 2)
    with pytest.raises(StaleClaimError):
        h.heartbeat(a)
