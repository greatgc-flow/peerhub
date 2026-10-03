import pytest
from jsonschema.validators import Draft202012Validator

from peerhub.m1.models import Record, Stream
from tests.m1.spec import SCHEMAS, errors, load, schema, valid_objects

pytestmark = pytest.mark.schema
REJECT = (ValueError, TypeError)  # explicit rejection; ImportError/AttributeError must NOT count as rejection
CORE_KINDS = ("peer", "stream", "record", "offset")


def _kinds_of(errs):
    return {e.validator for e in errs}


def _strict(kind, obj):
    from peerhub.m1.wire import parse_wire  # production strict wire boundary

    return parse_wire(kind, obj)


@pytest.mark.m1_id("SCH-001")
def test_sch_001_valid_peer_validates():
    assert errors("peer", valid_objects()["peer"]) == []
    assert _strict("peer", valid_objects()["peer"]).peer_id == "p-1"


@pytest.mark.m1_id("SCH-002")
def test_sch_002_peer_rejects_empty_id_wrong_version_unknown_property():
    base = valid_objects()["peer"]
    cases = [({**base, "peer_id": ""}, "minLength"), ({**base, "schema_version": "2.0"}, "const"),
             ({**base, "surprise": 1}, "additionalProperties")]
    for obj, want in cases:
        assert want in _kinds_of(errors("peer", obj)), (obj, want)
        with pytest.raises(REJECT):
            _strict("peer", obj)


@pytest.mark.m1_id("SCH-003")
def test_sch_003_stream_open_closed_and_unique_members_only():
    base = valid_objects()["stream"]
    for st in ("OPEN", "CLOSED"):
        assert errors("stream", {**base, "state": st}) == []
        assert _strict("stream", {**base, "state": st}).state.value == st
    bad = [({**base, "members": ["a", "a"]}, "uniqueItems"), ({**base, "state": "ARCHIVED"}, "enum")]
    for obj, want in bad:
        assert want in _kinds_of(errors("stream", obj))
        with pytest.raises(REJECT):
            _strict("stream", obj)
    with pytest.raises(REJECT):
        Stream(stream_id="s", members=["a", "a"])


@pytest.mark.m1_id("SCH-004")
def test_sch_004_record_position_kind_digest_constraints():
    base = valid_objects()["record"]
    assert errors("record", base) == []
    _strict("record", base)
    bad = [({**base, "position": 0}, "minimum"), ({**base, "kind": "Bad Kind"}, "pattern"),
           ({**base, "payload_digest": "sha256:xyz"}, "pattern"), ({**base, "payload_digest": "md5:" + "0" * 32}, "pattern")]
    for obj, want in bad:
        assert want in _kinds_of(errors("record", obj)), obj
        with pytest.raises(REJECT):
            _strict("record", obj)


@pytest.mark.m1_id("SCH-005")
def test_sch_005_record_rejects_duplicate_targets_and_unknown_fields():
    base = valid_objects()["record"]
    for obj, want in (({**base, "targets": ["a", "a"]}, "uniqueItems"), ({**base, "extra": 1}, "additionalProperties")):
        assert want in _kinds_of(errors("record", obj))
        with pytest.raises(REJECT):
            _strict("record", obj)
    with pytest.raises(REJECT):
        Record(**{**base, "targets": ["a", "a"]})


@pytest.mark.m1_id("SCH-006")
def test_sch_006_offset_read_through_and_revision_bounds():
    base = valid_objects()["offset"]
    assert errors("offset", base) == []
    _strict("offset", base)
    for obj in ({**base, "read_through_position": -1}, {**base, "revision": 0}):
        assert "minimum" in _kinds_of(errors("offset", obj))
        with pytest.raises(REJECT):
            _strict("offset", obj)


@pytest.mark.m1_id("SCH-007")
def test_sch_007_observation_vocabulary_closed():
    base = valid_objects()["peer-observation"]
    for st in ("MEASURED", "ABSENT", "UNAVAILABLE", "ERROR", "STALE", "UNKNOWN"):
        assert errors("peer-observation", {**base, "state": st}) == []
    for st in ("HEALTHY", "LIMITLESS"):
        assert "enum" in _kinds_of(errors("peer-observation", {**base, "state": st}))


