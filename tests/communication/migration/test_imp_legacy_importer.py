"""Wave 7: IMP-001..004 legacy importer (TD-16, MIGRATION_CUTOVER step 7).

Declared mappings (D-W7-1): event_log rows -> Records of Stream `legacy:<correlation_id>` authored by Peer `legacy:orchestrator`;
consumer_offsets -> Peer `legacy:consumer:<id>` + per-stream Offsets. Everything else is reported as unmapped, never guessed.
Atomicity: one transaction per correlation group ("unit"). Expected values below are literals / raw SQL, not importer output."""
import hashlib
import json
import shutil
import sqlite3
from contextlib import closing

import pytest

from peerhub.core.legacy_import import LegacyImporter, LegacyPlanChangedError, LegacySourceError
from tests.communication.harness.crash_workers import spawn_exitcode
from tests.communication.harness.import_workers import CRASH_EXIT, import_apply_worker, import_crash_worker
from tests.communication.harness.legacy_fixture import DEFAULT_EVENTS, make_legacy, normalized_state, raw_dump, tree_fingerprint
from tests.communication.harness.mp import BARRIER, OUTQ, run_procs

pytestmark = [pytest.mark.migration, pytest.mark.cutover]

CORE_TABLES = ("peers", "streams", "stream_members", "records", "offsets")


@pytest.fixture
def legacy(tmp_path):
    src = tmp_path / "legacy dir"
    src.mkdir()
    return make_legacy(src / "legacy.db")


def _counts(db):
    with closing(sqlite3.connect(db)) as c:
        return {t: c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in CORE_TABLES}


def _sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _unmapped_oracle(src):
    """Raw-SQL oracle: every non-empty legacy table except the two mapped ones."""
    with closing(sqlite3.connect(src)) as c:
        out = {}
        for (name,) in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall():
            n = c.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
            if n and name not in ("event_log", "consumer_offsets"):
                out[name] = n
        return out


# ---------------------------------------------------------------- IMP-001
def test_imp_001_dry_run_is_source_and_target_read_only_and_reports_only_declared_items(tmp_path, legacy):
    target = tmp_path / "m1" / "core.db"
    before_src = tree_fingerprint(legacy.parent)
    rep = LegacyImporter(legacy, target).dry_run()  # target does not exist: dry-run must not create it
    assert not target.exists() and not target.parent.exists()
    assert tree_fingerprint(legacy.parent) == before_src
    assert rep["mode"] == "dry-run"
    assert [(u["unit"], u["status"], u["records"]) for u in rep["units"]] == [("legacy:c1", "new", 2), ("legacy:c2", "new", 1)]
    assert rep["unmapped_tables"] == _unmapped_oracle(legacy) and "governed_targets" in rep["unmapped_tables"]
    assert rep["totals"]["records_imported"] == 0 and rep["totals"]["would_import_records"] == 3
    assert rep["mappings"] == ["event_log->records", "consumer_offsets->offsets"]  # the declared mappings, nothing else
    # existing M1 target: byte-for-byte unchanged too (positive control: apply DOES change it)
    LegacyImporter(legacy, target).apply()
    fp = tree_fingerprint(target.parent)
    rep2 = LegacyImporter(legacy, target).dry_run()
    assert tree_fingerprint(target.parent) == fp and tree_fingerprint(legacy.parent) == before_src
    assert [u["status"] for u in rep2["units"]] == ["already_imported", "already_imported"]
    assert fp != {}  # the target really holds data now


def test_imp_001_dry_run_on_changed_source_reports_conflict_without_writing(tmp_path, legacy):
    target = tmp_path / "core.db"
    LegacyImporter(legacy, target).apply()
    with closing(sqlite3.connect(legacy)) as c:  # legacy row edited after import
        c.execute("UPDATE event_log SET payload_json = '{\"changed\": 1}' WHERE event_id = 'e2'")
        c.commit()
    fp = tree_fingerprint(tmp_path)
    rep = LegacyImporter(legacy, target).dry_run()
    assert tree_fingerprint(tmp_path) == fp
    assert {u["unit"]: u["status"] for u in rep["units"]} == {"legacy:c1": "conflict", "legacy:c2": "already_imported"}


