"""Wave 4 CTX-001..003: bounded catch-up projection (TD-12/TD-22) and context/session loss recovery."""
import json

import pytest
from hypothesis import given, settings, strategies as st

from peerhub.extensions.bridge import ContextLostError, PrespawnError
from peerhub.extensions.catchup import CatchUpBudget, build_catch_up, project_catch_up
from tests.m1.control_helpers import bseed, ctl, delivery_rows, item_json, mem_records, msg, offset_row, payload_bytes, records, responses, sql
from tests.m1.fakes import CrashInjected, CrashInjector, FakeRuntimeTarget
from tests.m1.harness.bridge import BridgeHarness

pytestmark = [pytest.mark.context]


def _pos(p):
    return [r.position for r in p.records]


def _boundary_count(h):
    return sql(h, "SELECT COUNT(*) FROM records WHERE kind='context.boundary'")[0][0]


# ------------------------------------------------------------------ CTX-001
@pytest.mark.m1_id("CTX-001")
@pytest.mark.unit
def test_ctx_001_record_budget_keeps_ordered_suffix_and_marks_truncation():
    recs = mem_records([5] * 10)
    p = project_catch_up(recs, CatchUpBudget(max_records=3))
    assert _pos(p) == [8, 9, 10] and p.truncated is True  # literal oracle: the ordered suffix
    assert (p.omitted_count, p.omitted_through_position, p.first_position, p.last_position) == (7, 7, 8, 10)
    assert p.boundary()["truncated"] is True and p.boundary()["omitted_through_position"] == 7
    # positive control: a budget that fits everything is not truncated and says so explicitly
    full = project_catch_up(recs, CatchUpBudget(max_records=10))
    assert _pos(full) == list(range(1, 11)) and full.truncated is False and full.omitted_count == 0
    assert full.omitted_through_position is None and full.boundary()["truncated"] is False


@pytest.mark.m1_id("CTX-001")
@pytest.mark.unit
def test_ctx_001_byte_budget_exact_boundary_and_one_byte_less():
    recs = mem_records([10, 10, 10, 10, 10])
    three = payload_bytes(recs[2:])  # independent oracle: bytes of the last three items joined by commas
    assert _pos(project_catch_up(recs, CatchUpBudget(max_bytes=three))) == [3, 4, 5]  # exact fit is included
    assert _pos(project_catch_up(recs, CatchUpBudget(max_bytes=three - 1))) == [4, 5]  # one byte less drops the oldest
    p = project_catch_up(recs, CatchUpBudget(max_bytes=three))
    assert p.used_bytes == three == len(p.payload().encode("utf-8"))
    assert p.payload() == ",".join(item_json(r) for r in recs[2:])


@pytest.mark.m1_id("CTX-001")
@pytest.mark.unit
def test_ctx_001_oversized_newest_record_yields_empty_truncated_projection_not_a_gap():
    recs = mem_records([5, 5, 500])
    p = project_catch_up(recs, CatchUpBudget(max_bytes=200))
    assert _pos(p) == [] and p.truncated and p.omitted_count == 3 and p.omitted_through_position == 3  # never skips over the big one
    assert p.first_position is None and p.last_position is None
    ok = mem_records([5, 5, 5])  # control: same budget, small records fit exactly as the independent oracle says
    k = max(n for n in range(4) if payload_bytes(ok[3 - n:]) <= 200)
    small = project_catch_up(ok, CatchUpBudget(max_bytes=200))
    assert k >= 1 and len(small.records) == k and small.used_bytes == payload_bytes(ok[3 - k:])


@pytest.mark.m1_id("CTX-001")
@pytest.mark.unit
def test_ctx_001_token_budget_uses_injected_estimator():
    recs = mem_records([4, 8, 12, 16])
    est = lambda s: len(s) // 10  # noqa: E731 - deterministic abstraction
    cost = [est(item_json(r)) for r in recs]
    budget = cost[2] + cost[3]
    p = project_catch_up(recs, CatchUpBudget(max_tokens=budget, token_estimator=est))
    assert _pos(p) == [3, 4] and p.used_tokens == budget
    assert _pos(project_catch_up(recs, CatchUpBudget(max_tokens=budget - 1, token_estimator=est))) == [4]


@pytest.mark.m1_id("CTX-001")
@pytest.mark.unit
def test_ctx_001_after_and_before_position_window_is_exclusive_and_reported():
    recs = mem_records([5] * 8)
    p = project_catch_up(recs, CatchUpBudget(max_records=100), after_position=3, before_position=7)
    assert _pos(p) == [4, 5, 6] and not p.truncated and (p.after_position, p.before_position) == (3, 7)  # (after, before) exclusive
    p2 = project_catch_up(recs, CatchUpBudget(max_records=2), after_position=3, before_position=7)
    assert _pos(p2) == [5, 6] and p2.omitted_count == 1 and p2.omitted_through_position == 4


