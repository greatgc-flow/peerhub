"""Wave 2: CON-006/007/008 minimal fenced-claim kernel (TD-04, TD-19, TD-25). Full Bridge semantics arrive in Wave 3 (CLM)."""
import sqlite3
import threading

import pytest

from peerhub.extensions.bridge_claims import (
    ClaimFinalizedError, ClaimHeldError, ClaimScopeError, ClaimStore, ClaimToken, StaleClaimError,
)
from tests.m1.harness.clock import ManualClock
from tests.m1.harness.mp import run_threads
from tests.m1.helpers import append_n, req, seed

pytestmark = [pytest.mark.concurrency, pytest.mark.bridge]


def _final_rows(db_path):
    with sqlite3.connect(db_path) as c:
        return c.execute("SELECT stream_id, peer_id, claim_generation, result_json FROM bridge_finalizations ORDER BY 1,2,3").fetchall()


def _claim_rows(db_path):
    with sqlite3.connect(db_path) as c:
        return c.execute("SELECT * FROM bridge_claims ORDER BY 1,2").fetchall()


@pytest.mark.m1_id("CON-006")
def test_con_006_claim_race_elects_one_active_owner(harness):
    seed(harness)
    clock = ManualClock()
    for rnd in range(20):
        peer, stream = "a", "s"
        if rnd:
            harness.create_stream({"stream_id": f"s{rnd}", "members": ["a", "b"]})
            stream = f"s{rnd}"
        barrier = threading.Barrier(3, timeout=60)
        stores = [ClaimStore(harness.db_path, generation=harness.ws.generation, clock=clock.now) for _ in range(3)]

        def make(cs, owner):
            def run():
                barrier.wait()
                return cs.acquire(peer, stream, owner, lease_sec=30)
            return run

        res = run_threads([make(cs, f"o{i}") for i, cs in enumerate(stores)])
        won = [r for r in res if not isinstance(r, Exception)]
        lost = [r for r in res if isinstance(r, Exception)]
        assert len(won) == 1 and len(lost) == 2 and all(isinstance(e, ClaimHeldError) for e in lost), res
        rows = [r for r in _claim_rows(harness.db_path) if r[0] == stream]
        assert len(rows) == 1 and rows[0][2] == won[0].owner_id and rows[0][3] == won[0].generation == 1


@pytest.mark.m1_id("CON-007")
def test_con_007_expired_claim_takeover_increments_generation(harness):
    seed(harness)
    clock = ManualClock()
    cs = harness.claims(clock)
    a = cs.acquire("a", "s", "A", lease_sec=30)
    clock.advance(29.5)
    with pytest.raises(ClaimHeldError):  # control: before expiry B cannot take over
        cs.acquire("a", "s", "B", lease_sec=30)
    clock.advance(0.5)  # exactly expires_at: TD-19 now >= expires_at is expired
    b = cs.acquire("a", "s", "B", lease_sec=30)
    assert b.owner_id == "B" and b.generation > a.generation
    with pytest.raises(StaleClaimError):
        cs.heartbeat(a)
    with pytest.raises(StaleClaimError):
        cs.assert_current(a)
    assert cs.heartbeat(b).generation == b.generation  # control: B's token is valid


@pytest.mark.m1_id("CON-007")
def test_con_007_exact_expiry_boundary_and_same_owner_retake_fence_old_token(harness):
    seed(harness)
    clock = ManualClock()
    cs = harness.claims(clock)
    a1 = cs.acquire("a", "s", "A", lease_sec=30)
    clock.advance(29.999)
    assert cs.heartbeat(a1) == a1  # control: renewal strictly before expiry keeps the generation
    clock.advance(30.0)  # exactly the new expires_at (TD-19: now >= expires_at is expired)
    with pytest.raises(StaleClaimError):
        cs.heartbeat(a1)  # an expired claim cannot be renewed, even by its owner
    a2 = cs.acquire("a", "s", "A", lease_sec=30)  # same owner re-takes after expiry
    assert a2.owner_id == a1.owner_id and a2.generation == a1.generation + 1
    with pytest.raises(StaleClaimError):
        cs.assert_current(a1)  # fenced by generation, not by owner identity
    assert cs.heartbeat(a2) == a2