@pytest.mark.parametrize("bad", ["missing", "not_sqlite", "no_event_log", "same_as_target", "pending_wal"])
def test_imp_001_unusable_source_is_refused_before_any_write(tmp_path, legacy, bad):
    target = tmp_path / "core.db"
    if bad == "missing":
        src = tmp_path / "nope.db"
    elif bad == "not_sqlite":
        src = tmp_path / "junk.db"
        src.write_bytes(b"this is not a database" * 50)
    elif bad == "no_event_log":
        src = tmp_path / "other.db"
        with closing(sqlite3.connect(src)) as c:
            c.execute("CREATE TABLE x (a)")
            c.commit()
    elif bad == "same_as_target":
        src = target = legacy
    else:  # un-checkpointed WAL next to the source: a read-only reader cannot see it safely
        src = tmp_path / "wal.db"
        shutil.copy(legacy, src)
        (tmp_path / "wal.db-wal").write_bytes(b"x" * 64)
    before = tree_fingerprint(tmp_path)
    for call in ("dry_run", "apply"):
        with pytest.raises(LegacySourceError):
            getattr(LegacyImporter(src, target), call)()
    assert tree_fingerprint(tmp_path) == before
    # positive control: a valid legacy source with the same kind of target is accepted
    assert LegacyImporter(legacy, tmp_path / "ok.db").dry_run()["units"]


# ---------------------------------------------------------------- IMP-002
def test_imp_002_apply_imports_only_declared_mappings_and_leaves_source_bytes_untouched(tmp_path, legacy):
    target = tmp_path / "m1" / "core.db"
    src_hash, src_tree = _sha(legacy), tree_fingerprint(legacy.parent)
    rep = LegacyImporter(legacy, target).apply()
    assert _sha(legacy) == src_hash and tree_fingerprint(legacy.parent) == src_tree  # no -wal/-shm, no byte change
    assert [(u["unit"], u["status"]) for u in rep["units"]] == [("legacy:c1", "imported"), ("legacy:c2", "imported")]
    assert rep["totals"] == {"imported_units": 2, "already_imported_units": 0, "conflict_units": 0, "malformed_units": 0,
                             "records_imported": 3, "would_import_records": 0, "peers_created": 3, "offsets_written": 3,
                             "updated_units": 0, "would_write_offsets": 0, "offset_conflicts": 0}
    assert rep["unmapped_tables"] == _unmapped_oracle(legacy)
    with closing(sqlite3.connect(target)) as c:  # only the Core tables carry imported data; nothing for unmapped legacy tables
        names = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
    assert names == set(CORE_TABLES) and "governed_targets" not in names
    # literal expectations (epoch seconds -> RFC 3339 UTC)
    assert [r[0] for r in raw_dump(target, "peers", "peer_id", "peer_id")] == [
        "legacy:consumer:audit", "legacy:consumer:dash", "legacy:orchestrator"]
    assert raw_dump(target, "streams", "stream_id", "stream_id, state, revision, created_at") == [
        ("legacy:c1", "OPEN", 1, "2023-11-14T22:13:20Z"), ("legacy:c2", "OPEN", 1, "2023-11-14T22:13:30Z")]
    assert raw_dump(target, "stream_members", "stream_id, rowid", "stream_id, peer_id") == [
        ("legacy:c1", "legacy:orchestrator"), ("legacy:c1", "legacy:consumer:audit"), ("legacy:c1", "legacy:consumer:dash"),
        ("legacy:c2", "legacy:orchestrator"), ("legacy:c2", "legacy:consumer:audit")]
    recs = raw_dump(target, "records", "stream_id, position",
                    "stream_id, position, author_peer_id, kind, idempotency_key, created_at, body_json, reply_to")
    assert [r[:6] for r in recs] == [
        ("legacy:c1", 1, "legacy:orchestrator", "legacy.event", "legacy:event:e1", "2023-11-14T22:13:20Z"),
        ("legacy:c1", 2, "legacy:orchestrator", "legacy.event", "legacy:event:e2", "2023-11-14T22:13:25Z"),
        ("legacy:c2", 1, "legacy:orchestrator", "legacy.event", "legacy:event:e3", "2023-11-14T22:13:30Z")]
    assert all(r[7] is None for r in recs)
    b1 = json.loads(recs[0][6])
    assert b1 == {"event_id": "e1", "event_kind": "dispatch.admitted", "correlation_id": "c1", "occurred_at": 1700000000,
                  "appended_at": 1700000000, "outbox_position": 1, "protocol_major": 0, "protocol_minor": 1,
                  "schema_version": "v0", "request_id": "r1", "round_id": None, "evidence_refs": [],
                  "predecessor_digest": None, "recovery_context": None,
                  "payload": {"note": "café 한글", "n": 1.5, "nested": {"a": [1, 2, None]}}}
    assert json.loads(recs[1][6])["evidence_refs"] == ["ev-1", "ev-2"]
    assert json.loads(recs[2][6])["round_id"] == "round-9" and json.loads(recs[2][6])["predecessor_digest"] == "dddddddd"
    # offsets: consumer position -> count of that stream's imported records at or before it (dash@2, audit@3)
    assert raw_dump(target, "offsets", "peer_id, stream_id") == [
        ("legacy:consumer:audit", "legacy:c1", 2, 2), ("legacy:consumer:audit", "legacy:c2", 1, 2),
        ("legacy:consumer:dash", "legacy:c1", 2, 2)]
    # imported rows verify against the Core's own invariant (the digest recomputes from the stored Record)
    from peerhub.core.models import compute_record_digest
    from peerhub.core.store import CoreStore
    st = CoreStore(target)
    for sid in ("legacy:c1", "legacy:c2"):
        for r in st.read_records(sid):
            assert compute_record_digest(r.model_dump()) == r.payload_digest


