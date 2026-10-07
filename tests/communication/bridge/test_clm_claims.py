"""Wave 3 CLM-001..003: claim renewal, takeover and exact-expiry (TD-04, TD-19, TD-25) with ManualClock, no sleeps."""
import threading

import pytest

from peerhub.extensions.bridge_claims import ClaimHeldError, ClaimToken, StaleClaimError
from tests.communication.bridge_helpers import bseed, claim_row, delivery_rows, kinds, more_stream, offset_row, records
from tests.communication.fakes import FakeRuntimeTarget
from tests.communication.harness.mp import run_threads


@pytest.mark.unit
@pytest.mark.claim
@pytest.mark.catalog_id("CLM-001")
def test_clm_001_renew_before_expiry_extends_same_generation(bridge_h):
    bseed(bridge_h)
    t0 = bridge_h.clock.now()
    tok = bridge_h.acquire_claim("b", "s", "A", lease_sec=30)
    assert claim_row(bridge_h) == ("A", 1, t0 + 30)  # oracle: literal arithmetic on the ManualClock start
    bridge_h.clock.advance(29.5)  # T - epsilon
    renewed = bridge_h.renew_claim(tok)
    assert renewed == tok and renewed.generation == 1
    row = claim_row(bridge_h)
    assert row[:2] == ("A", 1) and row[2] == pytest.approx(t0 + 29.5 + 30, abs=1e-9)  # extended deterministically, G unchanged
    bridge_h.clock.advance(1.0)  # past the ORIGINAL expiry: only the renewal keeps the token alive
    assert bridge_h.renew_claim(tok).generation == 1


@pytest.mark.unit
@pytest.mark.claim
@pytest.mark.catalog_id("CLM-001")
def test_clm_001_renew_rejected_when_expired_or_not_owner_and_state_unchanged(bridge_h):
    bseed(bridge_h)
    tok = bridge_h.acquire_claim("b", "s", "A", lease_sec=30)
    before = claim_row(bridge_h)
    forged = ClaimToken(tok.workspace_generation, tok.generation, "B", tok.peer_id, tok.stream_id)
    with pytest.raises(StaleClaimError):
        bridge_h.renew_claim(forged)
    wrong_gen = ClaimToken(tok.workspace_generation, tok.generation + 1, "A", tok.peer_id, tok.stream_id)
    with pytest.raises(StaleClaimError):
        bridge_h.renew_claim(wrong_gen)
    assert claim_row(bridge_h) == before
    bridge_h.clock.advance(10)  # positive control: the real token renews while live
    assert bridge_h.renew_claim(tok) == tok
    expiry = claim_row(bridge_h)[2]
    bridge_h.clock.set(expiry)  # exactly expires_at: expired (TD-19), even the owner cannot renew
    with pytest.raises(StaleClaimError):
        bridge_h.renew_claim(tok)
    assert claim_row(bridge_h) == ("A", 1, expiry)


@pytest.mark.concurrency
@pytest.mark.claim
@pytest.mark.catalog_id("CLM-002")
def test_clm_002_takeover_before_expiry_rejected_under_race(bridge_h):
    bseed(bridge_h)
    t0 = bridge_h.clock.now()
    a = bridge_h.acquire_claim("b", "s", "A", lease_sec=30)
    bridge_h.clock.set(t0 + 30 - 0.001)  # T - epsilon
    before = claim_row(bridge_h)
    barrier = threading.Barrier(4, timeout=30)

    def contender(owner):
        def run():
            cs = bridge_h.new_bridge(owner).claims
            barrier.wait()
            return cs.acquire("b", "s", owner, 30)
        return run

    res = run_threads([contender(o) for o in ("B", "C", "D", "E")])
    assert all(isinstance(r, ClaimHeldError) for r in res), res
    assert claim_row(bridge_h) == before and before[:2] == ("A", 1)  # A remains owner, generation unchanged
    assert bridge_h.heartbeat(a) == a  # A's token is still valid
    # positive control: at the boundary the very same takeover succeeds
    bridge_h.clock.set(claim_row(bridge_h)[2])
    assert bridge_h.acquire_claim("b", "s", "B", 30).generation == 2


