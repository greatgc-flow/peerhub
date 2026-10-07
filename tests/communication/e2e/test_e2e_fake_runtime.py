"""Wave 6 E2E-001..008, 010..012 (fake runtimes only; real spawned processes for restart/crash/concurrency).
Catalog tier `e2e` kept in the default gate via the `integration` marker (D-W3-1 / Q-W5-11 precedent): the default run deselects the
`e2e` marker, these tests are deterministic and must not be hidden. Oracles: raw SQL / literals."""
import hashlib
import itertools
import json
import sqlite3
from contextlib import closing

import pytest

from peerhub.extensions.bridge import RuntimeTargetError
from peerhub.extensions.bridge_claims import ClaimHeldError, ClaimToken, StaleClaimError
from tests.communication.bridge_helpers import bseed, delivery_rows, offset_row, records, responses, sql
from tests.communication.control_helpers import control_rows, ctl, running
from tests.communication.fakes import FakeRuntimeTarget
from tests.communication.harness.bridge import BridgeHarness
from tests.communication.harness.bridge_workers import crash_cycle_worker, control_crash_worker  # noqa: F401
from tests.communication.harness.core import CoreHarness
from tests.communication.harness.crash_workers import (
    CRASH_EXIT, core_append_crash_worker, cycles_worker, recovery_worker, spawn_exitcode,
)
from tests.communication.harness.mp import BARRIER, OUTQ, run_one, run_procs
from tests.communication.helpers import req, seed

pytestmark = [pytest.mark.integration, pytest.mark.fault]


def restart(ws, clock=1000.0, max_cycles=1, script=(), peer="b"):
    return run_one(cycles_worker, (str(ws), peer, "s", clock, [list(s) for s in script], max_cycles, OUTQ))


def raw_ids(h, stream="s", where="1=1"):
    return [r[0] for r in sql(h, f"SELECT record_id FROM records WHERE stream_id=? AND {where} ORDER BY position", (stream,))]


# =========================================================================== E2E-001
@pytest.mark.catalog_id("E2E-001")
def test_e2e_001_two_peers_one_adapter_one_stream(tmp_path):
    h = BridgeHarness(tmp_path / "ws")
    for p in ("u", "a", "b"):
        h.create_peer({"peer_id": p})
    h.create_stream({"stream_id": "s", "members": ["u", "a", "b"]})
    user = h.append_record(req(body="hello", key="u1", author="u"))
    rt = FakeRuntimeTarget()  # ONE adapter instance serves both peers
    rt.script_deliver(("started", "xa"), ("terminal", {"response": "A says hi"}))
    rt.script_deliver(("started", "xb"), ("terminal", {"response": "B says hi"}))
    assert h.current_mapping("b", "s") is None
    ra = h.delivery_cycle("a", "s", rt)
    assert ra.status == "delivered"
    assert offset_row(h, "b") == (0, 1) and h.current_mapping("b", "s") is None  # peer b untouched by a's cycle
    rb = h.delivery_cycle("b", "s", rt)
    assert rb.status == "delivered"
    # durable ordered transcript (literal oracle)
    assert [(r[1], r[2], r[3], json.loads(r[4])) for r in records(h)] == [
        (1, "u", "message", "hello"), (2, "a", "response", "A says hi"), (3, "b", "response", "B says hi")]
    assert [r[5] for r in records(h)] == [None, user.record_id, user.record_id]
    assert offset_row(h, "a") == (1, 2) and offset_row(h, "b") == (1, 2)  # independent per-peer offsets
    ma, mb = h.current_mapping("a", "s"), h.current_mapping("b", "s")
    assert (ma["external_session_id"], mb["external_session_id"]) == ("ext-1", "ext-2") and ma["session_generation"] == mb["session_generation"] == 1
    assert rt.calls == [("create",), ("deliver", "ext-1", user.record_id, 0), ("create",), ("deliver", "ext-2", user.record_id, 0)]
    assert [(d[3], d[7], d[16]) for d in delivery_rows(h)] == [("a", "TERMINAL", 1), ("b", "TERMINAL", 1)]