@pytest.mark.m1_id("CTX-001")
@pytest.mark.unit
@pytest.mark.parametrize("kw,err", [({"max_records": -1}, "max_records"), ({"max_bytes": True}, "max_bytes"),
                                    ({"max_records": 1.5}, "max_records"), ({"max_tokens": 5}, "token_estimator")])
def test_ctx_001_invalid_budget_is_rejected_explicitly(kw, err):
    with pytest.raises(ValueError, match=err):
        CatchUpBudget(**kw)
    CatchUpBudget(max_records=0, max_bytes=0)  # positive control: zero is a legal (empty) budget


@pytest.mark.m1_id("CTX-001")
@pytest.mark.unit
def test_ctx_001_out_of_order_or_duplicate_input_is_rejected_not_reordered():
    recs = mem_records([5, 5, 5])
    for bad in ([recs[1], recs[0], recs[2]], [recs[0], recs[0], recs[1]]):
        with pytest.raises(ValueError, match="strictly increasing"):
            project_catch_up(bad, CatchUpBudget(max_records=10))
    assert _pos(project_catch_up(recs, CatchUpBudget(max_records=10))) == [1, 2, 3]  # control


@pytest.mark.m1_id("CTX-001")
def test_ctx_001_store_projection_pages_equal_whole_read_and_start_after_offset(bridge_h):
    bseed(bridge_h, n=1)
    for i in range(1, 12):
        msg(bridge_h, f"m{i}")
    rows = records(bridge_h)  # raw-SQL oracle of durable order
    budget = CatchUpBudget(max_records=4)
    for page in (1, 3, 200):
        p = build_catch_up(bridge_h.store, "s", budget, after_position=2, before_position=11, page=page)
        assert [r.record_id for r in p.records] == [r[0] for r in rows if 2 < r[1] < 11][-4:], page
        assert p.omitted_count == 4 and p.truncated  # positions 3..10 = 8 candidates, 4 kept
    with pytest.raises(ValueError, match="page"):
        build_catch_up(bridge_h.store, "s", budget, page=0)
    assert [r.position for r in build_catch_up(bridge_h.store, "s", CatchUpBudget(max_records=100)).records] == list(range(1, 13))  # control


# ------------------------------------------------------------------ CTX-002
@pytest.mark.m1_id("CTX-002")
@pytest.mark.property
@settings(max_examples=150, deadline=None)
@given(sizes=st.lists(st.integers(0, 60), max_size=25), mr=st.one_of(st.none(), st.integers(0, 30)),
       mb=st.one_of(st.none(), st.integers(0, 600)), after=st.integers(0, 5))
def test_ctx_002_projection_never_exceeds_budget_and_preserves_order(sizes, mr, mb, after):
    recs = mem_records(sizes)
    p = project_catch_up(recs, CatchUpBudget(max_records=mr, max_bytes=mb), after_position=after)
    cands = [r for r in recs if r.position > after]
    got = [r.position for r in p.records]
    assert got == sorted(got) and len(set(got)) == len(got)  # order preserved, no duplicates
    k = len(got)
    assert got == [r.position for r in cands[len(cands) - k:]]  # a contiguous ordered SUFFIX (no gaps, no reordering)
    if mr is not None:
        assert k <= mr
    if mb is not None:
        assert len(",".join(item_json(r) for r in p.records).encode()) <= mb  # serialized size, independent oracle
        assert len(p.payload().encode()) <= mb
    if k < len(cands):  # maximality: one more (older) Record would break a bound
        older = cands[len(cands) - k - 1:]
        assert (mr is not None and len(older) > mr) or (mb is not None and payload_bytes(older) > mb)
    assert p.omitted_count == len(cands) - k and p.truncated == (k < len(cands))
    assert p.used_records == k


@pytest.mark.m1_id("CTX-002")
@pytest.mark.property
@pytest.mark.parametrize("budget_kw,expect", [({"max_records": 0}, []), ({"max_bytes": 0}, []), ({"max_records": 1}, [3]),
                                              ({"max_bytes": 1}, [])])
def test_ctx_002_zero_and_one_boundaries(budget_kw, expect):
    recs = mem_records([0, 0, 0])  # smallest possible items are still > 1 byte
    assert _pos(project_catch_up(recs, CatchUpBudget(**budget_kw))) == expect