@pytest.mark.m1_id("SCH-008")
def test_sch_008_resource_pool_kind_vocabulary():
    base = valid_objects()["resource-pool"]
    for k in ("QUOTA", "RATE_LIMIT", "ACCOUNT", "RUNTIME"):
        assert errors("resource-pool", {**base, "kind": k}) == []
    assert "enum" in _kinds_of(errors("resource-pool", {**base, "kind": "OTHER"}))


@pytest.mark.m1_id("SCH-009")
def test_sch_009_published_examples_validate():
    files = sorted((SCHEMAS / "examples").glob("*.json"))
    assert files
    for f in files:
        name = f.name.replace(".example.json", "")
        assert (SCHEMAS / f"{name}.schema.json").exists(), f.name
        assert errors(name, load(f)) == [], f.name


@pytest.mark.m1_id("SCH-010")
def test_sch_010_all_schemas_valid_draft_2020_12():
    files = sorted(SCHEMAS.rglob("*.schema.json"))
    assert len(files) >= 9
    for f in files:
        s = load(f)
        assert s["$schema"] == "https://json-schema.org/draft/2020-12/schema", f.name
        Draft202012Validator.check_schema(s)


def _null_mutants(name, obj):
    for k in list(obj):
        m = {**obj, k: None}
        if errors(name, m):
            yield k, m  # only fields whose schema forbids null


def _must_reject(harness, name, m):
    """Persistence attempt through the strict boundary (D-W0-1); row counts, revisions and state digest must not change."""
    from peerhub.m1.wire import WireValidationError

    digest, counts, tables = harness.state_digest(), harness.row_counts(), harness.table_digests()
    with pytest.raises(WireValidationError):  # specific: rejected by the wire boundary, not by an incidental error
        harness.persist_wire(name, m)
    assert harness.row_counts() == counts, (name, m)
    assert harness.table_digests() == tables, (name, m)  # includes stream/offset revisions
    assert harness.state_digest() == digest, (name, m)


def _prove_port_live(harness):
    """Positive controls: each persistence port really writes, so unchanged-state asserts cannot be vacuous."""
    from peerhub.m1.models import compute_record_digest

    for p in ("p-1", "p-2"):
        harness.persist_wire("peer", {"schema_version": "1.0", "peer_id": p, "created_at": "2026-10-01T00:00:00Z"})
    harness.persist_wire("stream", {**valid_objects()["stream"], "members": ["p-1", "p-2"]})
    sem = {"stream_id": "s-1", "author_peer_id": "p-1", "kind": "message", "body": "hi", "targets": ["p-2"], "reply_to": None,
           "refs": [], "metadata": {}, "created_at": "2026-10-01T00:00:00Z"}
    wire = {"schema_version": "1.0", "record_id": "client-chosen", "position": 77, **sem, "idempotency_key": "wk",
            "payload_digest": compute_record_digest(sem), "appended_at": "2026-10-01T00:00:00Z"}
    before = harness.row_counts()
    rec = harness.persist_wire("record", wire)
    assert harness.row_counts()["records"] == before["records"] + 1
    assert rec.position == 1 and rec.record_id != "client-chosen"  # server-owned wire fields are not trusted
    off = harness.persist_wire("offset", {"schema_version": "1.0", "peer_id": "p-2", "stream_id": "s-1",
                                          "read_through_position": 1, "revision": 1})
    assert (off.read_through_position, off.revision) == (1, 2) and harness.row_counts()["offsets"] == 1
    return wire


@pytest.mark.m1_id("SCH-011")
def test_sch_011_required_fields_null_and_version_rejected_without_mutation(harness):
    _prove_port_live(harness)
    for name, obj in valid_objects().items():
        assert errors(name, obj) == [], name
        mutants = [(f"omit {req}", {k: v for k, v in obj.items() if k != req}) for req in schema(name)["required"]]
        mutants += [(f"null {k}", m) for k, m in _null_mutants(name, obj)]
        mutants += [("version 2.0", {**obj, "schema_version": "2.0"}), ("version 1.1", {**obj, "schema_version": "1.1"})]
        for label, m in mutants:
            assert errors(name, m), (name, label)
            if name in CORE_KINDS:
                _must_reject(harness, name, m)
    assert set(harness.table_names()) == {"peers", "streams", "stream_members", "records", "offsets"}
    assert harness.row_counts() == {"peers": 2, "streams": 1, "stream_members": 2, "records": 1, "offsets": 1}


