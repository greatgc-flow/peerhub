import random
import uuid

import pytest
from hypothesis import HealthCheck, given, settings, strategies as st

from peerhub.m1.models import canonical_json_bytes, compute_payload_digest
from tests.m1.harness import CoreHarness

pytestmark = pytest.mark.property

TEXT = st.text(alphabet=st.characters(blacklist_categories=("Cs",)), max_size=40)
FIXED = ["한국어 테스트", "😀👨‍👩‍👧 é", "line1\r\nline2\nline3\r", "it's \"quoted\"; DROP TABLE records; --",
         "'); DELETE FROM peers;--", "tab\tback\\slash", "  ", "%s ? :name"]


@pytest.mark.m1_id("PROP-007")
@settings(max_examples=60, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(extra=TEXT)
def test_prop_007_unicode_free_text_round_trips(tmp_path_factory, extra):
    h = CoreHarness(tmp_path_factory.mktemp("p7") / uuid.uuid4().hex)
    h.create_peer({"peer_id": "a"})
    h.create_stream({"stream_id": "s", "members": ["a"]})
    h.append_record(dict(created_at="2026-10-01T00:00:00Z", stream_id="s", author_peer_id="a", kind="message", body="sentinel", idempotency_key="sent"))
    tables, sent_digest = h.table_names(), h.read_records("s")[0].payload_digest
    texts = FIXED + [extra]
    for i, t in enumerate(texts):
        h.append_record(dict(created_at="2026-10-01T00:00:00Z", stream_id="s", author_peer_id="a", kind="message", body=t,
                             metadata={"note": t, "list": [t]}, idempotency_key=f"k{i}"))
    h.reopen()
    recs = h.read_records("s")
    assert len(recs) == len(texts) + 1
    assert recs[0].body == "sentinel" and recs[0].payload_digest == sent_digest
    for rec, t in zip(recs[1:], texts):
        assert rec.body == t and rec.metadata == {"note": t, "list": [t]}
    assert h.table_names() == tables
    assert h.get_peer("a").peer_id == "a"


JSON = st.recursive(st.none() | st.booleans() | st.integers(-5, 5) | st.text(max_size=5),
                    lambda c: st.lists(c, max_size=4) | st.dictionaries(st.text(max_size=3), c, max_size=4), max_leaves=8)


def D(body):
    return compute_payload_digest("message", body)


@pytest.mark.m1_id("PROP-008")
@settings(max_examples=100, deadline=None)
@given(items=st.lists(JSON, min_size=2, max_size=5, unique_by=canonical_json_bytes), v=JSON, seed=st.integers())
def test_prop_008_canonical_digest_preserves_order_and_types(items, v, seed):
    rev = list(reversed(items))
    if canonical_json_bytes(rev) != canonical_json_bytes(items):
        assert D(items) != D(rev)
    assert D([1]) != D(["1"]) and D({"a": 1}) != D({"a": "1"})
    assert D(True) != D(1) and D(False) != D(0) and D([True]) != D([1])
    assert D({"a": None}) != D({}) and D({"a": None, "b": v}) != D({"b": v})
    d = {f"k{i}": x for i, x in enumerate(items)}
    keys = list(d)
    random.Random(seed).shuffle(keys)
    assert D(d) == D({k: d[k] for k in keys})