# ------------------------------------------------------------------ CTX-003
@pytest.mark.m1_id("CTX-003")
def test_ctx_003_context_loss_recovers_with_bounded_catch_up_and_boundary_record(tmp_path):
    h = BridgeHarness(tmp_path / "ws", catch_up_budget=CatchUpBudget(max_records=1))
    bseed(h, n=1)
    rt = FakeRuntimeTarget()
    assert h.delivery_cycle("b", "s", rt).status == "delivered"
    reds = [ctl(h, "control.redirect", f"rd{i}", body={"goal": "g", "instruction": f"r{i}"}) for i in range(3)]  # unseen intent, consumed by the Offset
    msg(h, "ok")
    assert h.delivery_cycle("b", "s", rt).status == "delivered" and _boundary_count(h) == 0  # control: no loss, no boundary
    rt.lose_context()  # provider/session loss; durable Stream/Offset intact
    last = msg(h, "after-loss")
    res = h.delivery_cycle("b", "s", rt)
    assert res.status == "delivered" and res.session_generation == 2
    rows = records(h)
    pos = [r[1] for r in rows if r[0] == last.record_id][0]
    before = [r[0] for r in rows if r[1] < pos]  # D-W4-8b: bootstrap = ordered history suffix of the Stream within the budget
    meta = rt.catch_up_meta[-1]
    assert rt.catch_ups[-1] == [reds[-1].record_id] and meta["after_position"] == 0 and meta["before_position"] == pos  # pinned redirect intent first
    assert meta["mode"] == "fresh_generation" and meta["budget"]["max_records"] == 1 and meta["session_generation"] == 2
    assert meta["truncated"] is True and meta["omitted_count"] == len(before) - 1 and meta["included_count"] == 1 and meta["pinned_count"] == 1
    (b,) = [r for r in rows if r[3] == "context.boundary"]  # durable boundary Record carries the same metadata
    body = json.loads(b[4])
    assert b[2] == "b" and body["session_generation"] == 2 and body["reason"] == "missing"
    assert body["catch_up"] == {k: meta[k] for k in body["catch_up"]}
    assert len(responses(h)) == 3 and offset_row(h)[0] >= pos and len(reds) == 3
    msg(h, "next")  # established session: no catch-up, no second boundary
    assert h.delivery_cycle("b", "s", rt).status == "delivered" and rt.catch_ups[-1] == []
    assert _boundary_count(h) == 1


@pytest.mark.m1_id("CTX-003")
def test_ctx_003_unseen_redirects_survive_loss_within_budget(tmp_path):
    h = BridgeHarness(tmp_path / "ws", catch_up_budget=CatchUpBudget(max_records=2))
    bseed(h, n=1)
    rt = FakeRuntimeTarget()
    assert h.delivery_cycle("b", "s", rt).status == "delivered"
    reds = [ctl(h, "control.redirect", f"rd{i}", body={"goal": "g", "instruction": f"r{i}"}) for i in range(3)]
    rt.lose_context()
    last = msg(h, "after-loss")
    assert h.delivery_cycle("b", "s", rt).status == "delivered"
    assert rt.catch_ups[-1] == [r.record_id for r in reds[1:]]  # bounded ordered suffix of the unseen intent (oracle: literal)
    meta = rt.catch_up_meta[-1]
    assert meta["truncated"] is True and meta["omitted_count"] == 3 and meta["omitted_through_position"] == reds[0].position  # m0, response, red0
    assert last.position > reds[-1].position


@pytest.mark.m1_id("CTX-003")
def test_ctx_003_provider_reports_context_loss_during_delivery_retries_safely_on_fresh_generation(bridge_h):
    bseed(bridge_h, n=1)
    rt = FakeRuntimeTarget()
    rt.script_deliver(("context_lost", "context window exceeded"))
    r1 = bridge_h.delivery_cycle("b", "s", rt)
    assert r1.status == "failed_not_started" and r1.certainty == "NOT_STARTED"  # adapter-reported pre-run loss: explicit, safe to retry
    assert bridge_h.current_mapping("b", "s")["state"] == "LOST"
    assert responses(bridge_h) == [] and offset_row(bridge_h)[0] == 0
    r2 = bridge_h.delivery_cycle("b", "s", rt)  # recovery: fresh generation, same delivery record, runs once
    assert r2.status == "delivered" and r2.session_generation == 2 and r2.certainty == "TERMINAL"
    assert [c[0] for c in rt.calls].count("deliver") == 2 and len(responses(bridge_h)) == 1
    assert [d[6] for d in delivery_rows(bridge_h)] == [1]  # one attempt row: no blind replay was needed


