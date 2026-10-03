"""Wave 1: CORE-001..013, IDEM-001..003 (Peer identity, Record order/immutability/idempotency/canonicalization)."""
import threading

import pytest

from peerhub.m1.models import SERVER_OWNED_FIELDS, Record, compute_record_digest, is_rfc3339
from peerhub.m1.store import IdempotencyConflictError
from tests.m1.helpers import CONTROL_KINDS, req, seed

REJECT = (ValueError, TypeError)  # never Exception: AttributeError/ImportError/NotImplementedError must not count


@pytest.mark.m1_id("CORE-001")
def test_core_001_peer_identity_survives_adapter_profile_session_change(harness):
    p = harness.create_peer({"peer_id": "cx-review", "adapter_ref": "codex-cli", "created_at": "2026-10-01T00:00:00Z"})
    mappings = {"cx-review": {"model": "m1", "profile": "p1", "session": "s1"}}  # bridge-side data, not Core
    mappings["cx-review"] = {"model": "m2", "profile": "p2", "session": "s2"}
    after = harness.get_peer("cx-review")
    assert after.peer_id == p.peer_id and after.created_at == "2026-10-01T00:00:00Z"
    # adapter change re-registered under the same key keeps identity and original created_at, no second peer
    harness.create_peer({"peer_id": "cx-review", "adapter_ref": "claude-cli", "created_at": "2027-01-01T00:00:00Z"})
    again = harness.reopen().get_peer("cx-review")
    assert again.peer_id == "cx-review" and again.created_at == "2026-10-01T00:00:00Z"
    assert again.adapter_ref == "claude-cli" and harness.row_counts()["peers"] == 1


@pytest.mark.m1_id("CORE-002")
def test_core_002_one_adapter_kind_many_peers(harness):
    for pid in ("cx-01", "cx-02", "cx-review"):
        harness.create_peer({"peer_id": pid, "adapter_ref": "codex-cli"})
    harness.reopen()
    got = [harness.get_peer(p) for p in ("cx-01", "cx-02", "cx-review")]
    assert [g.peer_id for g in got] == ["cx-01", "cx-02", "cx-review"]
    assert {g.adapter_ref for g in got} == {"codex-cli"} and harness.row_counts()["peers"] == 3


@pytest.mark.m1_id("CORE-003")
def test_core_003_append_assigns_server_fields(harness):
    seed(harness)
    rec = harness.append_record(req("hello", created_at="2026-10-01T00:00:00Z"))
    assert rec.position >= 1 and rec.record_id and is_rfc3339(rec.appended_at)
    assert rec.payload_digest.startswith("sha256:") and rec.created_at == "2026-10-01T00:00:00Z"
    assert harness.reopen().read_records("s") == [rec]


@pytest.mark.m1_id("CORE-004")
def test_core_004_record_immutable_after_append(harness):
    import sqlite3

    seed(harness)
    rec = harness.append_record(req({"a": 1}))
    public = {n for n in dir(harness.store) if not n.startswith("_")}
    assert not {n for n in public if any(v in n for v in ("update_record", "delete_record", "edit_record", "mutate_record"))}
    with pytest.raises(ValueError):  # frozen model rejects mutation of the returned object
        rec.body = "tampered"
    with pytest.raises(sqlite3.IntegrityError):  # storage-level guard: no UPDATE/DELETE on records
        with sqlite3.connect(harness.db_path) as c:
            c.execute("UPDATE records SET body_json = '\"x\"'")
    with pytest.raises(sqlite3.IntegrityError):
        with sqlite3.connect(harness.db_path) as c:
            c.execute("DELETE FROM records")
    assert harness.reopen().read_records("s") == [rec] and rec.body == {"a": 1}
    digest = harness.state_digest()
    with pytest.raises(sqlite3.IntegrityError):  # INSERT OR REPLACE must not overwrite (REPLACE-proof via recursive triggers)
        with harness.store._get_connection() as c:
            c.execute("INSERT OR REPLACE INTO records (record_id, stream_id, position, author_peer_id, kind, body_json, "
                      "idempotency_key, payload_digest, created_at, appended_at) SELECT record_id, stream_id, position, "
                      "author_peer_id, kind, '\"evil\"', 'other', payload_digest, created_at, appended_at FROM records")
    assert harness.state_digest() == digest and harness.reopen().read_records("s") == [rec]