# =========================================================================== E2E-002
@pytest.mark.catalog_id("E2E-002")
def test_e2e_002_client_retry_plus_restart_yields_one_logical_append(tmp_path):
    h = CoreHarness(tmp_path / "ws")
    seed(h)
    h.append_record(req(body="earlier", key="k0"))
    request = req(body={"q": "once"}, key="retry-me")
    # the first process commits and then dies before the client ever sees the result
    assert spawn_exitcode(core_append_crash_worker, (str(h.db_path), request, "append.after_commit")) == CRASH_EXIT
    committed = sql(h, "SELECT record_id, position FROM records WHERE idempotency_key='retry-me'")
    assert len(committed) == 1
    h2 = CoreHarness(tmp_path / "ws")  # restart: the client retries with the same idempotency key
    got = h2.append_record(request)
    assert (got.record_id, got.position) == committed[0]  # the client obtains THE record
    assert sql(h2, "SELECT COUNT(*) FROM records WHERE idempotency_key='retry-me'") == [(1,)] and sql(h2, "SELECT COUNT(*) FROM records") == [(2,)]
    # positive control: a never-committed attempt (death BEFORE commit) is not visible and the retry creates exactly one record
    assert spawn_exitcode(core_append_crash_worker, (str(h.db_path), req(body="z", key="pre"), "append.before_commit")) == CRASH_EXIT
    assert sql(h2, "SELECT COUNT(*) FROM records WHERE idempotency_key='pre'") == [(0,)]
    h2.append_record(req(body="z", key="pre"))
    assert sql(h2, "SELECT COUNT(*) FROM records WHERE idempotency_key='pre'") == [(1,)]


# =========================================================================== E2E-003
@pytest.mark.catalog_id("E2E-003")
def test_e2e_003_restart_catches_up_unread_records_in_order_without_duplicates(tmp_path):
    ws = tmp_path / "ws"
    h = BridgeHarness(ws)
    for p in ("u", "b"):
        h.create_peer({"peer_id": p})
    h.create_stream({"stream_id": "s", "members": ["u", "b"]})
    for i in (1, 2):
        h.append_record(req(body=f"m{i}", key=f"m{i}", author="u"))
    rt = FakeRuntimeTarget()
    assert [h.delivery_cycle("b", "s", rt).status for _ in range(3)] == ["delivered", "delivered", "idle"]
    before_resp = len(responses(h))
    assert before_resp == 2 and offset_row(h)[0] == 4  # read through its own responses too
    unread = [h.append_record(req(body=f"m{i}", key=f"m{i}", author="u")) for i in (3, 4, 5)]
    expected = [u.record_id for u in unread]
    assert [u.position for u in unread] == [5, 6, 7] and raw_ids(h, where="position>4") == expected
    del h  # stop the process; a NEW process takes over
    out = restart(ws, max_cycles=10)
    assert out["statuses"] == ["delivered", "delivered", "delivered", "idle"]
    assert [c[2] for c in out["calls"] if c[0] == "deliver"] == expected  # unread 5..7 delivered in order, nothing older
    h2 = BridgeHarness(ws)
    new = responses(h2)[before_resp:]
    assert [r[5] for r in new] == expected and [r[1] for r in new] == [9, 10, 11]  # one response each, in order, no duplicates
    # the new process has no provider session (fresh generation): exactly one boundary Record (position 8) precedes the responses
    assert [(r[1], r[3]) for r in records(h2) if r[1] > 7] == [(8, "context.boundary"), (9, "response"), (10, "response"), (11, "response")]
    assert len(responses(h2)) == before_resp + 3 and offset_row(h2)[0] == 11
    assert restart(ws, max_cycles=3)["statuses"] == ["idle"] and len(responses(h2)) == before_resp + 3  # second restart: nothing replays


