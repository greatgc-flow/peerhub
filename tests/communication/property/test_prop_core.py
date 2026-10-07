"""Wave 1 property tests: PROP-001..006, 009..011 (hypothesis)."""
import json
import uuid

import pytest
from hypothesis import given, settings, strategies as st

from peerhub.core.models import canonical_json_bytes, compute_payload_digest, compute_record_digest
from peerhub.core.store import CasMismatchError, IdempotencyConflictError, OffsetBeyondHeadError, OffsetRegressionError
from tests.communication.harness import CoreHarness

pytestmark = pytest.mark.property
SET = settings(max_examples=40, deadline=None)

_H: dict = {}


def shared(tmp_path_factory, name):
    """One workspace per test function; each example uses a fresh stream id (keeps hypothesis fast)."""
    if name not in _H:
        h = CoreHarness(tmp_path_factory.mktemp(name) / "ws")
        for p in ("a", "b"):
            h.create_peer({"peer_id": p})
        _H[name] = h
    return _H[name]


def new_stream(h, state="OPEN"):
    sid = f"s-{uuid.uuid4().hex[:10]}"
    h.create_stream({"stream_id": sid, "members": ["a", "b"], "state": state})
    return sid


TEXT = st.text(alphabet=st.characters(blacklist_categories=("Cs",)), max_size=12)
JSON = st.recursive(
    st.none() | st.booleans() | st.integers(-10**12, 10**12) | st.floats(allow_nan=False, allow_infinity=False) | TEXT,
    lambda c: st.lists(c, max_size=4) | st.dictionaries(TEXT, c, max_size=4), max_leaves=10)
STAMP = st.sampled_from(["2026-10-01T00:00:00Z", "2026-10-02T12:30:00+09:00", "2026-10-03T00:00:00.123Z"])
FIELDS = st.fixed_dictionaries({
    "kind": st.sampled_from(["message", "control.pause", "context.boundary", "x-y_z.1"]),
    "body": JSON,
    "targets": st.lists(st.sampled_from(["a", "b", "c", "d"]), unique=True, max_size=3),
    "reply_to": st.none() | st.sampled_from(["r1", "r2"]),
    "refs": st.lists(st.sampled_from(["r1", "r2", "r3"]), max_size=3),
    "metadata": st.dictionaries(TEXT, JSON, max_size=3),
    "created_at": STAMP,
})


def request(sid, key, fields, author="a"):
    return {"stream_id": sid, "author_peer_id": author, "idempotency_key": key, **fields}  # fields carry created_at


@pytest.mark.catalog_id("PROP-001")
@SET
@given(fields=FIELDS, n=st.integers(1, 30))
def test_prop_001_repeated_retries_collapse_to_one_record(tmp_path_factory, fields, n):
    h = shared(tmp_path_factory, "p1")
    sid = new_stream(h)
    results = [h.append_record(request(sid, "K", fields)) for _ in range(n)]
    assert len(h.read_records(sid)) == 1
    assert len({r.record_id for r in results}) == 1 and all(r == results[0] for r in results)


def _mutate(field, fields):
    f = {**fields}
    if field == "kind":
        f["kind"] = "zz-other" if fields["kind"] != "zz-other" else "message"
    elif field == "body":
        f["body"] = {"changed": fields["body"]}
    elif field == "targets":
        f["targets"] = fields["targets"] + ["zz"]
    elif field == "reply_to":
        f["reply_to"] = "changed" if fields["reply_to"] != "changed" else None
    elif field == "refs":
        f["refs"] = fields["refs"] + ["zz"]
    elif field == "metadata":
        f["metadata"] = {**fields["metadata"], "zz-extra": 1}
    elif field == "created_at":
        f["created_at"] = "2030-01-01T00:00:00Z"
    return f


