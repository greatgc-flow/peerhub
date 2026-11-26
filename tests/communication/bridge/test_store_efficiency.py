"""Store startup, snapshot, and query cost regressions without provider calls."""
from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path
from tempfile import NamedTemporaryFile

import pytest

from peerhub.core.migrations import get_migration_registry, run_migrations
from peerhub.core.store import CoreStore
from peerhub.extensions import ask, ask_budgets, bridge_ledger, bridge_registry, delivery_projections, monitor_queries
from peerhub.extensions.bridge import Bridge
from peerhub.extensions.bridge_claims import ClaimStore, ClaimToken
from peerhub.extensions.observation import ObservationStore


@pytest.fixture
def db_path():
    with NamedTemporaryFile(suffix=".db", delete=False) as handle:
        db = Path(handle.name)
    try:
        yield db
    finally:
        db.unlink()


@pytest.fixture
def bridge(db_path):
    store = CoreStore(db_path)
    return Bridge(store, ClaimStore(db_path, lambda: "workspace"))


def _insert_deliveries(conn, rows):
    conn.executemany(
        "INSERT INTO bridge_deliveries(delivery_id, seq, stream_id, peer_id, record_id, record_position,"
        "attempt, certainty, claim_generation, terminal_at) VALUES (?,?,'s','cx',?, ?, 1,'NOT_STARTED',1,?)",
        [(delivery_id, seq, f"r{seq}", seq, terminal_at) for delivery_id, seq, terminal_at in rows],
    )


def test_current_opens_and_delivery_reads_use_committed_snapshot(bridge, db_path, monkeypatch):
    ObservationStore(db_path)
    with bridge.claims.transaction() as conn:
        _insert_deliveries(conn, [("d1", 1, None)])
        conn.execute("INSERT INTO bridge_evidence(delivery_id,stream_id,peer_id,kind,detail) "
                     """VALUES ('d1','s','cx','execution_metadata','{"ag_transport":"stored"}')""")

    def forbidden(*args, **kwargs):
        pytest.fail("a current schema open or ordinary lookup took the writer lock")

    monkeypatch.setattr(ClaimStore, "_tx", forbidden)
    monkeypatch.setattr(ObservationStore, "_tx", forbidden)
    with closing(sqlite3.connect(db_path)) as writer, writer:
        writer.execute("BEGIN IMMEDIATE")
        _insert_deliveries(writer, [("d2", 2, None)])
        reopened = Bridge(bridge.store, ClaimStore(db_path, lambda: "workspace"))
        ObservationStore(db_path)
        token = ClaimToken("workspace", 1, "owner", "cx", "s")
        assert reopened.latest_delivery_id(token) == "d1"
        assert reopened._inflight(token)["delivery_id"] == "d1"
        assert reopened.delivery_details("d1") == {"ag_transport": "stored"}
        assert reopened.execution_evidence("r1", "cx")["deliveries"][0]["delivery_id"] == "d1"
        assert reopened.execution_evidence("r2", "cx") == {"deliveries": [], "events": []}


def test_execution_evidence_pins_both_queries_to_one_snapshot(bridge, db_path, monkeypatch):
    with bridge.claims.transaction() as conn:
        _insert_deliveries(conn, [("d1", 1, None)])
    original = sqlite3.connect

    class InterleavedRead(sqlite3.Connection):
        def execute(self, sql, parameters=(), /):
            if sql.startswith("SELECT e.* FROM bridge_evidence"):
                with closing(original(db_path)) as writer, writer:
                    writer.execute("INSERT INTO bridge_evidence(delivery_id,stream_id,peer_id,kind,detail) "
                                   "VALUES ('d1','s','cx','new','{}')")
            return super().execute(sql, parameters)

    monkeypatch.setattr(sqlite3, "connect", lambda *args, **kwargs: original(*args, factory=InterleavedRead, **kwargs))
    evidence = bridge.execution_evidence("r1", "cx")
    assert len(evidence["deliveries"]) == 1
    assert not evidence["events"]
    with closing(original(db_path)) as conn:
        assert conn.execute("SELECT COUNT(*) FROM bridge_evidence").fetchone()[0] == 1