@pytest.mark.m1_id("SCH-012")
def test_sch_012_null_vs_empty_arrays(harness):
    wire = _prove_port_live(harness)
    v = valid_objects()
    for name, field in (("stream", "members"), ("record", "targets"), ("record", "refs")):
        assert errors(name, {**v[name], field: None}), (name, field)
        with pytest.raises(REJECT):
            _strict(name, {**v[name], field: None})
        assert errors(name, {**v[name], field: []}) == []
        _strict(name, {**v[name], field: []})
    _must_reject(harness, "stream", {**v["stream"], "stream_id": "s-null", "members": None})
    for field in ("targets", "refs"):
        _must_reject(harness, "record", {**wire, "idempotency_key": f"n-{field}", field: None})
    harness.persist_wire("stream", {**v["stream"], "stream_id": "s-empty", "members": []})
    assert harness.get_stream("s-empty").members == []
    from peerhub.m1.models import compute_record_digest

    sem = {k: wire[k] for k in ("stream_id", "author_peer_id", "kind", "body", "reply_to", "metadata", "created_at")}
    empty = {**wire, "idempotency_key": "e1", "targets": [], "refs": [], "payload_digest": compute_record_digest({**sem, "targets": [], "refs": []})}
    assert harness.persist_wire("record", empty).targets == []


@pytest.mark.m1_id("SCH-013")
def test_sch_013_malformed_dates_digest_enum_unknown_fields_fail_closed(harness):
    v = valid_objects()
    mutants = []
    for date_field, name in (("created_at", "peer"), ("created_at", "stream"), ("appended_at", "record"),
                             ("created_at", "record"), ("observed_at", "peer-observation")):
        for bad in ("2026-13-45T00:00:00Z", "yesterday", "2026-10-01 00:00:00", "2026-10-01T00:00:00"):
            mutants.append((name, {**v[name], date_field: bad}))
    for bad in ("sha256:" + "G" * 64, "sha256:" + "a" * 63, "SHA256:" + "a" * 64, "a" * 64):
        mutants.append(("record", {**v["record"], "payload_digest": bad}))
    mutants += [("stream", {**v["stream"], "state": "open"}), ("peer-observation", {**v["peer-observation"], "state": "measured"}),
                ("resource-pool", {**v["resource-pool"], "kind": "quota"})]
    for name in ("peer", "stream", "record", "peer-observation"):
        mutants.append((name, {**v[name], "unknown_field": 1}))
    for name, m in mutants:
        assert errors(name, m), (name, m)
        if name in CORE_KINDS:
            _must_reject(harness, name, m)


def _seed(h):
    h.create_peer({"peer_id": "a"})
    h.create_stream({"stream_id": "s", "members": ["a"]})


def _req(body=None, key="k", **extra):
    return dict(stream_id="s", author_peer_id="a", kind="message", body=body, idempotency_key=key, **extra)


@pytest.mark.m1_id("SCH-014")
def test_sch_014_record_body_accepts_json_values_rejects_runtime_values(harness):
    _seed(harness)
    for i, b in enumerate([None, True, 0, 1.5, "s", [], [1, {"a": None}], {}, {"a": [1, "b"]}]):
        assert harness.append_record(_req(b, key=f"g{i}")).body == b
    n = len(harness.read_records("s"))
    for i, b in enumerate([b"bytes", {1, 2}, object(), {"a": {1}}, [b"x"], {1: "intkey"}]):
        with pytest.raises((TypeError, ValueError)):
            harness.append_record(_req(b, key=f"b{i}"))
    assert len(harness.read_records("s")) == n


@pytest.mark.m1_id("SCH-015")
def test_sch_015_nan_and_infinity_rejected_before_mutation(harness):
    _seed(harness)
    digest = harness.state_digest()
    for bad in (float("nan"), float("inf"), float("-inf")):
        for kw in ({"body": bad}, {"body": {"x": [bad]}}, {"body": None, "metadata": {"m": bad}}):
            with pytest.raises((TypeError, ValueError)):
                harness.append_record(_req(key="nf", **kw))
    assert harness.read_records("s") == []
    assert harness.state_digest() == digest


def test_wire_schema_copies_match_spec_package():
    """Not a catalog id: guards drift of peerhub/m1/schemas (runtime copy) from docs/m1_spec/04_SCHEMAS."""
    from pathlib import Path

    import peerhub.m1.wire as wire

    for kind in CORE_KINDS:
        assert (Path(wire.__file__).parent / "schemas" / f"{kind}.schema.json").read_bytes() == \
            (SCHEMAS / f"{kind}.schema.json").read_bytes(), kind