@pytest.mark.catalog_id("PROP-002")
@SET
@given(fields=FIELDS, field=st.sampled_from(["kind", "body", "targets", "reply_to", "refs", "metadata", "created_at"]))
def test_prop_002_any_semantic_change_conflicts_under_same_key(tmp_path_factory, fields, field):
    h = shared(tmp_path_factory, "p2")
    sid = new_stream(h)
    first = h.append_record(request(sid, "K", fields))
    with pytest.raises(IdempotencyConflictError):
        h.append_record(request(sid, "K", _mutate(field, fields)))
    assert h.read_records(sid) == [first]


@pytest.mark.catalog_id("PROP-002")
@SET
@given(fields=FIELDS)
def test_prop_002_author_and_stream_change_are_outside_scope_td20(tmp_path_factory, fields):
    """TD-20 scope is (stream, author, key): changing author/stream_id selects another scope -> independent record, digest differs."""
    h = shared(tmp_path_factory, "p2b")
    s1, s2 = new_stream(h), new_stream(h)
    first = h.append_record(request(s1, "K", fields))
    other_author = h.append_record(request(s1, "K", fields, author="b"))
    other_stream = h.append_record(request(s2, "K", fields))
    assert len({first.record_id, other_author.record_id, other_stream.record_id}) == 3
    assert len({first.payload_digest, other_author.payload_digest, other_stream.payload_digest}) == 3
    assert h.append_record(request(s1, "K", fields)) == first


def _shuffled(o, rnd):
    if isinstance(o, dict):
        keys = list(o)
        rnd.shuffle(keys)
        return {k: _shuffled(o[k], rnd) for k in keys}
    if isinstance(o, list):
        return [_shuffled(x, rnd) for x in o]
    return o


@pytest.mark.catalog_id("PROP-003")
@SET
@given(body=JSON, seed=st.randoms(use_true_random=False))
def test_prop_003_canonical_json_map_ordering_is_stable(body, seed):
    other = _shuffled(body, seed)
    assert canonical_json_bytes(body) == canonical_json_bytes(other)
    base = {"stream_id": "s", "author_peer_id": "a", "kind": "message", "created_at": "2026-10-01T00:00:00Z", "metadata": {"m": body}}
    assert compute_record_digest({**base, "body": body}) == compute_record_digest({**base, "body": other})
    assert compute_payload_digest("message", body) == compute_payload_digest("message", other)


@pytest.mark.catalog_id("PROP-004")
@SET
@given(bodies=st.lists(JSON, min_size=1, max_size=12))
def test_prop_004_positions_strictly_increasing(tmp_path_factory, bodies):
    h = shared(tmp_path_factory, "p4")
    sid = new_stream(h)
    pos = [h.append_record({"created_at": "2026-10-01T00:00:00Z", "stream_id": sid, "author_peer_id": "a", "kind": "message", "body": b,
                            "idempotency_key": f"k{i}"}).position for i, b in enumerate(bodies)]
    assert pos == sorted(set(pos)) and all(y > x for x, y in zip(pos, pos[1:])) and pos[0] >= 1
    assert [r.position for r in h.read_records(sid)] == pos


def _stream_with_records(h, n):
    sid = new_stream(h)
    for i in range(n):
        h.append_record({"created_at": "2026-10-01T00:00:00Z", "stream_id": sid, "author_peer_id": "a", "kind": "message", "body": i, "idempotency_key": f"k{i}"})
    return sid


@pytest.mark.catalog_id("PROP-005")
@SET
@given(ops=st.lists(st.tuples(st.booleans(), st.integers(0, 8)), min_size=1, max_size=15))
def test_prop_005_offset_revision_increments_only_on_accepted_cas(tmp_path_factory, ops):
    h = shared(tmp_path_factory, "p5")
    sid = _stream_with_records(h, 8)
    accepted_n = 0
    head = h.read_records(sid)[-1].position
    for fresh, pos in ops:
        before = h.get_offset("a", sid)
        expected = before.revision if fresh else before.revision + 7
        should_accept = fresh and before.read_through_position <= pos <= head  # decided BEFORE the CAS
        if should_accept:
            after = h.cas_offset("a", sid, expected, pos)  # valid writes must succeed
            assert after.revision == before.revision + 1 and after.read_through_position == pos
            assert h.get_offset("a", sid) == after
            accepted_n += 1
        else:
            with pytest.raises((CasMismatchError, OffsetRegressionError, OffsetBeyondHeadError)):
                h.cas_offset("a", sid, expected, pos)
            assert h.get_offset("a", sid) == before  # rejected: nothing changes
    assert h.get_offset("a", sid).revision == 1 + accepted_n