# =========================================================================== E2E-004
@pytest.mark.catalog_id("E2E-004")
def test_e2e_004_may_have_started_survives_restart_and_blocks_blind_replay(tmp_path):
    ws = tmp_path / "ws"
    h = BridgeHarness(ws)
    (rec,) = bseed(h)
    assert spawn_exitcode(crash_cycle_worker, (str(ws), "b", "s", "bridge.before_runtime_invoke",
                                                [["started", "e1"], ["terminal", {"response": "R"}]], 1000.0)) == CRASH_EXIT
    out = restart(ws, clock=1000.0 + 10 ** 6, max_cycles=5)  # restart + scheduler, much later
    assert out["statuses"] == ["blocked_uncertain"] and out["calls"] == []  # no automatic second runtime start
    ev = h.bridge.execution_evidence(rec.record_id, "b")  # status exposes the uncertainty
    assert [(d["certainty"], d["attempt"], d["acked"]) for d in ev["deliveries"]] == [("MAY_HAVE_STARTED", 1, 0)]
    assert ev["events"][-1]["certainty"] == "MAY_HAVE_STARTED" and responses(h) == [] and offset_row(h) == (0, 1)
    # positive control: after an explicit reconciliation the scheduler runs the runtime exactly once more
    h.reconcile_uncertain(rec.record_id, h.append_record(req(body={"decision": "RETRY", "delivery_id": ev["deliveries"][0]["delivery_id"]},
                                                              key="rc", author="a", kind="control.reconcile")))
    out2 = restart(ws, clock=1000.0 + 10 ** 6 + 100, max_cycles=1)
    assert out2["statuses"] == ["delivered"] and [c[0] for c in out2["calls"]].count("deliver") == 1


# =========================================================================== E2E-005
@pytest.mark.catalog_id("E2E-005")
def test_e2e_005_pause_commit_precedes_runtime_interrupt_end_to_end(tmp_path):
    seq, events = itertools.count(1), []
    hook = lambda point: events.append((next(seq), point)) if point == "control.after_intent_commit" else None
    h = BridgeHarness(tmp_path / "ws", fault_hook=hook)
    bseed(h)
    rt, _ = running(h)  # session active: STARTED, no terminal
    pause = ctl(h, "control.pause", "p1")
    rt.script_effect("interrupt", lambda: events.append((next(seq), "interrupt", sql(h, "SELECT outcome FROM bridge_controls WHERE record_id=?", (pause.record_id,)),
                                                          sql(h, "SELECT kind FROM records WHERE record_id=?", (pause.record_id,)))))
    assert [e for e in events if e[1] == "interrupt"] == []
    res = h.handle_control(pause.record_id, rt, "b")
    assert res.status == "applied" and res.runtime_outcome == "done" and res.paused
    names = [e[1] for e in events]
    assert names == ["control.after_intent_commit", "interrupt"] and [e[0] for e in events] == sorted(e[0] for e in events)  # strictly ordered
    assert events[1][2] == [(None,)] and events[1][3] == [("control.pause",)]  # at interrupt time: intent + Record already durable, outcome pending
    assert [c for c in rt.calls if c[0] == "interrupt"] == [("interrupt", "ext-1")]
    assert control_rows(h, pause.record_id)[0][6] == "done"
    # positive control for the oracle itself: a control with nothing to interrupt records intent but never calls the runtime
    h2 = BridgeHarness(tmp_path / "ws2")
    bseed(h2)
    rt2 = FakeRuntimeTarget()
    h2.delivery_cycle("b", "s", rt2)
    p2 = ctl(h2, "control.pause", "p2")
    assert h2.handle_control(p2.record_id, rt2, "b").runtime_outcome == "nothing_running" and rt2.count("interrupt") == 0


