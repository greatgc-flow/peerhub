"""Wave 1: STR-001..006, OFF-001..008."""
import threading

import pytest

from peerhub.m1.store import (
    CasMismatchError, IllegalTransitionError, OffsetBeyondHeadError, OffsetRegressionError,
    StreamClosedError, UnknownReferenceError,
)
from peerhub.m1.wire import WireValidationError, parse_wire
from tests.m1.helpers import append_n, req, seed
from tests.m1.spec import errors, schema, valid_objects


@pytest.mark.m1_id("STR-001")
def test_str_001_open_closes_with_current_revision(harness):
    s = seed(harness)
    n = s.revision
    closed = harness.cas_stream("s", n, {"state": "CLOSED"})
    assert closed.state.value == "CLOSED" and closed.revision == n + 1
    assert harness.reopen().get_stream("s") == closed and harness.get_stream("s").members == ["a", "b"]
    with pytest.raises(IllegalTransitionError):  # reopen is not an M1 API (TD-09)
        harness.cas_stream("s", n + 1, {"state": "OPEN"})
    assert harness.get_stream("s").revision == n + 1


@pytest.mark.m1_id("STR-002")
def test_str_002_stale_revision_loses_without_mutation(harness):
    s = seed(harness)
    harness.create_peer({"peer_id": "c"})
    won = harness.cas_stream("s", s.revision, {"members": ["a", "b", "c"]})
    digest = harness.state_digest()
    for mutation in ({"members": ["a"]}, {"state": "CLOSED"}, {"title": "t"}):
        with pytest.raises(CasMismatchError):
            harness.cas_stream("s", s.revision, mutation)
    assert harness.get_stream("s") == won and won.revision == s.revision + 1 and harness.state_digest() == digest


@pytest.mark.m1_id("STR-003")
def test_str_003_closed_stream_rejects_append_atomically(harness):
    seed(harness)
    harness.create_stream({"stream_id": "c", "state": "CLOSED", "members": ["a", "b"]})
    digest, counts = harness.state_digest(), harness.row_counts()
    with pytest.raises(StreamClosedError):
        harness.append_record(req("x", key="fresh", stream="c"))
    assert harness.state_digest() == digest and harness.row_counts() == counts
    # closing an open stream with history also blocks appends, history intact
    harness.append_record(req("x", key="one"))
    harness.cas_stream("s", 1, {"state": "CLOSED"})
    digest = harness.state_digest()
    with pytest.raises(StreamClosedError):
        harness.append_record(req("y", key="two"))
    assert harness.state_digest() == digest and len(harness.read_records("s")) == 1
    assert harness.append_record(req("x", key="one")).position == 1  # retry of an already committed append still resolves


@pytest.mark.m1_id("STR-004")
def test_str_004_concurrent_cas_has_one_winner(harness):
    s = seed(harness)
    harness.create_peer({"peer_id": "c"})
    muts = [{"state": "CLOSED"}, {"members": ["a", "b", "c"]}]
    barrier, out = threading.Barrier(2), []

    def run(m):
        barrier.wait()
        try:
            out.append(("ok", harness.cas_stream("s", s.revision, m)))
        except CasMismatchError as e:
            out.append(("lost", e))

    ts = [threading.Thread(target=run, args=(m,)) for m in muts]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert sorted(k for k, _ in out) == ["lost", "ok"]
    final = harness.get_stream("s")
    assert final.revision == s.revision + 1 and final == next(v for k, v in out if k == "ok")


@pytest.mark.m1_id("STR-005")
def test_str_005_members_reject_duplicates_and_null_empty_allowed(harness):
    base = valid_objects()["stream"]
    for bad in (["p-1", "p-1"], [None], ["p-1", None], None, [""]):
        assert errors("stream", {**base, "members": bad}), bad
        with pytest.raises(WireValidationError):
            parse_wire("stream", {**base, "members": bad})
    # declared policy (schema has no minItems): an empty members array is valid and persists
    assert errors("stream", {**base, "members": []}) == []
    assert "minItems" not in schema("stream")["properties"]["members"]
    harness.persist_wire("stream", {**base, "members": []})
    assert harness.get_stream("s-1").members == []


@pytest.mark.m1_id("STR-006")
def test_str_006_member_integrity_and_order_independent_of_member_array(harness):
    harness.create_peer({"peer_id": "A"})
    harness.create_peer({"peer_id": "B"})
    digest = harness.state_digest()
    with pytest.raises(UnknownReferenceError):
        harness.create_stream({"stream_id": "bad", "members": ["A", "ghost"]})
    assert harness.state_digest() == digest and harness.get_stream("bad") is None
    s = harness.create_stream({"stream_id": "s", "members": ["B", "A"]})
    harness.append_record(req(1, key="1", author="A"))
    harness.append_record(req(2, key="2", author="B"))
    harness.append_record(req(3, key="3", author="A"))
    assert [r.author_peer_id for r in harness.read_records("s")] == ["A", "B", "A"]  # position order, not [B, A]
    assert harness.get_stream("s").members == s.members == ["B", "A"]
    digest = harness.state_digest()
    with pytest.raises(UnknownReferenceError):
        harness.cas_stream("s", 1, {"members": ["A", "ghost"]})
    assert harness.state_digest() == digest


