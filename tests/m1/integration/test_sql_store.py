"""Wave 2: SQL-001..011 (SQLite integration)."""
import sqlite3
import threading

import pytest

from peerhub.extensions.bridge_claims import ClaimStore, StaleClaimError
from peerhub.m1.migrations import CURRENT_VERSION, run_migrations
from peerhub.m1.models import AppendRequest, compute_record_digest
from peerhub.m1.store import CoreStore, UnknownReferenceError
from tests.m1.harness.clock import ManualClock
from tests.m1.harness.core import CoreHarness, compute_state_digest
from tests.m1.harness.mp import OUTQ, run_one
from tests.m1.harness.mp_workers import verify_worker
from tests.m1.helpers import append_n, req, seed

pytestmark = [pytest.mark.sqlite]
CORE_TABLES = {"peers", "streams", "stream_members", "records", "offsets"}


def _schema_dump(path):
    with sqlite3.connect(path) as c:
        return c.execute("SELECT type, name, tbl_name, sql FROM sqlite_master ORDER BY type, name").fetchall()


@pytest.mark.m1_id("SQL-001")
def test_sql_001_connection_enforces_wal_fk_busy_timeout(harness):
    with harness.store.connect() as c:
        assert c.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        assert c.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert c.execute("PRAGMA busy_timeout").fetchone()[0] > 0
    # WAL is persisted in the file, visible to a completely independent plain connection
    raw = sqlite3.connect(harness.db_path)
    try:
        assert raw.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        assert raw.execute("PRAGMA foreign_keys").fetchone()[0] == 0  # control: pragma is per-connection, store sets it
    finally:
        raw.close()


@pytest.mark.m1_id("SQL-002")
def test_sql_002_ordered_migrations_bootstrap_empty_db(tmp_path):
    for name, create in (("zero.db", lambda p: p.write_bytes(b"")), ("new.db", lambda p: None)):
        p = tmp_path / name
        create(p)
        applied = run_migrations(p)
        assert applied == list(range(1, CURRENT_VERSION + 1))
        with sqlite3.connect(p) as c:
            tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            assert tables == CORE_TABLES
            assert c.execute("PRAGMA user_version").fetchone()[0] == CURRENT_VERSION
        # positive control: the migrated DB is actually usable by the store
        s = CoreStore(p)
        assert s.get_peer("nobody") is None


@pytest.mark.m1_id("SQL-003")
def test_sql_003_migration_runner_repeatable_at_current_version(harness):
    seed(harness)
    append_n(harness, 3)
    before = (_schema_dump(harness.db_path), harness.table_digests(), harness.state_digest())
    for _ in range(3):
        assert run_migrations(harness.db_path) == []  # deterministic: nothing to apply
    after = (_schema_dump(harness.db_path), harness.table_digests(), harness.state_digest())
    assert before == after
    with sqlite3.connect(harness.db_path) as c:
        assert c.execute("PRAGMA user_version").fetchone()[0] == CURRENT_VERSION


@pytest.mark.m1_id("SQL-004")
def test_sql_004_foreign_keys_reject_orphan_record_references(harness):
    seed(harness, peers=("a",), members=["a"])
    ok = harness.append_record(req(author="a", key="control"))  # positive control: valid refs commit
    assert ok.position == 1
    counts, digests = harness.row_counts(), harness.table_digests()
    # store path: explicit referential error, atomic
    for bad in (req(stream="ghost", key="k1"), req(author="ghost", key="k2")):
        with pytest.raises(UnknownReferenceError):
            harness.append_record(bad)
    # direct SQL path: SQLite itself enforces the FK
    for sql_args in (("ghost-s", 2, "a"), ("s", 2, "ghost-a")):
        with harness.store.connect() as c:
            with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY constraint failed"):
                c.execute(
                    "INSERT INTO records (record_id, stream_id, position, author_peer_id, kind, body_json, idempotency_key,"
                    " payload_digest, created_at, appended_at) VALUES ('x', ?, ?, ?, 'message', '1', 'kx', 'd', 't', 't')",
                    sql_args)
    assert harness.row_counts() == counts and harness.table_digests() == digests


@pytest.mark.m1_id("SQL-005")
def test_sql_005_append_publishes_record_and_position_atomically(harness):
    seed(harness)
    at_barrier, release = threading.Event(), threading.Event()

    def hook(point):
        if point == "append.before_commit":
            at_barrier.set()
            assert release.wait(30), "release never signalled"

    harness.store.fault_hook = hook
    box = {}

    def writer():
        try:
            box["rec"] = harness.store.append_record(**req(body="atomic", key="k"))
        except BaseException as e:
            box["err"] = e

    t = threading.Thread(target=writer, daemon=True)
    t.start()
    try:
        assert at_barrier.wait(30), "writer never reached the commit barrier"
        # the write window is real: another writer cannot take the lock right now (control)
        obs = sqlite3.connect(harness.db_path, timeout=0)
        try:
            with pytest.raises(sqlite3.OperationalError, match="database is locked"):
                obs.execute("BEGIN IMMEDIATE")
        finally:
            obs.close()
        # observer connection: neither partial Record nor a reserved visible position
        obs = sqlite3.connect(harness.db_path)
        try:
            assert obs.execute("SELECT COUNT(*), MAX(position) FROM records").fetchone() == (0, None)
        finally:
            obs.close()
        fresh = CoreStore(harness.db_path)
        assert fresh.read_records("s", 0, 100) == [] and fresh.stream_head("s") == 0
    finally:
        release.set()
        t.join(30)
    assert not t.is_alive() and "err" not in box, box
    rec = box["rec"]
    assert rec.position == 1
    seen = CoreStore(harness.db_path).read_records("s", 0, 100)
    assert seen == [rec]  # complete Record after commit