# =========================================================================== E2E-006
@pytest.mark.catalog_id("E2E-006")
def test_e2e_006_lost_session_creates_new_generation_and_reconstructs_from_durable_records(tmp_path):
    h = BridgeHarness(tmp_path / "ws")
    bseed(h, n=2)
    rt = FakeRuntimeTarget()
    assert [h.delivery_cycle("b", "s", rt).status for _ in range(2)] == ["delivered", "delivered"]
    assert rt.catch_ups[-1] == [] and ("resume", "ext-1") in rt.calls  # control: live session resumed, no catch-up needed
    assert h.current_mapping("b", "s")["session_generation"] == 1
    rt.lose_context()  # generation 1 disappears (provider memory is gone)
    new = h.append_record(req(body="after loss", key="after", author="a"))
    res = h.delivery_cycle("b", "s", rt)
    assert res.status == "delivered" and res.record_id == new.record_id and res.session_generation == 2
    durable_before = raw_ids(h, where=f"position < {new.position}")
    assert rt.catch_ups[-1] == durable_before and len(durable_before) == 4  # context comes from the durable Stream, in order
    m = h.current_mapping("b", "s")
    assert (m["session_generation"], m["external_session_id"]) == (2, "ext-2")
    assert [e["event"] for e in h.bridge.session_events("b", "s")][-2:] == ["session_lost", "fresh_generation"]
    assert len(responses(h)) == 3 and offset_row(h)[0] >= new.position


# =========================================================================== E2E-007
@pytest.mark.catalog_id("E2E-007")
def test_e2e_007_shared_pool_visible_in_diag_without_contaminating_core_peers(obs_h):
    from tests.communication.fakes.observation import FakeObservationSource, measured

    for p in ("p1", "p2"):
        obs_h.create_peer({"peer_id": p})
    obs_h.create_stream({"stream_id": "s", "members": ["p1", "p2"]})
    obs_h.register_pool({"schema_version": "1.0", "resource_pool_id": "POOL", "provider": "acme", "kind": "ACCOUNT"})
    for p in ("p1", "p2"):
        obs_h.capture(f"peer:{p}", "quota", FakeObservationSource(measured({"remaining_fraction": 0.0, "exhausted": True}, semantic="quota")),
                      resource_pool_ref="POOL")
    rt = FakeRuntimeTarget()
    before = obs_h.full_state()
    rep = obs_h.render_diag()
    assert obs_h.full_state() == before and rt.calls == []  # no writes, no runtime/provider call
    pools = rep.sections["resource_pools"].data["pools"]
    assert [(p["resource_pool_id"], p["observation_count"]) for p in pools] == [("POOL", 2)]
    items = rep.sections["observations"].data["items"]
    assert sorted((i["subject_ref"], i["resource_pool_ref"], i["payload"]["exhausted"]) for i in items) == [("peer:p1", "POOL", True), ("peer:p2", "POOL", True)]
    assert [p["peer_id"] for p in rep.sections["peers"].data["peers"]] == ["p1", "p2"]
    # serialized Core Peers stay quota-free: model dump keys and raw rows carry no quota/pool/observation data
    for p in ("p1", "p2"):
        dumped = json.dumps(obs_h.get_peer(p).model_dump(), sort_keys=True)
        assert not any(w in dumped for w in ("quota", "POOL", "remaining", "exhausted"))
    raw_peers = json.dumps(sql(obs_h, "SELECT * FROM peers ORDER BY 1"))
    assert not any(w in raw_peers for w in ("quota", "POOL", "remaining", "exhausted"))
    assert [r[0] for r in sql(obs_h, "SELECT name FROM pragma_table_info('peers') ORDER BY cid")] == [
        "peer_id", "display_name", "adapter_ref", "metadata_json", "created_at"]


# =========================================================================== E2E-008
def _backup(db, dst):
    src, out = sqlite3.connect(db), sqlite3.connect(dst)
    try:
        src.backup(out)
    finally:
        src.close(), out.close()