def test_imp_002_every_source_event_is_present_in_the_target_no_data_loss(tmp_path, legacy):
    target = tmp_path / "core.db"
    LegacyImporter(legacy, target).apply()
    with closing(sqlite3.connect(legacy)) as c:
        src_ids = [r[0] for r in c.execute("SELECT event_id FROM event_log ORDER BY outbox_position")]
    got = [json.loads(r[0])["event_id"] for r in raw_dump(target, "records", "stream_id, position", "body_json")]
    assert sorted(got) == sorted(src_ids) == sorted(e["event_id"] for e in DEFAULT_EVENTS)


def test_imp_002_apply_with_stale_plan_digest_is_refused_without_writes(tmp_path, legacy):
    target = tmp_path / "core.db"
    plan = LegacyImporter(legacy, target).dry_run()["plan_digest"]
    with closing(sqlite3.connect(legacy)) as c:  # source changes between dry-run and apply
        c.execute("UPDATE event_log SET event_kind = 'changed' WHERE event_id = 'e3'")
        c.commit()
    with pytest.raises(LegacyPlanChangedError):
        LegacyImporter(legacy, target).apply(expected_plan_digest=plan)
    assert not target.exists()
    fresh = LegacyImporter(legacy, target).dry_run()["plan_digest"]  # positive control: the fresh plan digest is accepted
    assert fresh != plan
    assert LegacyImporter(legacy, target).apply(expected_plan_digest=fresh)["totals"]["imported_units"] == 2