@pytest.mark.m1_id("OFF-001")
def test_off_001_initial_offset_is_effective_zero(harness):
    seed(harness)
    digest = harness.state_digest()
    off = harness.get_offset("a", "s")
    assert (off.read_through_position, off.revision) == (0, 1)
    assert harness.row_counts()["offsets"] == 0 and harness.state_digest() == digest  # read creates nothing


def _offset_at(h, pos_seq):
    off = h.get_offset("a", "s")
    for p in pos_seq:
        off = h.cas_offset("a", "s", off.revision, p)
    return off


@pytest.mark.m1_id("OFF-002")
def test_off_002_cas_succeeds_with_current_revision(harness):
    seed(harness)
    append_n(harness, 6)
    cur = _offset_at(harness, [1, 2])
    assert (cur.read_through_position, cur.revision) == (2, 3)
    new = harness.cas_offset("a", "s", 3, 5)
    assert (new.read_through_position, new.revision) == (5, 4)
    assert harness.reopen().get_offset("a", "s") == new


@pytest.mark.m1_id("OFF-003")
def test_off_003_stale_cas_fails_without_mutation(harness):
    seed(harness)
    append_n(harness, 6)
    cur = _offset_at(harness, [1, 2, 3])
    assert cur.revision == 4
    digest = harness.state_digest()
    with pytest.raises(CasMismatchError):
        harness.cas_offset("a", "s", 3, 5)
    assert harness.get_offset("a", "s") == cur and harness.state_digest() == digest


@pytest.mark.m1_id("OFF-004")
def test_off_004_offset_cannot_move_backward(harness):
    seed(harness)
    append_n(harness, 10)
    cur = _offset_at(harness, [10])
    digest = harness.state_digest()
    with pytest.raises(OffsetRegressionError):
        harness.cas_offset("a", "s", cur.revision, 9)
    assert harness.get_offset("a", "s") == cur and harness.state_digest() == digest
    eq = harness.cas_offset("a", "s", cur.revision, 10)  # equal is allowed (TD-03) and is one accepted write
    assert (eq.read_through_position, eq.revision) == (10, cur.revision + 1)


@pytest.mark.m1_id("OFF-005")
def test_off_005_offset_does_not_mark_work_complete(harness):
    seed(harness)
    append_n(harness, 3)
    before = harness.table_digests()
    harness.cas_offset("a", "s", 1, 2)
    after = harness.table_digests()
    assert {t for t in before if before[t] != after[t]} == {"offsets"}
    assert set(after) == {"peers", "streams", "stream_members", "records", "offsets"}
    assert harness.row_counts()["offsets"] == 1


@pytest.mark.m1_id("OFF-006")
def test_off_006_offset_cannot_pass_head(harness):
    seed(harness)
    append_n(harness, 4)
    cur = _offset_at(harness, [3])
    digest = harness.state_digest()
    with pytest.raises(OffsetBeyondHeadError):
        harness.cas_offset("a", "s", cur.revision, 5)
    assert harness.get_offset("a", "s") == cur and harness.state_digest() == digest
    assert harness.cas_offset("a", "s", cur.revision, 4).read_through_position == 4  # == head allowed


@pytest.mark.m1_id("OFF-007")
def test_off_007_empty_stream_offset_zero_and_no_positive_jump(harness):
    seed(harness)
    assert harness.get_offset("a", "s").read_through_position == 0
    digest = harness.state_digest()
    with pytest.raises(OffsetBeyondHeadError):
        harness.cas_offset("a", "s", 1, 1)
    assert harness.state_digest() == digest and harness.row_counts()["offsets"] == 0
    harness.append_record(req("x"))
    assert harness.cas_offset("a", "s", 1, 1).read_through_position == 1


@pytest.mark.m1_id("OFF-008")
def test_off_008_offsets_isolated_by_exact_peer_stream_key(harness):
    seed(harness)
    harness.create_stream({"stream_id": "y", "members": ["a", "b"]})
    for st in ("s", "y"):
        append_n(harness, 3, stream=st, prefix=f"{st}-")
    harness.cas_offset("a", "s", 1, 2)
    harness.cas_offset("b", "y", 1, 3)
    keys = [(p, st) for p in ("a", "b") for st in ("s", "y")]
    snap = {k: harness.get_offset(*k) for k in keys}
    harness.cas_offset("a", "s", 2, 3)
    assert harness.get_offset("a", "s").read_through_position == 3
    assert all(harness.get_offset(*k) == snap[k] for k in keys if k != ("a", "s"))
    digest = harness.state_digest()
    for p, st in (("ghost", "s"), ("a", "ghost"), ("ghost", "ghost")):
        with pytest.raises(UnknownReferenceError):
            harness.cas_offset(p, st, 1, 0)
        with pytest.raises(UnknownReferenceError):
            harness.get_offset(p, st)
    assert harness.state_digest() == digest