def test_gate_probes_outside_writer_and_rechecks_changed_claim(bridge, db_path, monkeypatch):
    with bridge.claims.transaction() as conn:
        conn.execute("INSERT INTO bridge_claims VALUES ('s','cx','owner',1,100,60,'workspace')")
        conn.execute("INSERT INTO bridge_claim_callers VALUES ('s','cx',1,'{}')")
    claims = ClaimStore(db_path, lambda: "workspace", clock=lambda: 50)
    probes = []

    def probe(_):
        probes.append(True)
        with closing(sqlite3.connect(db_path, timeout=0)) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("UPDATE bridge_claims SET expires_at=101 WHERE stream_id='s'")
        return False

    monkeypatch.setattr(bridge_ledger, "claim_wrapper_alive", probe)
    busy = bridge.launch_check(claims)
    with claims.transaction() as conn:
        assert busy(conn, "workspace", 50)
    assert len(probes) == 1
    busy = bridge.launch_check(claims)
    with claims.transaction() as conn:
        assert not busy(conn, "workspace", 50)
    assert len(probes) == 2


def test_new_indexes_install_on_existing_store(bridge, db_path):
    indexes = ("idx_bc_workspace_expiry", "idx_bd_seq", "idx_bd_scope_seq", "idx_bd_scope_unresolved",
               "idx_records_ask_target", "idx_bd_confirmed_end")
    with bridge.claims.transaction() as conn:
        _insert_deliveries(conn, [("d1", 1, None)])
        for name in indexes:
            conn.execute(f"DROP INDEX {name}")
    claims = ClaimStore(db_path, lambda: "workspace")
    Bridge(bridge.store, claims)
    with claims.snapshot() as conn:
        assert set(indexes) <= {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")}
        assert conn.execute("SELECT delivery_id FROM bridge_deliveries").fetchone()[0] == "d1"


def test_observation_reopen_repairs_changed_trigger_definition(db_path):
    ObservationStore(db_path)
    with closing(sqlite3.connect(db_path)) as conn:
        name = "observations_no_update"
        expected = conn.execute("SELECT sql FROM sqlite_master WHERE name=?", (name,)).fetchone()[0]
        conn.execute(f"DROP TRIGGER {name}")
        conn.execute(f"CREATE TRIGGER {name} BEFORE UPDATE ON observations BEGIN SELECT 1; END")
    ObservationStore(db_path)
    with closing(sqlite3.connect(db_path)) as conn:
        assert conn.execute("SELECT sql FROM sqlite_master WHERE name=?", (name,)).fetchone()[0] == expected


def test_registry_reopen_repairs_missing_stats_rows(bridge):
    with bridge.claims.transaction() as conn:
        conn.execute("INSERT INTO bridge_session_history(session_handle,stream_id,peer_id,runtime_kind,"
                     "external_session_id,session_generation,adapter_fingerprint,binding,resumable,state,last_seen,created_at) "
                     "VALUES ('handle','s','cx','cx','external',1,'fp','binding',1,'FRESH',0,0)")
        assert bridge_registry.needs_projection_repair(conn)
    for _ in range(2):
        Bridge(bridge.store, bridge.claims)
        with bridge.claims.transaction() as conn:
            assert conn.execute("SELECT session_handle FROM bridge_session_monitor_stats").fetchone()[0] == "handle"
            assert not bridge_registry.needs_projection_repair(conn)
            conn.execute("DELETE FROM bridge_session_monitor_stats WHERE session_handle='handle'")
            assert bridge_registry.needs_projection_repair(conn)


def test_light_preflight_keeps_full_check_for_existing_migration(migration_db):
    db, statements = migration_db
    assert CoreStore(db, light_preflight=True).preflight_mode == "full"
    assert any("PRAGMA quick_check" in sql for sql in statements)
    statements.clear()
    assert CoreStore(db, light_preflight=True).preflight_mode == "light"
    assert not any("PRAGMA quick_check" in sql for sql in statements)
    assert CoreStore(db).preflight_mode == "full"
    assert any("PRAGMA quick_check" in sql for sql in statements)


@pytest.fixture
def migration_db(db_path, monkeypatch):
    run_migrations(db_path, migrations=get_migration_registry()[:-1])
    statements = []
    original = sqlite3.connect

    def connect(*args, **kwargs):
        conn = original(*args, **kwargs)
        conn.set_trace_callback(statements.append)
        return conn

    monkeypatch.setattr(sqlite3, "connect", connect)
    return db_path, statements


def test_activity_uses_scope_seek_and_preserves_superseded_rows(bridge):
    with bridge.claims.transaction() as conn:
        conn.execute("INSERT INTO bridge_claims VALUES ('s','cx','owner',1,100,60,'workspace')")
        _insert_deliveries(conn, [("older", 1, None), ("latest", 2, None)])
        conn.execute("UPDATE bridge_deliveries SET certainty='STARTED' WHERE delivery_id='older'")
        expected = [(2, 0), (1, 1)]
        plan = conn.execute("EXPLAIN QUERY PLAN " + monitor_queries._ACTIVITY_SQL,
                            monitor_queries._activity_params(50, "workspace", False)).fetchall()
        assert "idx_bd_scope_seq" in str([tuple(row) for row in plan])
        assert "idx_bd_scope_unresolved" in str([tuple(row) for row in plan])
        assert [(row["seq"], row["sup"]) for row in monitor_queries.activity_rows(conn, 50, "workspace", False)] == expected
        conn.execute("DROP INDEX idx_bd_scope_seq")
        conn.execute("DROP INDEX idx_bd_scope_unresolved")
        assert [(row["seq"], row["sup"]) for row in monitor_queries.activity_rows(conn, 50, "workspace", False)] == expected


def test_prompt_guard_seeks_target_ranges_and_preserves_legacy_mismatches(bridge):
    with bridge.claims.transaction() as conn:
        conn.executemany(
            "INSERT INTO records(record_id,stream_id,position,author_peer_id,kind,targets_json,metadata_json,"
            "idempotency_key,payload_digest,created_at,appended_at) VALUES (?,'s',?,'user','prompt',?,?,?,'d','t','t')",
            [(f"r{i}", i, '["cx"]', '{"binding":"b","runtime_kind":"cx"}', f"ask:{i}") for i in range(1, 300)]
            + [("foreign", 300, '["cc"]', '{}', 'ask:foreign'),
               ("malformed", 301, '["cc"', '{"binding":"","runtime_kind":"cx"}', 'ask:malformed'),
               ("legacy", 302, '["cc","cx"]', '{"binding":null,"runtime_kind":null}', 'ask:legacy'),
               ("single", 303, '["cc"]', '{"binding":"","runtime_kind":"cx"}', 'ask:single')],
        )
        sql = "EXPLAIN QUERY PLAN " + ask_budgets.STREAM_PEER_CONFLICTS_SQL
        plan = " ".join(str(row[3]) for row in conn.execute(sql, {"stream": "s", "peer": "cx"}))
        assert plan.count("idx_records_ask_target") == 3 and "stream_id=? AND <expr>" in plan, plan
        with pytest.raises(ask.StreamPeerMismatchError, match="mixed historical prompt targets"):
            ask._check_stream_peer(conn, "s", "cx")
        with pytest.raises(ask.StreamPeerMismatchError, match="already bound to ask target peer 'cx'"):
            ask._check_stream_peer(conn, "s", "cc")


def test_existing_delivery_projection_updates_without_history_scan(bridge):
    with bridge.claims.transaction() as conn:
        _insert_deliveries(conn, [("d1", 1, None), ("d2", 2, None)])
        first, second = conn.execute("SELECT * FROM bridge_deliveries ORDER BY seq").fetchall()
        delivery_projections.record_delivery_created(conn, first)
        statements = []
        conn.set_trace_callback(statements.append)
        delivery_projections.record_delivery_created(conn, second)
        conn.set_trace_callback(None)
        assert not any("FROM bridge_deliveries" in stmt for stmt in statements)
        row = conn.execute("SELECT not_started_count,delivery_high_water_seq,delivery_complete "
                           "FROM bridge_monitor_delivery_scope").fetchone()
        assert tuple(row) == (2, 2, 1)


def test_quota_competitor_cutoff_keeps_exact_and_unknown_ends(bridge):
    with bridge.claims.transaction() as conn:
        rows = [("past", 1, "1970-01-01T00:00:01Z"), ("same_second", 2, "1970-01-01T00:00:02Z"),
                ("unknown", 3, None), ("legacy", 4, "1970-01-01T09:00:02.750+09:00"),
                ("invalid", 5, "1900-02-29T00:00:00Z"), ("later", 6, "1970-01-01T00:00:03Z")]
        _insert_deliveries(conn, rows)
        conn.executemany("INSERT INTO bridge_invocations VALUES (?,1,'s','cx',1,NULL,NULL)",
                         [(delivery_id,) for delivery_id, _, _ in rows])
        statements = []
        conn.set_trace_callback(statements.append)
        competitors = ask_budgets._competing_calls(conn, "own", "cx", 2.5, 3.0)
        conn.set_trace_callback(None)
        assert sorted(item[1] for item in competitors if item[1] is not None) == [2.75, 3]
        assert competitors.count((None, None, None)) == 2
        plan = " ".join(str(row[3]) for row in conn.execute("EXPLAIN QUERY PLAN " + statements[0]))
        assert plan.count("idx_bd_confirmed_end") == 2 and "<expr>>?" in plan, plan