# ---------------------------------------------------------------- IMP-003
def test_imp_003_repeated_import_is_idempotent_and_reports_already_imported(tmp_path, legacy):
    target = tmp_path / "core.db"
    first = LegacyImporter(legacy, target).apply()
    state1 = normalized_state(target)
    ids1 = raw_dump(target, "records", "record_id", "record_id, appended_at")
    second = LegacyImporter(legacy, target).apply()
    assert first["totals"]["imported_units"] == 2
    assert [u["status"] for u in second["units"]] == ["already_imported", "already_imported"]
    assert second["totals"]["records_imported"] == 0 and second["totals"]["peers_created"] == 0
    assert second["totals"]["offsets_written"] == 0
    assert normalized_state(target) == state1
    assert _counts(target) == {"peers": 3, "streams": 2, "stream_members": 5, "records": 3, "offsets": 3}
    assert raw_dump(target, "records", "record_id", "record_id, appended_at") == ids1  # same rows, not re-created


def test_imp_003_import_does_not_overwrite_work_done_in_m1_after_the_first_import(tmp_path, legacy):
    from peerhub.core.store import CoreStore
    target = tmp_path / "core.db"
    LegacyImporter(legacy, target).apply()
    st = CoreStore(target)
    st.append_record(stream_id="legacy:c1", author_peer_id="legacy:orchestrator", kind="message", body="m1 native",
                     idempotency_key="native-1", created_at="2026-10-04T00:00:00Z")
    off = st.get_offset("legacy:consumer:dash", "legacy:c1")
    st.advance_offset_cas("legacy:consumer:dash", "legacy:c1", 3, off.revision)
    rep = LegacyImporter(legacy, target).apply()
    assert [u["status"] for u in rep["units"]] == ["already_imported", "already_imported"]  # prefix match tolerates M1 records
    assert st.get_offset("legacy:consumer:dash", "legacy:c1").read_through_position == 3  # M1-advanced offset not rewound
    assert len(st.read_records("legacy:c1")) == 3


def test_imp_003_foreign_or_edited_target_objects_are_reported_as_conflicts_and_left_untouched(tmp_path, legacy):
    from peerhub.core.models import Peer, Stream
    from peerhub.core.store import CoreStore
    # a pre-existing stream that merely has the reserved id (no import marker): authenticated against the persisted row
    target = tmp_path / "core.db"
    st = CoreStore(target)
    st.register_peer(Peer(peer_id="p1"))
    st.create_stream(Stream(stream_id="legacy:c1", members=["p1"]))
    before = normalized_state(target)
    rep = LegacyImporter(legacy, target).apply()
    assert {u["unit"]: u["status"] for u in rep["units"]} == {"legacy:c1": "conflict", "legacy:c2": "imported"}
    assert "not created by the legacy importer" in next(u for u in rep["units"] if u["unit"] == "legacy:c1")["reason"]
    after = normalized_state(target)
    assert [s for s in after["streams"] if s[0] == "legacy:c1"] == [s for s in before["streams"] if s[0] == "legacy:c1"]
    assert not [r for r in after["records"] if r[0] == "legacy:c1"]
    assert [r for r in after["records"] if r[0] == "legacy:c2"]  # control: the unrelated unit still imported
    # a stream carrying an importer marker for a DIFFERENT correlation group is equally foreign
    t3 = tmp_path / "core3.db"
    s3 = CoreStore(t3)
    s3.register_peer(Peer(peer_id="p1"))
    s3.create_stream(Stream(stream_id="legacy:c1", members=["p1"], metadata={"legacy_import": {"source": "event_log", "group": "other"}}))
    rep3 = LegacyImporter(legacy, t3).apply()
    u3 = next(u for u in rep3["units"] if u["unit"] == "legacy:c1")
    assert u3["status"] == "conflict" and "not created by the legacy importer" in u3["reason"]
    assert not [r for r in raw_dump(t3, "records", "record_id", "stream_id") if r[0] == "legacy:c1"]
    # a foreign peer squatting on the reserved author id blocks every unit that needs it, and is not modified
    t2 = tmp_path / "core2.db"
    s2 = CoreStore(t2)
    s2.register_peer(Peer(peer_id="legacy:orchestrator", display_name="mine"))
    rep2 = LegacyImporter(legacy, t2).apply()
    assert {u["status"] for u in rep2["units"]} == {"conflict"}
    assert s2.get_peer("legacy:orchestrator").display_name == "mine" and _counts(t2)["records"] == 0


