"""Wave 5 READ-001/002: read boundary and limit semantics (TD-22). Oracles: literal position lists + raw SQL."""
import sqlite3
from contextlib import closing

import pytest
from hypothesis import given, settings, strategies as st

from tests.m1.harness import CoreHarness
from tests.m1.helpers import append_n, seed

pytestmark = [pytest.mark.unit]


def positions(recs):
    return [r.position for r in recs]


def raw_positions(h, stream="s"):
    with closing(sqlite3.connect(h.db_path)) as c:
        return [r[0] for r in c.execute("SELECT position FROM records WHERE stream_id=? ORDER BY position", (stream,))]


@pytest.mark.m1_id("READ-001")
def test_read_001_after_position_returns_strict_suffix(harness):
    seed(harness)
    seed_other = harness.create_stream({"stream_id": "other", "members": ["a", "b"]})
    append_n(harness, 5)
    append_n(harness, 2, stream="other", prefix="o")  # interleaved other stream must never leak in
    assert raw_positions(harness) == [1, 2, 3, 4, 5]
    assert positions(harness.read_records("s", 0)) == [1, 2, 3, 4, 5]
    assert positions(harness.read_records("s", 3)) == [4, 5]
    assert positions(harness.read_records("s", 5)) == []
    assert positions(harness.read_records("s", 99)) == []  # beyond head: empty, not an error
    assert {r.stream_id for r in harness.read_records("s", 0)} == {"s"} and seed_other.stream_id == "other"
    assert [r.body for r in harness.read_records("s", 3)] == [3, 4]  # canonical order, content intact
    digest = harness.state_digest()
    harness.reopen()
    assert positions(harness.read_records("s", 3)) == [4, 5]  # same after restart
    for bad in (-1, True, 1.5, "1", None):
        with pytest.raises(ValueError, match="after_position"):
            harness.read_records("s", bad)
    assert harness.state_digest() == digest
    assert positions(harness.read_records("s", 4)) == [5]  # control after the rejections


@pytest.mark.m1_id("READ-001")
@settings(max_examples=40, deadline=None)
@given(n=st.integers(1, 9), after=st.integers(0, 11))
def test_read_001_property_suffix_equals_sliced_positions(tmp_path_factory, n, after):
    h = CoreHarness(tmp_path_factory.mktemp("r") / "ws")
    seed(h)
    append_n(h, n)
    exp = [p for p in raw_positions(h) if p > after]
    got = positions(h.read_records("s", after))
    assert got == exp and (n > after) == bool(got)  # nonempty exactly when records remain: cannot pass by returning nothing


@pytest.mark.m1_id("READ-002")
def test_read_002_limit_boundaries_and_invalid_limits(harness):
    seed(harness)
    append_n(harness, 6)
    assert positions(harness.read_records("s", 0, 1)) == [1]
    assert positions(harness.read_records("s", 2, 3)) == [3, 4, 5]
    assert positions(harness.read_records("s", 3, 3)) == [4, 5, 6]  # exact remaining count
    assert positions(harness.read_records("s", 3, 4)) == [4, 5, 6]  # more than remaining
    assert positions(harness.read_records("s", 0, 6)) == [1, 2, 3, 4, 5, 6]
    digest, counts = harness.state_digest(), harness.row_counts()
    for bad in (0, -1, -100, True, 1.5, "1", None):
        with pytest.raises(ValueError, match="limit must be a positive integer"):
            harness.store.read_records("s", 0, bad)
    assert harness.state_digest() == digest and harness.row_counts() == counts  # rejections mutate nothing
    assert positions(harness.store.read_records("s", 0, 2)) == [1, 2]  # control


@pytest.mark.m1_id("READ-002")
@settings(max_examples=40, deadline=None)
@given(after=st.integers(0, 7), limit=st.integers(1, 9))
def test_read_002_property_limit_is_prefix_of_suffix(tmp_path_factory, after, limit):
    h = CoreHarness(tmp_path_factory.mktemp("l") / "ws")
    seed(h)
    append_n(h, 7)
    suffix = [p for p in raw_positions(h) if p > after]
    got = positions(h.read_records("s", after, limit))
    assert got == suffix[:limit] and len(got) == min(limit, len(suffix))
    if suffix:
        assert got  # a positive limit over a non-empty suffix must return something