@pytest.mark.m1_id("CORE-005")
def test_core_005_read_order_is_position_not_created_at_or_record_id(harness):
    seed(harness)
    stamps = ["2026-10-05T00:00:00Z", "2026-10-01T00:00:00Z", "2026-10-04T00:00:00Z", "2026-10-02T00:00:00Z", "2026-10-03T00:00:00Z"]
    for i, ts in enumerate(stamps):
        harness.append_record(req(i, key=f"k{i}", created_at=ts))
    recs = harness.read_records("s")
    positions = [r.position for r in recs]  # actual committed positions; gaps would be legal (TD-01)
    assert positions == sorted(set(positions)) and len(positions) == 5 and positions[0] >= 1
    assert [r.body for r in recs] == [0, 1, 2, 3, 4]
    assert [r.created_at for r in recs] == stamps and stamps != sorted(stamps)


@pytest.mark.m1_id("CORE-006")
def test_core_006_idempotent_retry_returns_existing(harness):
    seed(harness)
    r = req({"t": "x"}, key="K", created_at="2026-10-01T00:00:00Z")
    first, second = harness.append_record(r), harness.append_record(dict(r))
    assert (second.record_id, second.position) == (first.record_id, first.position) and second == first
    assert len(harness.read_records("s")) == 1 and harness.row_counts()["records"] == 1


@pytest.mark.m1_id("CORE-007")
def test_core_007_changed_payload_conflicts(harness):
    seed(harness)
    harness.append_record(req("A", key="K"))
    digest = harness.state_digest()
    with pytest.raises(IdempotencyConflictError):
        harness.append_record(req("B", key="K"))
    assert len(harness.read_records("s")) == 1 and harness.state_digest() == digest


@pytest.mark.m1_id("CORE-008")
def test_core_008_map_key_order_does_not_change_digest():
    a = {"stream_id": "s", "author_peer_id": "a", "kind": "message", "created_at": "2026-10-01T00:00:00Z",
         "body": {"x": 1, "y": {"p": [1, 2], "q": None}}, "metadata": {"m1": 1, "m2": 2}}
    b = {"metadata": {"m2": 2, "m1": 1}, "body": {"y": {"q": None, "p": [1, 2]}, "x": 1},
         "kind": "message", "author_peer_id": "a", "stream_id": "s", "created_at": "2026-10-01T00:00:00Z"}
    assert compute_record_digest(a) == compute_record_digest(b)
    c = {**a, "body": {"x": 1, "y": {"p": [2, 1], "q": None}}}  # array order is semantic
    assert compute_record_digest(c) != compute_record_digest(a)


@pytest.mark.m1_id("CORE-009")
def test_core_009_server_fields_do_not_participate_in_digest(harness):
    seed(harness)
    request = req({"k": "v"}, key="K", targets=["b"], refs=["r1"], metadata={"m": 1}, created_at="2026-10-01T00:00:00Z")
    stored = harness.append_record(request)
    projection = {k: v for k, v in stored.model_dump().items()
                  if k not in set(SERVER_OWNED_FIELDS) | {"idempotency_key", "schema_version"}}
    assert compute_record_digest(projection) == stored.payload_digest == compute_record_digest(request)
    # stored Record (reopened) must recompute to its own digest, and omitted created_at is rejected (D-W1-2)
    again = harness.reopen().read_records("s")[0]
    assert compute_record_digest(again.model_dump()) == again.payload_digest == stored.payload_digest
    digest = harness.state_digest()
    for bad in ({k: v for k, v in request.items() if k != "created_at"}, {**request, "created_at": None}):
        with pytest.raises(REJECT):
            harness.append_record(bad)
    assert harness.state_digest() == digest
    polluted = {**stored.model_dump(), "record_id": "other", "position": 99, "appended_at": "2030-01-01T00:00:00Z",
                "payload_digest": "sha256:" + "0" * 64}
    assert compute_record_digest(polluted) == stored.payload_digest


@pytest.mark.m1_id("CORE-010")
def test_core_010_changed_created_at_is_conflict(harness):
    seed(harness)
    harness.append_record(req("x", key="K", created_at="2026-10-01T00:00:00Z"))
    digest = harness.state_digest()
    with pytest.raises(IdempotencyConflictError):
        harness.append_record(req("x", key="K", created_at="2026-10-02T00:00:00Z"))
    assert harness.row_counts()["records"] == 1 and harness.state_digest() == digest


@pytest.mark.m1_id("CORE-011")
def test_core_011_control_kinds_are_durable_data_only(harness):
    seed(harness)
    tables = harness.table_names()
    for i, kind in enumerate(CONTROL_KINDS):
        harness.append_record(dict(req({"why": kind}, key=f"c{i}", targets=["b"]), kind=kind))
    recs = harness.reopen().read_records("s")
    assert [(r.kind, r.body, r.targets) for r in recs] == [(k, {"why": k}, ["b"]) for k in CONTROL_KINDS]
    assert harness.table_names() == tables and harness.row_counts()["offsets"] == 0  # no runtime/offset side effect