def test_imp_003_concurrent_importers_import_each_unit_exactly_once(tmp_path, legacy):
    target = tmp_path / "core.db"
    res = run_procs([(import_apply_worker, (str(legacy), str(target), BARRIER, OUTQ))] * 3)
    for unit in ("legacy:c1", "legacy:c2"):
        assert sorted(r["statuses"][unit] for r in res) == ["already_imported", "already_imported", "imported"]
    assert sum(r["totals"]["records_imported"] for r in res) == 3
    assert _counts(target) == {"peers": 3, "streams": 2, "stream_members": 5, "records": 3, "offsets": 3}


# ---------------------------------------------------------------- IMP-004
MALFORMED = {
    "payload_not_json": {"payload_json": "{not json"},
    "payload_nan": {"payload_json": '{"x": NaN}'},
    "refs_not_list": {"evidence_refs_json": '{"a": 1}'},
    "occurred_at_text": {"occurred_at": "yesterday"},
    "occurred_at_overflow": {"occurred_at": 10 ** 18},
    "empty_event_id": {"event_id": ""},
    "empty_correlation": {"correlation_id": ""},
}


def _events_with_bad(extra):
    evs = [dict(e) for e in DEFAULT_EVENTS]
    evs.append({"event_id": "e4", "correlation_id": "c3", "occurred_at": 1700000020, "payload": {"k": "v"}})  # valid group c3
    bad = {"event_id": "bad1", "correlation_id": "cbad", "occurred_at": 1700000030, "payload": {}}
    bad.update(extra)
    evs.insert(3, bad)  # outbox_position 4
    evs.append({"event_id": "e5", "correlation_id": bad["correlation_id"], "occurred_at": 1700000031, "payload": {}})  # valid sibling
    return evs


@pytest.mark.parametrize("name", sorted(MALFORMED))
def test_imp_004_malformed_row_isolates_its_unit_without_touching_valid_units(tmp_path, name):
    src = make_legacy(tmp_path / "l.db", _events_with_bad(MALFORMED[name]), consumers={"dash": 2})
    target = tmp_path / "core.db"
    rep = LegacyImporter(src, target).apply()
    st = {u["unit"]: u["status"] for u in rep["units"]}
    bad = [u for u in rep["units"] if u["status"] == "malformed"]
    assert len(bad) == 1 and rep["totals"]["malformed_units"] == 1
    assert st["legacy:c1"] == st["legacy:c2"] == st["legacy:c3"] == "imported"  # positive control: valid groups import
    assert bad[0]["rows"] == ([4, 6] if name == "empty_correlation" else [4]) and bad[0]["reason"]  # offending legacy rows (outbox_position) are named
    assert [r[0] for r in raw_dump(target, "streams", "stream_id", "stream_id")] == ["legacy:c1", "legacy:c2", "legacy:c3"]
    keys = [r[0] for r in raw_dump(target, "records", "record_id", "idempotency_key")]
    assert not [k for k in keys if k.endswith(("e5", "bad1"))]  # no partial malformed object, not even its valid sibling
    assert _counts(target)["records"] == 4
    assert rep["totals"]["records_imported"] == _counts(target)["records"]  # exact accounting
    rep2 = LegacyImporter(src, target).apply()  # re-run keeps isolating it and imports nothing new
    assert {u["status"] for u in rep2["units"] if u["unit"] != bad[0]["unit"]} == {"already_imported"}
    assert next(u for u in rep2["units"] if u["unit"] == bad[0]["unit"])["status"] == "malformed"
    assert _counts(target)["records"] == 4