@pytest.mark.catalog_id("PROP-006")
@SET
@given(start=st.integers(0, 8), cand=st.integers(-2, 10))
def test_prop_006_offset_position_never_decreases(tmp_path_factory, start, cand):
    h = shared(tmp_path_factory, "p6")
    sid = _stream_with_records(h, 8)
    cur = h.cas_offset("a", sid, 1, start)
    head = h.read_records(sid)[-1].position
    if cand >= start and cand <= head:
        new = h.cas_offset("a", sid, cur.revision, cand)
        assert new.read_through_position == cand >= start
    else:
        with pytest.raises((ValueError, OffsetRegressionError, OffsetBeyondHeadError)):
            h.cas_offset("a", sid, cur.revision, cand)
        assert h.get_offset("a", sid) == cur


SIZES = st.sampled_from([0, 1, 4095, 4096, 4097, 65535, 65536, 65537, 1048575, 1048576, 1048577])


@pytest.mark.catalog_id("PROP-009")
@settings(max_examples=11, deadline=None)
@given(n=SIZES, ch=st.sampled_from(["a", "한", "😀", "\n"]))
def test_prop_009_large_body_never_silently_truncated(tmp_path_factory, n, ch):
    h = shared(tmp_path_factory, "p9")
    sid = new_stream(h)
    body = ch * n
    rec = h.append_record({"created_at": "2026-10-01T00:00:00Z", "stream_id": sid, "author_peer_id": "a", "kind": "message", "body": body, "idempotency_key": "K"})
    assert rec.body == body
    got = h.reopen().read_records(sid)[0]  # no size limit is configured in M1: exact survival is the only legal outcome
    assert got.body == body and len(got.body) == n and got.payload_digest == rec.payload_digest


@pytest.mark.catalog_id("PROP-010")
@SET
@given(total=st.integers(0, 25), page=st.integers(1, 9))
def test_prop_010_paged_catch_up_has_no_duplicates_or_skips(tmp_path_factory, total, page):
    h = shared(tmp_path_factory, "p10")
    sid = _stream_with_records(h, total)
    committed = [r.position for r in h.read_records(sid)]
    assert len(committed) == total and committed == sorted(set(committed))
    seen, last = [], 0
    while True:
        chunk = h.read_records(sid, after_position=last, limit=page)
        if not chunk:
            break
        assert len(chunk) <= page
        seen += [r.position for r in chunk]
        last = chunk[-1].position
    assert seen == committed  # actual committed positions; no gapless assumption (TD-01)


@pytest.mark.catalog_id("PROP-010")
def test_prop_010_non_positive_limit_rejected(harness):
    harness.create_peer({"peer_id": "a"})
    harness.create_stream({"stream_id": "s", "members": ["a"]})
    for bad in (0, -1):
        with pytest.raises(ValueError):
            harness.read_records("s", limit=bad)


@pytest.mark.catalog_id("PROP-011")
@SET
@given(body=JSON, meta=st.dictionaries(TEXT, JSON, max_size=3))
def test_prop_011_digest_stable_across_strict_json_round_trip(body, meta):
    direct = canonical_json_bytes({"body": body, "metadata": meta})
    wire = json.dumps({"metadata": meta, "body": body}, allow_nan=False, indent=3, ensure_ascii=False)
    again = json.loads(wire)
    assert canonical_json_bytes(again) == direct
    base = {"stream_id": "s", "author_peer_id": "a", "kind": "message", "created_at": "2026-10-01T00:00:00Z"}
    assert compute_record_digest({**base, "body": body, "metadata": meta}) == \
        compute_record_digest({**base, "body": again["body"], "metadata": again["metadata"]})