@pytest.mark.catalog_id("E2E-008")
def test_e2e_008_restore_generation_invalidates_old_claim_and_session_and_safely_resumes(tmp_path):
    h = BridgeHarness(tmp_path / "ws")
    (r1,) = bseed(h)
    rt = FakeRuntimeTarget()
    assert h.delivery_cycle("b", "s", rt).status == "delivered"
    tok1 = h.acquire_claim("b", "s", "bridge-1", 300)  # G1 claim; G1 session ext-1
    snap = tmp_path / "snap.db"
    _backup(h.db_path, snap)
    snap_ids = raw_ids(h)
    r2 = h.append_record(req(body="advance after snapshot", key="adv", author="a"))  # state advances past the snapshot
    assert h.delivery_cycle("b", "s", rt).status == "delivered" and len(responses(h)) == 2
    g1 = h.workspace_generation()
    g2 = h.ws.restore_snapshot(snap)
    h.reopen()
    assert g2 != g1 and raw_ids(h) == snap_ids and r2.record_id not in raw_ids(h)  # durable state is the snapshot's
    for op in (lambda: h.heartbeat(tok1), lambda: h.cs.assert_current(tok1),
               lambda: h.store.advance_offset_cas("b", "s", 1, 1, guard=h.cs.guard(tok1))):
        with pytest.raises(StaleClaimError):  # G1 operations rejected
            op()
    # G2 resumes safely from the durable state: new human input is delivered through a FRESH session generation
    r3 = h.append_record(req(body="after restore", key="post", author="a"))
    calls_before = len(rt.calls)
    res = h.delivery_cycle("b", "s", rt)
    assert res.status == "delivered" and res.record_id == r3.record_id and res.session_generation == 2
    new_calls = rt.calls[calls_before:]
    assert [c[0] for c in new_calls] == ["create", "deliver"]  # the provider's ext-1 (which remembers r2!) was never resumed
    assert rt.catch_ups[-1] == snap_ids  # context = restored durable records only; the lost r2 never reaches the new session
    assert r2.record_id not in rt.catch_ups[-1]
    fresh = h.acquire_claim("b", "s", "bridge-1", 300)
    assert fresh.workspace_generation == g2 and h.heartbeat(fresh) == fresh
    assert len(responses(h)) == 2 and offset_row(h)[0] >= r3.position  # exactly one new response on top of the restored history


# =========================================================================== E2E-010
@pytest.mark.catalog_id("E2E-010")
def test_e2e_010_resume_rejection_falls_back_to_exactly_one_fresh_generation(tmp_path):
    h = BridgeHarness(tmp_path / "ws")
    (r1,) = bseed(h)
    rt = FakeRuntimeTarget()
    assert h.delivery_cycle("b", "s", rt).status == "delivered"
    r2 = h.append_record(req(body="second", key="m2", author="a"))
    rt.script_resume("rejected")  # provider rejects resume of ext-1 but accepts a fresh session
    start = len(rt.calls)
    res = h.delivery_cycle("b", "s", rt)
    prior_ids = raw_ids(h, where=f"position < {r2.position}")
    assert rt.calls[start:] == [("resume", "ext-1"), ("create",), ("deliver", "ext-2", r2.record_id, len(prior_ids))]  # exactly one resume, one create
    assert res.status == "delivered" and res.session_generation == 2 and rt.catch_ups[-1] == prior_ids  # bounded catch-up
    assert [r[5] for r in responses(h)] == [r1.record_id, r2.record_id] and offset_row(h)[0] >= r2.position  # appended once
    assert [e["event"] for e in h.bridge.session_events("b", "s")][-2:] == ["resume_rejected", "fresh_generation"]
    after = list(rt.calls)
    assert h.delivery_cycle("b", "s", rt).status == "idle" and rt.calls == after  # no loop, nothing re-attempted
    # positive control: without the rejection the same sequence resumes (one resume, no new create)
    h2 = BridgeHarness(tmp_path / "ws2")
    bseed(h2)
    rt2 = FakeRuntimeTarget()
    h2.delivery_cycle("b", "s", rt2)
    h2.append_record(req(body="second", key="m2", author="a"))
    h2.delivery_cycle("b", "s", rt2)
    assert rt2.count("resume") == 1 and rt2.count("create") == 1