def test_imp_004_crash_between_units_leaves_complete_units_only_and_resume_matches_clean_run(tmp_path, legacy):
    clean = tmp_path / "clean.db"
    LegacyImporter(legacy, clean).apply()
    for point, nth in (("import.unit.before_commit", 2), ("import.unit.after_commit", 1)):
        t = tmp_path / f"{point}.db"
        code = spawn_exitcode(import_crash_worker, (str(legacy), str(t), point, nth))
        assert code == CRASH_EXIT, point  # the fault really fired (hard process death)
        st = normalized_state(t)
        assert {s[0] for s in st["streams"]} == {"legacy:c1"}  # complete old-or-new: exactly the committed unit
        assert {r[0] for r in st["records"]} == {"legacy:c1"} and len(st["records"]) == 2
        assert {o[1] for o in st["offsets"]} == {"legacy:c1"}
        with closing(sqlite3.connect(t)) as c:
            assert c.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            assert c.execute("PRAGMA foreign_key_check").fetchall() == []
        rep = LegacyImporter(legacy, t).apply()  # resume with exact accounting
        assert {u["unit"]: u["status"] for u in rep["units"]} == {"legacy:c1": "already_imported", "legacy:c2": "imported"}
        assert rep["totals"]["records_imported"] == 1
        assert normalized_state(t) == normalized_state(clean)
    # positive control: a never-firing crash point lets the worker finish normally
    t3 = tmp_path / "never.db"
    assert spawn_exitcode(import_crash_worker, (str(legacy), str(t3), "no.such.point", 1)) == 0
    assert normalized_state(t3) == normalized_state(clean)


# ---------------------------------------------------------------- IMP-003 (per-component idempotency, review item 1)
def _add_consumer(src, consumer, pos):
    with closing(sqlite3.connect(src)) as c:
        eid = c.execute("SELECT event_id FROM event_log WHERE outbox_position = ?", (pos,)).fetchone()[0]
        c.execute("DELETE FROM consumer_offsets WHERE consumer_id = ?", (consumer,))
        c.execute("INSERT INTO consumer_offsets (consumer_id, outbox_position, event_id, revision) VALUES (?,?,?,1)", (consumer, pos, eid))
        c.commit()


def _row_ids(db):
    return (raw_dump(db, "records", "record_id", "record_id, appended_at"), raw_dump(db, "offsets", "peer_id, stream_id"),
            raw_dump(db, "peers", "peer_id", "peer_id, created_at, metadata_json"))


def test_imp_003_offsets_added_to_the_legacy_store_after_import_are_imported_on_rerun(tmp_path, legacy):
    target = tmp_path / "core.db"
    LegacyImporter(legacy, target).apply()
    before_ids, before_offsets, before_peers = _row_ids(target)
    _add_consumer(legacy, "late", 3)  # legacy gained a consumer after the first import
    dry = LegacyImporter(legacy, target).dry_run()
    assert [u["status"] for u in dry["units"]] == ["update", "update"] and dry["totals"]["would_write_offsets"] == 2
    assert _row_ids(target) == (before_ids, before_offsets, before_peers)  # dry-run wrote nothing
    rep = LegacyImporter(legacy, target).apply()
    assert [u["status"] for u in rep["units"]] == ["updated", "updated"]
    t = rep["totals"]
    assert (t["records_imported"], t["peers_created"], t["offsets_written"], t["updated_units"], t["imported_units"]) == (0, 1, 2, 2, 0)
    ids, offsets, peers = _row_ids(target)
    assert ids == before_ids  # no Record re-created or touched
    assert set(before_offsets) <= set(offsets) and set(offsets) - set(before_offsets) == {
        ("legacy:consumer:late", "legacy:c1", 2, 2), ("legacy:consumer:late", "legacy:c2", 1, 2)}  # exact new rows only
    assert set(before_peers) <= set(peers) and len(peers) == len(before_peers) + 1
    assert raw_dump(target, "stream_members", "stream_id, rowid", "stream_id, peer_id")[-1] == ("legacy:c2", "legacy:consumer:late")
    again = LegacyImporter(legacy, target).apply()  # now fully converged
    assert [u["status"] for u in again["units"]] == ["already_imported", "already_imported"]
    assert again["totals"]["offsets_written"] == 0 and _row_ids(target) == (ids, offsets, peers)