@pytest.mark.concurrency
@pytest.mark.claim
@pytest.mark.catalog_id("CLM-003")
def test_clm_003_exact_expiry_heartbeat_vs_takeover_race(bridge_h):
    """TD-19: expired when now >= expires_at. At exactly T the heartbeat fails and takeover wins, in every interleaving."""
    bseed(bridge_h)
    streams = ["s"] + [f"s{i}" for i in range(1, 16)]
    for st in streams[1:]:
        more_stream(bridge_h, st)
    for rnd, st in enumerate(streams):
        bridge_h.clock.set(5000.0 + rnd * 1000)
        t0 = bridge_h.clock.now()
        a = bridge_h.acquire_claim("b", st, "A", 30)
        bridge_h.clock.set(t0 + 30)  # exactly expires_at
        barrier = threading.Barrier(2, timeout=30)
        cs_a, cs_b = bridge_h.new_bridge("A").claims, bridge_h.new_bridge("B").claims

        def hb():
            barrier.wait()
            return cs_a.heartbeat(a)

        def take():
            barrier.wait()
            return cs_b.acquire("b", st, "B", 30)

        r_hb, r_take = run_threads([hb, take])
        assert isinstance(r_hb, StaleClaimError), (rnd, r_hb)
        assert not isinstance(r_take, Exception) and r_take.generation == 2 and r_take.owner_id == "B", (rnd, r_take)
        assert claim_row(bridge_h, st) == ("B", 2, t0 + 30 + 30)
        # afterwards only the current generation can append/ack/finalize (TD-25); the old token changes nothing
        before_recs, before_off = records(bridge_h, st), offset_row(bridge_h, "b", st)
        with pytest.raises(StaleClaimError):
            bridge_h.store.append_record(guard=bridge_h.cs.guard(a), stream_id=st, author_peer_id="b", kind="response",
                                         body="late", created_at="2026-10-01T00:00:00Z", idempotency_key="late")
        with pytest.raises(StaleClaimError):
            bridge_h.store.advance_offset_cas("b", st, 1, before_off[1], guard=bridge_h.cs.guard(a))
        with pytest.raises(StaleClaimError):
            bridge_h.cs.finalize_terminal(a, {"x": 1})
        assert records(bridge_h, st) == before_recs and offset_row(bridge_h, "b", st) == before_off
        assert bridge_h.store.advance_offset_cas("b", st, 1, before_off[1], guard=bridge_h.cs.guard(r_take)).read_through_position == 1
    # control: one tick BEFORE the boundary the heartbeat wins and the takeover loses
    more_stream(bridge_h, "sx")
    bridge_h.clock.set(90000.0)
    a = bridge_h.acquire_claim("b", "sx", "A", 30)
    bridge_h.clock.set(90000.0 + 30 - 1e-3)
    assert bridge_h.heartbeat(a) == a
    with pytest.raises(ClaimHeldError):
        bridge_h.acquire_claim("b", "sx", "B", 30)


@pytest.mark.concurrency
@pytest.mark.claim
@pytest.mark.catalog_id("CLM-003")
def test_clm_003_stale_owner_mid_delivery_is_fenced_and_late_terminal_is_evidence_only(bridge_h):
    """TD-25 through the Bridge: A loses its claim mid-delivery; nothing authoritative is written for A afterwards."""
    (rec,) = bseed(bridge_h)
    rt = FakeRuntimeTarget()
    holder = {}

    def lose_claim():
        bridge_h.clock.advance(31)  # A's lease lapses ...
        holder["b"] = bridge_h.acquire_claim("b", "s", "B", 30)  # ... and B takes over (generation 2)

    rt.script_deliver(("started", "exec-1"), ("call", lose_claim), ("output", "late chunk"), ("terminal", {"response": "stale"}))
    res = bridge_h.delivery_cycle("b", "s", rt)
    assert res.status == "fenced" and res.certainty == "STARTED"
    assert len(records(bridge_h)) == 1 and offset_row(bridge_h) == (0, 1)  # no response Record, no ack
    before = delivery_rows(bridge_h, rec.record_id)
    assert len(before) == 1
    stale_a = ClaimToken(bridge_h.workspace_generation(), 1, "bridge-1", "b", "s")
    with pytest.raises(StaleClaimError):
        bridge_h.finalize_terminal(stale_a, {"response": "stale"})
    assert delivery_rows(bridge_h, rec.record_id) == before  # delivery row untouched
    assert len(records(bridge_h)) == 1 and offset_row(bridge_h) == (0, 1)
    ev = kinds(bridge_h)
    assert "fenced_mid_delivery" in ev and "stale_terminal_callback" in ev  # late callbacks became evidence
    assert "terminal" not in ev
    # positive control: the current generation (B) may finalize
    out = bridge_h.finalize_terminal(holder["b"], {"response": "from B"})
    assert out.outcome == "first" and len(records(bridge_h)) == 2 and offset_row(bridge_h) == (1, 2)


@pytest.mark.concurrency
@pytest.mark.claim
@pytest.mark.catalog_id("CLM-003")
@pytest.mark.parametrize("point,resp_before,ack_before", [
    ("bridge.after_terminal_evidence_before_response_append", 0, 0),
    ("bridge.after_terminal_evidence_before_offset_ack", 1, 0),
])
def test_clm_003_claim_lost_between_terminal_evidence_and_completion_fences_each_write(tmp_path, point, resp_before, ack_before):
    """TD-25: response append and Offset ack are each fenced; the claim lapses exactly between the committed steps."""
    from tests.communication.harness.bridge import BridgeHarness

    state = {}

    def hook(p):
        if p == point and "armed" not in state:
            state["armed"] = True
            h.clock.advance(31)
            state["b"] = h.acquire_claim("b", "s", "B", 30)  # B takes over (generation 2)

    h = BridgeHarness(tmp_path / "ws", fault_hook=hook)
    (rec,) = bseed(h)
    with pytest.raises(StaleClaimError):
        h.delivery_cycle("b", "s", FakeRuntimeTarget())
    from tests.communication.bridge_helpers import responses
    assert len(responses(h)) == resp_before and offset_row(h) == (0, 1)  # the fenced write did not happen
    assert delivery_rows(h, rec.record_id)[0][7] == "TERMINAL"  # terminal truth was committed before the claim lapsed
    # positive control: the CURRENT generation completes the same delivery without re-running the runtime
    rt = FakeRuntimeTarget()
    res = h.new_bridge("B").delivery_cycle("b", "s", rt)
    assert res.status == "recovered_terminal" and rt.calls == []
    assert len(responses(h)) == 1 and offset_row(h) == (rec.position, 2)