@pytest.mark.m1_id("SQL-006")
def test_sql_006_read_only_uow_rejects_writes(harness):
    seed(harness)
    append_n(harness, 2)
    counts, digest = harness.row_counts(), harness.state_digest()
    with harness.store.read_uow() as ro:
        assert ro.execute("SELECT COUNT(*) FROM records").fetchone()[0] == 2  # positive control: reads work
        for sql in ("INSERT INTO peers (peer_id, created_at) VALUES ('x', 't')",
                    "UPDATE streams SET title='nope'",
                    "DELETE FROM offsets",
                    "CREATE TABLE evil (x)"):
            with pytest.raises(sqlite3.OperationalError, match="readonly"):
                ro.execute(sql)
    assert harness.row_counts() == counts and harness.state_digest() == digest
    harness.create_peer({"peer_id": "c"})  # control: the normal write path still works afterwards
    assert harness.get_peer("c") is not None


@pytest.mark.m1_id("SQL-007")
def test_sql_007_committed_state_survives_new_store_and_process(harness):
    seed(harness)
    recs = append_n(harness, 5)
    off = harness.cas_offset("b", "s", 1, 3)
    stream, peers = harness.get_stream("s"), [harness.get_peer(p) for p in ("a", "b")]
    digest = harness.state_digest()
    harness.reopen()
    assert harness.read_records("s") == recs and harness.get_offset("b", "s") == off
    assert harness.get_stream("s") == stream and [harness.get_peer(p) for p in ("a", "b")] == peers
    unread = [r.position for r in harness.read_records("s", harness.get_offset("b", "s").read_through_position)]
    assert unread == [4, 5]
    out = run_one(verify_worker, (str(harness.db_path), "s", OUTQ))
    assert out["integrity"] == "ok" and out["digest"] == digest
    assert [r[0] for r in out["records"]] == [r.record_id for r in recs]


@pytest.mark.m1_id("SQL-008")
def test_sql_008_core_starts_after_extension_schema_deletion(harness):
    seed(harness)
    claims = harness.claims(ManualClock())
    tok = claims.acquire("a", "s", "owner", lease_sec=30)
    with sqlite3.connect(harness.db_path) as c:
        ext = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")} - CORE_TABLES
        assert ext, "extension created no table: the deletion step would be vacuous"
        for t in ext:
            c.execute(f"DROP TABLE {t}")
    harness.reopen()
    assert set(harness.table_names()) == CORE_TABLES  # Core migrations never recreate extension tables
    rec = harness.append_record(req(key="after"))
    assert harness.read_records("s") == [rec]
    # extension tables are not required, and a late-installed extension still works
    again = harness.claims(ManualClock()).acquire("a", "s", "owner2", lease_sec=30)
    assert again.generation >= 1 and tok.owner_id == "owner"


@pytest.mark.m1_id("SQL-009")
def test_sql_009_workspace_generation_is_durable(harness):
    seed(harness)
    g = harness.workspace_generation()
    assert g and isinstance(g, str)
    for _ in range(3):
        assert harness.reopen().workspace_generation() == g
    from peerhub.m1.workspace import Workspace
    assert Workspace(harness.workspace).generation() == g  # a fresh Workspace object over the same dir
    other = CoreHarness(harness.workspace.parent / "other")
    assert other.workspace_generation() != g  # control: identities are per workspace


@pytest.mark.m1_id("SQL-010")
def test_sql_010_restore_generation_replacement_is_observable(harness, tmp_path):
    seed(harness)
    harness.append_record(req(key="k1"))
    clock = ManualClock()
    claims = harness.claims(clock)
    g1, tok1 = harness.workspace_generation(), claims.acquire("a", "s", "owner", lease_sec=300)
    assert claims.heartbeat(tok1).generation == tok1.generation  # control: valid under G1
    snap = tmp_path / "snapshot.db"
    src, dst = sqlite3.connect(harness.db_path), sqlite3.connect(snap)
    try:
        src.backup(dst)  # online backup = the supported snapshot primitive
    finally:
        src.close(), dst.close()
    g2 = harness.ws.restore_snapshot(snap)
    harness.reopen()
    assert g2 == harness.workspace_generation() and g2 != g1
    claims2 = harness.claims(clock)
    with pytest.raises(StaleClaimError):  # pre-G2 owner token is stale although its lease is unexpired
        claims2.heartbeat(tok1)
    with pytest.raises(StaleClaimError):
        claims2.assert_current(tok1)
    assert harness.get_stream("s") is not None and len(harness.read_records("s")) == 1  # restored data intact
    fresh = claims2.acquire("a", "s", "owner", lease_sec=300)  # fresh claim for G2 works
    assert fresh.workspace_generation == g2 and claims2.heartbeat(fresh)


@pytest.mark.m1_id("SQL-011")
def test_sql_011_server_computes_digest_and_ids_durably(harness):
    seed(harness)
    request = req(body={"k": [1, "two", None]}, key="d1", metadata={"m": 1}, targets=["b"])
    assert not {"record_id", "position", "payload_digest", "appended_at"} & set(request)
    rec = harness.append_record(request)
    harness.reopen()
    (stored,) = harness.read_records("s")
    assert stored == rec
    expected = compute_record_digest(AppendRequest(**request).model_dump())
    assert stored.payload_digest == expected
    assert stored.record_id and stored.position == 1 and stored.appended_at
    assert stored.appended_at != request["created_at"]  # server clock, not client supplied