def test_imp_003_offset_differences_are_reported_never_guessed(tmp_path):
    evs = [{"event_id": f"e{i}", "correlation_id": "c1", "occurred_at": 1700000000 + i, "payload": {}} for i in (1, 2, 3)]
    src = make_legacy(tmp_path / "l.db", evs, consumers={"dash": 1, "audit": 1, "same": 2})
    target = tmp_path / "core.db"
    LegacyImporter(src, target).apply()
    from peerhub.core.store import CoreStore
    st = CoreStore(target)
    st.advance_offset_cas("legacy:consumer:audit", "legacy:c1", 3, 2)  # M1 moves past legacy (audit: legacy 1, M1 3)
    _add_consumer(src, "dash", 3)  # legacy moves past M1 (dash: legacy 3, M1 1)
    before = _row_ids(target)
    rep = LegacyImporter(src, target).apply()
    (u,) = rep["units"]
    assert u["status"] == "already_imported"  # nothing written for it
    assert sorted((c["consumer"], c["kind"], c["legacy"], c["m1"]) for c in u["offsets"]["conflicts"]) == [
        ("audit", "m1_ahead", 1, 3), ("dash", "legacy_ahead", 3, 1)]
    assert [a[0] for a in u["offsets"]["already"]] == ["same"]  # positive control: equal offsets are not conflicts
    assert rep["totals"]["offset_conflicts"] == 2 and rep["totals"]["offsets_written"] == 0
    assert _row_ids(target) == before  # existing offsets are never rewound or advanced
    assert st.get_offset("legacy:consumer:dash", "legacy:c1").read_through_position == 1


def test_imp_003_crash_while_adding_offsets_leaves_old_state_and_resume_matches_clean_import(tmp_path, legacy):
    _add_consumer(legacy, "late", 3)
    clean = tmp_path / "clean.db"
    LegacyImporter(legacy, clean).apply()
    with closing(sqlite3.connect(legacy)) as c:  # rewind the legacy store to its state before the late consumer existed
        c.execute("DELETE FROM consumer_offsets WHERE consumer_id = 'late'")
        c.commit()
    for point, nth in (("import.unit.before_commit", 2), ("import.unit.after_commit", 1)):
        t = tmp_path / (point + ".db")
        LegacyImporter(legacy, t).apply()
        old = normalized_state(t)
        _add_consumer(legacy, "late", 3)
        assert spawn_exitcode(import_crash_worker, (str(legacy), str(t), point, nth)) == CRASH_EXIT  # the update really crashed
        st = normalized_state(t)
        late = [o for o in st["offsets"] if o[0] == "legacy:consumer:late"]
        assert late == [("legacy:consumer:late", "legacy:c1", 2, 2)]  # complete unit or nothing: c1 committed, c2 absent
        assert st != old
        with closing(sqlite3.connect(t)) as c:
            assert c.execute("PRAGMA integrity_check").fetchone()[0] == "ok" and c.execute("PRAGMA foreign_key_check").fetchall() == []
        rep = LegacyImporter(legacy, t).apply()  # resume with exact accounting
        assert {u["unit"]: u["status"] for u in rep["units"]} == {"legacy:c1": "already_imported", "legacy:c2": "updated"}
        assert rep["totals"]["offsets_written"] == 1 and rep["totals"]["records_imported"] == 0
        assert normalized_state(t) == normalized_state(clean)
        with closing(sqlite3.connect(legacy)) as c:
            c.execute("DELETE FROM consumer_offsets WHERE consumer_id = 'late'")
            c.commit()