@pytest.mark.m1_id("CTX-003")
def test_ctx_003_crash_after_fresh_generation_before_delivery_still_gets_catch_up_once(tmp_path):
    crash = CrashInjector()
    h = BridgeHarness(tmp_path / "ws", fault_hook=crash)
    bseed(h, n=1)
    rt = FakeRuntimeTarget()
    assert h.delivery_cycle("b", "s", rt).status == "delivered"
    msg(h, "u2")
    rt.lose_context()
    crash.arm("bridge.after_session_resolved")
    with pytest.raises(CrashInjected):
        h.delivery_cycle("b", "s", rt)  # fresh generation 2 persisted, runtime never delivered to
    assert crash.fired == ["bridge.after_session_resolved"]
    assert sql(h, "SELECT session_generation FROM bridge_sessions") == [(2,)] and [c[0] for c in rt.calls].count("deliver") == 1
    h2 = BridgeHarness(tmp_path / "ws", h.clock)  # restart
    res = h2.delivery_cycle("b", "s", rt)
    assert res.status == "delivered" and res.session_generation == 2
    assert rt.catch_up_meta[-1]["mode"] == "fresh_generation" and rt.catch_up_meta[-1]["session_generation"] == 2  # catch-up still built after the crash
    assert _boundary_count(h2) == 1  # idempotent boundary: exactly one
    msg(h2, "u3")
    assert h2.delivery_cycle("b", "s", rt).status == "delivered" and rt.catch_ups[-1] == []


@pytest.mark.m1_id("CTX-003")
def test_ctx_003_context_loss_error_type_is_a_prespawn_error():
    assert issubclass(ContextLostError, PrespawnError)


# ------------------------------------------------------------------ pinned selection (pure projection)
@pytest.mark.m1_id("CTX-001")
@pytest.mark.unit
def test_ctx_001_pinned_records_are_reserved_first_newest_first_and_output_stays_ordered():
    recs = mem_records([5] * 10)
    pin = lambda r: r.position in (1, 2, 3)  # noqa: E731 - early intent
    p = project_catch_up(recs, CatchUpBudget(max_records=4), pin=pin)
    assert _pos(p) == [1, 2, 3, 10] and p.pinned_count == 3  # all three pins reserved, the remaining slot takes the newest other
    assert p.truncated and p.omitted_count == 6 and p.omitted_through_position == 9 and p.boundary()["pinned_count"] == 3
    # budget smaller than the pins: pins alone, newest first
    assert _pos(project_catch_up(recs, CatchUpBudget(max_records=2), pin=pin)) == [2, 3]
    # control: no pin predicate = the plain contiguous suffix
    assert _pos(project_catch_up(recs, CatchUpBudget(max_records=4))) == [7, 8, 9, 10]
    # pinned Records at/below after_position stay candidates; non-pinned there do not
    assert _pos(project_catch_up(recs, CatchUpBudget(max_records=10), after_position=5, pin=pin)) == [1, 2, 3, 6, 7, 8, 9, 10]


@pytest.mark.m1_id("CTX-002")
@pytest.mark.property
@settings(max_examples=100, deadline=None)
@given(sizes=st.lists(st.integers(0, 40), min_size=1, max_size=20), pins=st.sets(st.integers(1, 20), max_size=6),
       mr=st.one_of(st.none(), st.integers(0, 12)), mb=st.one_of(st.none(), st.integers(0, 500)))
def test_ctx_002_pinned_projection_never_exceeds_budget_and_stays_ordered(sizes, pins, mr, mb):
    recs = mem_records(sizes)
    p = project_catch_up(recs, CatchUpBudget(max_records=mr, max_bytes=mb), pin=lambda r: r.position in pins)
    got = [r.position for r in p.records]
    assert got == sorted(set(got))
    if mr is not None:
        assert len(got) <= mr
    if mb is not None:
        assert len(",".join(item_json(r) for r in p.records).encode()) <= mb
    assert p.omitted_count == len(recs) - len(got) and p.pinned_count == len([g for g in got if g in pins])
    kept_pins = [g for g in got if g in pins]
    omitted_pins = [x for x in pins if x <= len(recs) and x not in got]
    if omitted_pins and kept_pins:  # pins are kept newest-first: every kept pin is newer than every omitted pin
        assert min(kept_pins) > max(omitted_pins)
    # independent oracle (not derived from the implementation): which Records MUST be kept
    valid = sorted(x for x in pins if 1 <= x <= len(recs))
    if mb is None:  # record budget only: pins reserved newest-first, remaining slots take the newest non-pinned Records
        k = len(recs) if mr is None else mr
        want_pins = valid[::-1][:k]
        others = [i for i in range(len(recs), 0, -1) if i not in valid][:max(0, k - len(want_pins))]
        assert got == sorted(want_pins + others)
    else:  # byte budget: the newest pin (reserved first) must be kept whenever it fits alone and a record slot exists
        newest = valid[-1] if valid else None
        if newest is not None and (mr is None or mr >= 1) and len(item_json(recs[newest - 1]).encode()) <= mb:
            assert newest in got
        if not valid and recs and (mr is None or mr >= 1) and len(item_json(recs[-1]).encode()) <= mb:
            assert got  # an always-empty projection is wrong whenever something fits
