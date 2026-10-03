"""Wave 2: MIG-001/002 (+ TD-14 future-version rejection)."""
import sqlite3

import pytest

from peerhub.m1.migrations import CURRENT_VERSION, SchemaVersionError, run_migrations
from peerhub.m1.models import AppendRequest, compute_record_digest
from peerhub.m1.store import CoreStore
from tests.m1.harness.core import compute_state_digest
from tests.m1.harness.migration_fixtures import FIXTURE_MIGRATIONS, LEGACY_V0_SCHEMA
from tests.m1.harness.mp import OUTQ, run_one
from tests.m1.harness.mp_workers import migrate_crash_worker
from tests.m1.helpers import append_n, req, seed

pytestmark = [pytest.mark.migration, pytest.mark.sqlite]


class Boom(RuntimeError):
    pass


def _version(p):
    with sqlite3.connect(p) as c:
        return c.execute("PRAGMA user_version").fetchone()[0]


def _cols(p, table):
    with sqlite3.connect(p) as c:
        return [r[1] for r in c.execute(f"PRAGMA table_info({table})")]


def _tables(p):
    with sqlite3.connect(p) as c:
        return {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type IN ('table','index')")}


def _n_version_db(harness):
    seed(harness)
    append_n(harness, 4)
    harness.cas_offset("b", "s", 1, 2)
    assert _version(harness.db_path) == CURRENT_VERSION == FIXTURE_MIGRATIONS[-1].version - 1  # N-version fixture


@pytest.mark.m1_id("MIG-001")
def test_mig_001_interrupted_migration_does_not_bump_version_early(harness):
    _n_version_db(harness)
    n = CURRENT_VERSION
    before_state, before_schema = harness.state_digest(), _tables(harness.db_path)
    fired = []

    def fault(point):
        fired.append(point)
        if point == "migration.before_commit":
            # the step already ran inside the open transaction: prove the injection is mid-flight, not before the step
            with sqlite3.connect(harness.db_path, timeout=0) as c:
                assert c.execute("PRAGMA user_version").fetchone()[0] == n  # other connections never see the early bump
            raise Boom("crash before commit")

    with pytest.raises(Boom):
        run_migrations(harness.db_path, migrations=FIXTURE_MIGRATIONS, fault=fault)
    assert "migration.before_commit" in fired
    for reopen in range(2):
        harness.reopen()
        assert _version(harness.db_path) == n
        assert "note" not in _cols(harness.db_path, "peers") and _tables(harness.db_path) == before_schema
        assert harness.state_digest() == before_state
        assert len(harness.read_records("s")) == 4 and harness.get_offset("b", "s").read_through_position == 2
    # rerunning can complete exactly once
    assert run_migrations(harness.db_path, migrations=FIXTURE_MIGRATIONS) == [n + 1]
    assert _version(harness.db_path) == n + 1 and "note" in _cols(harness.db_path, "peers")
    assert run_migrations(harness.db_path, migrations=FIXTURE_MIGRATIONS) == []


@pytest.mark.m1_id("MIG-001")
def test_mig_001_killed_process_mid_migration_leaves_version_n(harness):
    _n_version_db(harness)
    n, state = CURRENT_VERSION, harness.state_digest()
    from tests.m1.harness.mp import CTX
    q = CTX.Queue()
    p = CTX.Process(target=migrate_crash_worker, args=(str(harness.db_path), q))
    p.start()
    p.join(120)
    assert not p.is_alive() and p.exitcode == 17, f"child must be killed by the injected fault, exit={p.exitcode}"
    harness.reopen()
    assert _version(harness.db_path) == n and "note" not in _cols(harness.db_path, "peers")
    assert harness.state_digest() == state and len(harness.read_records("s")) == 4
    with sqlite3.connect(harness.db_path) as c:
        assert c.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert run_migrations(harness.db_path, migrations=FIXTURE_MIGRATIONS) == [n + 1]
    assert _version(harness.db_path) == n + 1


def _seed_legacy_v0(path):
    """Unversioned prior schema with Peer/Stream/Record/Offset rows written by raw SQL (no store involved)."""
    c = sqlite3.connect(path)
    c.executescript(LEGACY_V0_SCHEMA)
    c.executemany("INSERT INTO peers (peer_id, created_at) VALUES (?, '2026-10-01T00:00:00Z')", [("a",), ("b",)])
    c.execute("INSERT INTO streams (stream_id, state, revision, created_at) VALUES ('s','OPEN',3,'2026-10-01T00:00:00Z')")
    c.executemany("INSERT INTO stream_members (stream_id, peer_id) VALUES ('s', ?)", [("b",), ("a",)])
    for pos, body in ((1, "x"), (2, {"k": [1, 2]}), (5, "gap")):  # gaps are legal (TD-01)
        request = dict(created_at="2026-10-01T00:00:00Z", stream_id="s", author_peer_id="a", kind="message", body=body,
                       targets=[], reply_to=None, refs=[], metadata={}, idempotency_key=f"k{pos}")
        c.execute(
            "INSERT INTO records (record_id, stream_id, position, author_peer_id, kind, body_json, idempotency_key,"
            " payload_digest, created_at, appended_at) VALUES (?, 's', ?, 'a', 'message', ?, ?, ?, ?, '2026-10-01T00:00:01Z')",
            (f"rec-{pos}", pos, __import__("json").dumps(body), f"k{pos}",
             compute_record_digest(AppendRequest(**request).model_dump()), "2026-10-01T00:00:00Z"))
    c.execute("INSERT INTO offsets (peer_id, stream_id, read_through_position, revision) VALUES ('b','s',2,4)")
    c.commit()
    c.close()


@pytest.mark.m1_id("MIG-002")
def test_mig_002_committed_migration_preserves_core_data_and_invariants(tmp_path):
    db = tmp_path / "legacy.db"
    _seed_legacy_v0(db)
    assert _version(db) == 0
    before = {t: _dump(db, t) for t in ("peers", "streams", "stream_members", "records", "offsets")}
    before_digest = compute_state_digest(db)
    assert run_migrations(db) == [1]  # advances exactly once, v0 -> v1
    assert _version(db) == CURRENT_VERSION == 1
    assert compute_state_digest(db) == before_digest  # logical data byte-identical
    assert {t: _dump(db, t) for t in before} == before
    store = CoreStore(db)
    assert [(r.position, r.record_id) for r in store.read_records("s", 0, 100)] == [(1, "rec-1"), (2, "rec-2"), (5, "rec-5")]
    for r in store.read_records("s", 0, 100):  # digests recompute from stored semantic fields
        request = r.model_dump(exclude={"record_id", "position", "payload_digest", "appended_at", "schema_version"})
        assert compute_record_digest(AppendRequest(**request).model_dump()) == r.payload_digest
    assert store.get_stream("s").members == ["b", "a"] and store.get_stream("s").revision == 3
    off = store.get_offset("b", "s")
    assert (off.read_through_position, off.revision) == (2, 4)
    nxt = store.append_record(**req(body="next", key="k-next"))  # invariants: position stays monotonic after migration
    assert nxt.position == 6 and store.advance_offset_cas("b", "s", 6, 4).revision == 5
    assert run_migrations(db) == []


def _dump(p, table):
    with sqlite3.connect(p) as c:
        return c.execute(f"SELECT * FROM {table} ORDER BY 1,2").fetchall()


ALL = ("peers", "streams", "stream_members", "records", "offsets")


def _full_snapshot(p):
    """Every original column of every Core table (peers.note added by the fixture step is excluded)."""
    out = {}
    with sqlite3.connect(p) as c:
        for t in ALL:
            cols = [r[1] for r in c.execute(f"PRAGMA table_info({t})") if r[1] != "note"]
            out[t] = c.execute(f"SELECT {', '.join(cols)} FROM {t} ORDER BY 1,2").fetchall()
        out["_order"] = c.execute("SELECT stream_id, peer_id FROM stream_members ORDER BY rowid").fetchall()
    return out


def assert_preserved(before, after):
    for t in before:
        assert after[t] == before[t], f"{t} changed by migration"


@pytest.mark.m1_id("MIG-002")
def test_mig_002_supplied_n_to_n_plus_1_migration_preserves_data(harness):
    _n_version_db(harness)
    harness.create_peer({"peer_id": "c", "display_name": "Cee", "metadata": {"x": [1]}})
    harness.cas_stream("s", harness.get_stream("s").revision, {"members": ["b", "c", "a"], "title": "T"})
    recs, off = harness.read_records("s"), harness.get_offset("b", "s")
    before = _full_snapshot(harness.db_path)
    assert before["peers"] and before["streams"] and len(before["stream_members"]) == 3
    assert run_migrations(harness.db_path, migrations=FIXTURE_MIGRATIONS) == [CURRENT_VERSION + 1]
    assert _version(harness.db_path) == CURRENT_VERSION + 1 and "note" in _cols(harness.db_path, "peers")
    assert_preserved(before, _full_snapshot(harness.db_path))  # Peers, Streams, members (incl. order), Records, Offsets
    assert harness.read_records("s") == recs and harness.get_offset("b", "s") == off
    assert harness.get_stream("s").members == ["b", "c", "a"] and harness.get_peer("c").display_name == "Cee"


@pytest.mark.m1_id("MIG-002")
@pytest.mark.parametrize("damage", ["delete_peer_row", "delete_member", "edit_stream", "reorder_members", "edit_peer"])
def test_mig_002_preservation_oracle_detects_damaging_migrations(harness, damage):
    from peerhub.m1.migrations import Migration
    _n_version_db(harness)
    before = _full_snapshot(harness.db_path)
    stmts = {
        "delete_peer_row": "DELETE FROM peers WHERE peer_id='b'",
        "delete_member": "DELETE FROM stream_members WHERE peer_id='b'",
        "edit_stream": "UPDATE streams SET title='tampered'",
        "reorder_members": "UPDATE stream_members SET rowid = rowid + 100 WHERE peer_id='a'",
        "edit_peer": "UPDATE peers SET display_name='tampered'",
    }

    def bad_step(conn):
        conn.execute("PRAGMA defer_foreign_keys = ON")
        conn.execute(stmts[damage])

    run_migrations(harness.db_path, migrations=[*FIXTURE_MIGRATIONS[:-1], Migration(CURRENT_VERSION + 1, "bad", bad_step)])
    with pytest.raises(AssertionError):
        assert_preserved(before, _full_snapshot(harness.db_path))


@pytest.mark.m1_id("MIG-002")
def test_migration_connection_enforces_foreign_keys_and_checks_before_commit(harness):
    from peerhub.m1.migrations import Migration, MigrationIntegrityError
    _n_version_db(harness)
    state = harness.state_digest()

    def orphan(conn):  # FK enforcement must be ON in migration connections: immediate violation
        conn.execute("INSERT INTO stream_members (stream_id, peer_id) VALUES ('s', 'ghost')")

    def deferred_orphan(conn):  # survives the statements, must be caught by foreign_key_check before COMMIT
        conn.execute("PRAGMA defer_foreign_keys = ON")
        conn.execute("INSERT INTO stream_members (stream_id, peer_id) VALUES ('s', 'ghost')")

    base = FIXTURE_MIGRATIONS[:-1]
    n = CURRENT_VERSION
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
        run_migrations(harness.db_path, migrations=[*base, Migration(n + 1, "orphan", orphan)])
    with pytest.raises(MigrationIntegrityError):
        run_migrations(harness.db_path, migrations=[*base, Migration(n + 1, "deferred", deferred_orphan)])
    assert _version(harness.db_path) == n and harness.state_digest() == state  # both rolled back
    assert run_migrations(harness.db_path, migrations=FIXTURE_MIGRATIONS) == [n + 1]  # positive control


def test_td14_future_schema_version_is_rejected_without_change(tmp_path):
    db = tmp_path / "future.db"
    CoreStore(db)
    with sqlite3.connect(db) as c:
        c.execute(f"PRAGMA user_version = {CURRENT_VERSION + 5}")
    state = compute_state_digest(db)
    with pytest.raises(SchemaVersionError, match="newer"):
        CoreStore(db)
    with pytest.raises(SchemaVersionError):
        run_migrations(db)
    assert _version(db) == CURRENT_VERSION + 5 and compute_state_digest(db) == state  # no downgrade, no repair


def test_td14_future_version_rejected_before_any_persistent_mutation(tmp_path):
    db = tmp_path / "nonwal.db"
    c = sqlite3.connect(db)
    c.execute("PRAGMA journal_mode = DELETE")
    c.execute("CREATE TABLE marker (x)")
    c.execute(f"PRAGMA user_version = {CURRENT_VERSION + 3}")
    c.commit()
    c.close()
    before = db.read_bytes()
    for opener in (lambda: run_migrations(db), lambda: CoreStore(db)):
        with pytest.raises(SchemaVersionError):
            opener()
        assert db.read_bytes() == before  # no WAL conversion (header bytes), no repair
        with sqlite3.connect(db) as c2:
            assert c2.execute("PRAGMA journal_mode").fetchone()[0].lower() == "delete"
        assert not (tmp_path / "nonwal.db-wal").exists()
    ok = tmp_path / "ok.db"  # control: a supported non-WAL database does migrate (and converts to WAL)
    sqlite3.connect(ok).close()
    assert run_migrations(ok) == [1]