# =========================================================================== E2E-011
@pytest.mark.catalog_id("E2E-011")
def test_e2e_011_cancel_intent_stays_durable_when_termination_fails_across_restart(tmp_path):
    ws = tmp_path / "ws"
    h = BridgeHarness(ws)
    bseed(h)
    rt, _ = running(h)
    prior = sql(h, "SELECT * FROM records WHERE stream_id='s' ORDER BY position")
    off = offset_row(h)
    cancel = ctl(h, "control.cancel", "cx")
    rt.script_effect("terminate", RuntimeTargetError("kill hook raised"))
    res = h.handle_control(cancel.record_id, rt, "b")
    assert (res.status, res.runtime_outcome) == ("applied", "failed")
    ev_before = sql(h, "SELECT kind, certainty, detail FROM bridge_evidence ORDER BY seq")
    del h
    h2 = BridgeHarness(ws)  # restart
    assert sql(h2, "SELECT * FROM records WHERE stream_id='s' ORDER BY position")[:len(prior)] == prior  # history not rewritten
    assert [r[3] for r in records(h2)][-1] == "control.cancel" and len(records(h2)) == len(prior) + 1  # cancel Record present
    assert offset_row(h2) == off
    (row,) = control_rows(h2, cancel.record_id)
    assert row[6] == "failed" and "kill hook raised" in row[7]  # explicit failure evidence persisted
    assert sql(h2, "SELECT kind, certainty, detail FROM bridge_evidence ORDER BY seq")[:len(ev_before)] == ev_before
    again = h2.handle_control(cancel.record_id, rt, "b")  # replay after restart: failure is not retried behind the user's back
    assert again.status == "replayed" and rt.count("terminate") == 1
    out = restart(ws, max_cycles=2)  # the delivery cycle after restart never silently re-executes
    assert out["statuses"] == ["blocked_uncertain"] and out["calls"] == []
    # positive control: when terminate works the same path ends 'done'
    h3 = BridgeHarness(tmp_path / "ws3")
    bseed(h3)
    rt3, _ = running(h3)
    assert h3.handle_control(ctl(h3, "control.cancel", "cx").record_id, rt3, "b").runtime_outcome == "done"


# =========================================================================== E2E-012
@pytest.mark.catalog_id("E2E-012")
def test_e2e_012_two_recovering_bridges_deliver_exactly_once(tmp_path):
    ws = tmp_path / "ws"
    h = BridgeHarness(ws)
    (rec,) = bseed(h)
    assert spawn_exitcode(crash_cycle_worker, (str(ws), "b", "s", "claim.after_acquire", [], 1000.0)) == CRASH_EXIT  # previous owner dies
    assert delivery_rows(h) == [] and sql(h, "SELECT owner_id, generation FROM bridge_claims") == [("bridge-1", 1)]
    clock = 1000.0 + 31  # its lease has expired
    results = run_procs([(recovery_worker, (str(ws), "b", "s", clock, o, BARRIER, OUTQ)) for o in ("rec-A", "rec-B")])
    winners = [r for r in results if r.get("status") == "delivered"]
    losers = [r for r in results if "claim_err" in r]
    assert len(winners) == 1 and len(losers) == 1, results
    assert winners[0]["deliver_calls"] == 1 and losers[0]["deliver_calls"] == 0 and losers[0]["claim_err"]["type"] == "ClaimHeldError"
    h2 = BridgeHarness(ws)
    (d,) = delivery_rows(h2, rec.record_id)
    assert d[6] == 1 and d[7] == "TERMINAL" and d[16] == 1  # exactly one delivery, completed
    assert len(responses(h2)) == 1 and offset_row(h2) == (rec.position, 2)
    assert sql(h2, "SELECT owner_id, generation FROM bridge_claims") == [(winners[0]["owner"], 2)]  # one claim generation won; loser fenced
    with pytest.raises(ClaimHeldError):
        h2.acquire_claim("b", "s", losers[0]["owner"], 30)  # loser still cannot act