@pytest.mark.m1_id("CON-008")
def test_con_008_stale_owner_heartbeat_finalize_and_ack_are_fenced(harness):
    seed(harness)
    append_n(harness, 3)
    clock = ManualClock()
    cs = harness.claims(clock)
    a = cs.acquire("a", "s", "A", lease_sec=30)
    clock.advance(31)
    b = cs.acquire("a", "s", "B", lease_sec=30)
    state, rows, off = harness.state_digest(), _claim_rows(harness.db_path), harness.get_offset("a", "s")
    with pytest.raises(StaleClaimError):
        cs.heartbeat(a)
    with pytest.raises(StaleClaimError):
        cs.assert_current(a)
    with pytest.raises(StaleClaimError):
        cs.finalize_terminal(a, {"text": "stale result"})  # REAL terminal finalization write under the fence (TD-25)
    assert _final_rows(harness.db_path) == []
    with pytest.raises(StaleClaimError):
        harness.store.advance_offset_cas("a", "s", 2, off.revision, guard=cs.guard(a))  # offset ack
    with pytest.raises(StaleClaimError):
        harness.store.append_record(guard=cs.guard(a), **req(body="late response", key="late"))  # response append
    assert harness.state_digest() == state and _claim_rows(harness.db_path) == rows and harness.get_offset("a", "s") == off
    assert rows[0][2] == "B" and rows[0][3] == b.generation  # still owned by B
    # positive controls: the same operations succeed with the current token
    assert harness.store.advance_offset_cas("a", "s", 2, off.revision, guard=cs.guard(b)).read_through_position == 2
    assert harness.store.append_record(guard=cs.guard(b), **req(body="resp", key="resp")).position == 4
    cs.finalize_terminal(b, {"text": "done"})
    assert _final_rows(harness.db_path) == [("s", "a", b.generation, '{"text":"done"}')]
    with pytest.raises(ClaimFinalizedError):  # a generation finalizes once; no overwrite
        cs.finalize_terminal(b, {"text": "other"})
    assert _final_rows(harness.db_path)[0][3] == '{"text":"done"}'


@pytest.mark.m1_id("CON-008")
def test_con_008_claim_is_bound_to_its_stream_and_peer(harness):
    seed(harness)
    harness.create_stream({"stream_id": "s2", "members": ["a", "b"]})
    append_n(harness, 2)
    append_n(harness, 2, stream="s2", author="b", prefix="x")
    clock = ManualClock()
    cs = harness.claims(clock)
    t_sa = cs.acquire("a", "s", "A", lease_sec=1000)
    t_s2b = cs.acquire("b", "s2", "A", lease_sec=1000)  # same owner id, other scope
    state = harness.state_digest()
    off_s2b = harness.get_offset("b", "s2")
    for kwargs in (dict(stream="s2", author="b"), dict(stream="s2", author="a"), dict(stream="s", author="b")):
        with pytest.raises(ClaimScopeError):
            harness.store.append_record(guard=cs.guard(t_sa), **req(body="x", key="cross", **kwargs))
    for peer, stream in (("b", "s2"), ("a", "s2"), ("b", "s")):
        with pytest.raises(ClaimScopeError):
            harness.store.advance_offset_cas(peer, stream, 1, harness.get_offset(peer, stream).revision, guard=cs.guard(t_sa))
    with pytest.raises(StaleClaimError):  # no claim exists for (a, s2): a re-scoped token authorizes nothing
        cs.finalize_terminal(ClaimToken(t_sa.workspace_generation, t_s2b.generation, "A", "a", "s2"), {"x": 1})
    assert harness.state_digest() == state and harness.get_offset("b", "s2") == off_s2b and _final_rows(harness.db_path) == []
    # controls: each token authorizes exactly its own scope
    assert harness.store.append_record(guard=cs.guard(t_sa), **req(body="mine", key="m1")).stream_id == "s"
    assert harness.store.advance_offset_cas("b", "s2", 1, off_s2b.revision, guard=cs.guard(t_s2b)).read_through_position == 1
