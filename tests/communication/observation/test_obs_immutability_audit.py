"""Audit of every append-only table owned by the observation extension (finding: pools lacked a no-replace guard)."""
import sqlite3
from contextlib import closing

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.observation]

POOL = {"schema_version": "1.0", "resource_pool_id": "P", "provider": "openai", "kind": "ACCOUNT"}


@pytest.mark.catalog_id("OBS-008")
def test_immutable_tables_reject_replace_update_delete_with_mutable_control(obs_h):
    from tests.communication.fakes.observation import FakeObservationSource, measured

    obs_h.register_pool(POOL)
    obs_h.capture("peer:a", "quota", FakeObservationSource(measured({"remaining_fraction": 0.5})), resource_pool_ref="P")
    state = lambda: (obs_h.obs_rows(), obs_h.pool_rows(), obs_h.full_state())
    before = state()
    obs_ins = ("(observation_id, subject_ref, kind, source, state, payload_json, observed_at, effective_at_us) "
               "VALUES ('obs-1','p','quota','s','MEASURED','{}','2026-10-01T00:00:00Z',0)")
    ops = {
        "resource_pools": ["INSERT OR REPLACE INTO resource_pools VALUES ('P','evil','QUOTA','{}')",
                           "REPLACE INTO resource_pools VALUES ('P','evil','QUOTA','{}')",
                           "UPDATE resource_pools SET provider='evil'", "DELETE FROM resource_pools"],
        "observations": ["INSERT OR REPLACE INTO observations " + obs_ins, "UPDATE observations SET state='STALE'", "DELETE FROM observations"],
    }
    for recursive in ("OFF", "ON"):
        for table, stmts in ops.items():
            for sql in stmts:
                with closing(sqlite3.connect(obs_h.db_path)) as c:
                    c.execute("PRAGMA recursive_triggers = " + recursive)
                    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
                        c.execute(sql)
                assert state() == before, (table, sql, recursive)
    with obs_h.obs._tx() as conn:  # the store's own writer connection runs with recursive_triggers ON
        assert conn.execute("PRAGMA recursive_triggers").fetchone()[0] == 1
    with closing(sqlite3.connect(obs_h.db_path)) as c:
        owned = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
        assert owned - {"peers", "streams", "stream_members", "records", "offsets"} == {"observations", "resource_pools"}  # nothing unaudited
        # mutable control: identical statement shapes succeed on a genuinely mutable table, so the rejections above are not vacuous
        c.execute("CREATE TABLE mutable_control (id TEXT PRIMARY KEY, v TEXT)")
        c.execute("INSERT INTO mutable_control VALUES ('a','1')")
        c.execute("INSERT OR REPLACE INTO mutable_control VALUES ('a','2')")
        c.execute("UPDATE mutable_control SET v='3'")
        assert c.execute("SELECT v FROM mutable_control").fetchone()[0] == "3"
        c.execute("DELETE FROM mutable_control")
        assert c.execute("SELECT COUNT(*) FROM mutable_control").fetchone()[0] == 0
        c.execute("DROP TABLE mutable_control")