@pytest.mark.m1_id("CORE-012")
def test_core_012_durable_append_does_not_imply_delivery(harness):
    seed(harness)
    rec = harness.append_record(req("hi"))
    assert isinstance(rec, Record)
    assert not {"delivered", "delivery", "execution", "certainty", "claim", "status"} & set(Record.model_fields)
    assert set(harness.table_names()) == {"peers", "streams", "stream_members", "records", "offsets"}
    assert harness.row_counts()["offsets"] == 0 and harness.get_offset("b", "s").read_through_position == 0


@pytest.mark.m1_id("CORE-013")
def test_core_013_server_owned_fields_cannot_be_injected(harness):
    seed(harness)
    harness.append_record(req("base", key="base"))
    digest, counts = harness.state_digest(), harness.row_counts()
    injections = {"record_id": "forced", "position": 99, "payload_digest": "sha256:" + "a" * 64,
                  "appended_at": "2026-10-01T00:00:00Z"}
    for field, value in injections.items():
        with pytest.raises(REJECT):
            harness.append_record(req("evil", key=f"i-{field}", **{field: value}))
    with pytest.raises(REJECT):
        harness.append_record(req("evil", key="i-all", **injections))
    assert harness.state_digest() == digest and harness.row_counts() == counts
    assert harness.append_record(req("ok", key="ok")).position == 2


@pytest.mark.m1_id("IDEM-001")
def test_idem_001_same_key_different_streams_independent(harness):
    seed(harness)
    harness.create_stream({"stream_id": "y", "members": ["a"]})
    x = harness.append_record(req("P", key="K", stream="s"))
    y = harness.append_record(req("Q", key="K", stream="y"))
    assert (x.position, y.position) == (1, 1) and x.payload_digest != y.payload_digest and x.record_id != y.record_id


@pytest.mark.m1_id("IDEM-002")
def test_idem_002_same_key_different_authors_independent(harness):
    seed(harness)
    a = harness.append_record(req("P", key="K", author="a"))
    b = harness.append_record(req("Q", key="K", author="b"))
    assert (a.position, b.position) == (1, 2) and a.record_id != b.record_id
    assert harness.append_record(req("P", key="K", author="a")) == a  # still collapses inside its own scope


@pytest.mark.m1_id("IDEM-003")
def test_idem_003_same_key_races_independently_across_scopes(harness):
    seed(harness)
    harness.create_stream({"stream_id": "y", "members": ["a"]})
    scopes = [("s", "a", "PA"), ("s", "b", "PB"), ("y", "a", "PY")]
    jobs = [sc for sc in scopes for _ in range(4)]  # duplicates inside each scope
    barrier, results, errs = threading.Barrier(len(jobs)), [], []

    def run(sc):
        barrier.wait()
        try:
            results.append((sc, harness.append_record(req(sc[2], key="K", stream=sc[0], author=sc[1]))))
        except Exception as e:  # noqa: BLE001 - collected and asserted empty below
            errs.append(e)

    ts = [threading.Thread(target=run, args=(sc,)) for sc in jobs]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert errs == [] and len(results) == len(jobs)
    for sc in scopes:
        assert len({r.record_id for s, r in results if s == sc}) == 1, sc  # collapse only within identical scope
    assert harness.row_counts()["records"] == 3
    ps = [r.position for r in harness.read_records("s")]
    assert len(ps) == 2 and ps == sorted(set(ps)) and len(harness.read_records("y")) == 1


@pytest.mark.m1_id("CORE-013")
def test_core_013_non_json_containers_rejected_before_canonicalization(harness):
    """TD-24: tuples/sets/other containers in body or nested metadata are rejected, never coerced to lists."""
    seed(harness)
    digest = harness.state_digest()
    for i, kw in enumerate(({"body": (1, 2)}, {"body": {"a": (1,)}}, {"body": [1, (2, 3)]}, {"metadata": {"m": (1,)}},
                            {"metadata": {"m": {"n": [(1,)]}}}, {"targets": ("b",)}, {"refs": ("r",)})):
        with pytest.raises(REJECT):
            harness.append_record(req(key=f"t{i}", **kw))
    assert harness.state_digest() == digest
    with pytest.raises(TypeError):
        compute_record_digest({**req(), "body": (1, 2)})
