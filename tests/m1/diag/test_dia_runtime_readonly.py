"""Runtime read-only enforcement (sqlite authorizer) for Diag and the Observation store's read connections.
Static scanning of SQL text can never be complete (ARCH-004 gate finding), so denial is also proven at runtime."""
import sqlite3
from contextlib import closing

import pytest

from peerhub.extensions.diag import ReadonlyDiag
from tests.m1.diag.test_dia_diag import file_hash, populated
from tests.m1.fakes.observation import FakeObservationSource, measured

pytestmark = [pytest.mark.integration, pytest.mark.diag]


def _writable_factory(box):
    def factory(path):
        conn = sqlite3.connect(path, isolation_level=None)  # deliberately WRITABLE file handle: only the authorizer can stop writes
        conn.row_factory = sqlite3.Row
        box.append(conn)
        return conn
    return factory


@pytest.mark.m1_id("ARCH-004")
def test_dia_authorizer_denies_every_non_read_operation_at_runtime(tmp_path):
    h = populated(tmp_path)
    other = tmp_path / "other.db"
    with closing(sqlite3.connect(other)) as c:
        c.execute("CREATE TABLE t (x)")
        c.commit()
    before, other_hash = (h.state_digest(), h.row_counts(), h.table_digests()), file_hash(other)
    built = chr(73) + "NSERT INTO peers (peer_id, created_at) VALUES ('evil', 'x')"  # SQL text assembled at runtime
    built2 = "".join(["DE", "LETE FROM records"])
    denied = [built, built2, "INSERT INTO peers (peer_id, created_at) VALUES ('e', 'x')", "UPDATE peers SET display_name='x'",
              "INSERT OR REPLACE INTO offsets (peer_id, stream_id, read_through_position, revision) VALUES ('b','s',9,9)",
              "CREATE TABLE evil (x)", "CREATE TEMP TABLE evil (x)", "CREATE VIEW v AS SELECT 1",
              "CREATE TRIGGER tr AFTER INSERT ON peers BEGIN SELECT 1; END", "DROP TABLE peers", "ALTER TABLE peers ADD COLUMN z",
              "ATTACH DATABASE '" + other.as_posix() + "' AS o", "PRAGMA query_only = OFF", "PRAGMA query_only = 0",
              "PRAGMA journal_mode = DELETE", "PRAGMA wal_checkpoint(TRUNCATE)", "PRAGMA user_version = 5", "PRAGMA writable_schema = ON",
              "PRAGMA foreign_keys = OFF", "REINDEX", "COMMIT", "SAVEPOINT sp"]
    results, box = {}, []

    def hook(section):
        if section != "peers":
            return
        conn = box[0]
        for sql in denied:
            with pytest.raises(sqlite3.DatabaseError, match="not authorized"):
                conn.execute(sql)
        with pytest.raises(sqlite3.DatabaseError):  # VACUUM inside the read transaction: refused by SQLite itself
            conn.execute("VACUUM")
        with pytest.raises(sqlite3.DatabaseError):  # query_only could not be switched off, so a follow-up write is impossible
            conn.execute("INSERT INTO peers (peer_id, created_at) VALUES ('after', 'x')")
        results["peers"] = conn.execute("SELECT COUNT(*) FROM peers").fetchone()[0]  # positive controls: permitted reads work
        results["fn"] = conn.execute("SELECT lower('ABC'), (SELECT COUNT(*) FROM sqlite_master)").fetchone()[0]
        results["qo"] = conn.execute("PRAGMA query_only").fetchone()[0]

    rep = ReadonlyDiag(h.db_path, connection_factory=_writable_factory(box), section_hook=hook).render()
    assert rep.status == "OK" and results == {"peers": 2, "fn": "abc", "qo": 1}
    assert (h.state_digest(), h.row_counts(), h.table_digests()) == before and file_hash(other) == other_hash
    with closing(sqlite3.connect(h.db_path)) as c:
        names = {r[0] for r in c.execute("SELECT name FROM sqlite_master")}
        assert "evil" not in names and "v" not in names and "tr" not in names and "z" not in str(c.execute("PRAGMA table_info(peers)").fetchall())


@pytest.mark.m1_id("ARCH-004")
def test_observation_store_read_connections_deny_writes(obs_h):
    obs_h.capture("peer:a", "quota", FakeObservationSource(measured({"remaining_fraction": 0.5})))
    before = obs_h.full_state()
    with obs_h.obs._read() as conn:
        for sql in ("UPDATE observations SET state='STALE'", "DELETE FROM observations", "INSERT INTO resource_pools VALUES ('p','x','QUOTA','{}')",
                    "CREATE TEMP TABLE t (x)", "PRAGMA query_only = OFF", "PRAGMA journal_mode = DELETE"):
            with pytest.raises(sqlite3.DatabaseError, match="not authorized"):
                conn.execute(sql)
        assert conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == 1  # reads still work
    assert obs_h.full_state() == before
    assert obs_h.latest("peer:a", "quota").observation.payload == {"remaining_fraction": 0.5}
