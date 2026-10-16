"""Slice 1 tests: central connection factory, unified migration registry,
user_version 3->4 bump, and legacy schema regression.
(docs/design/db-schema-v2-2026-10-16.md section 1, 4, 5, 6).
"""
from __future__ import annotations

import sqlite3
from typing import Any

import pytest

from peerhub.core.connection import create_connection
import peerhub.core.connection as conn_mod
from peerhub.core.migrations import MigrationStep, get_migration_registry, run_migrations
from peerhub.core.schema_version import SUPPORTED_SCHEMA_VERSION, SchemaVersionError, future_schema_message
from peerhub.core.store import CoreStore
from peerhub.extensions.bridge import Bridge
from peerhub.extensions.bridge_claims import ClaimStore
from peerhub.extensions.observation import ObservationStore

pytestmark = [pytest.mark.migration, pytest.mark.sqlite]


def _version(path: str | sqlite3.Connection) -> int:
    if isinstance(path, sqlite3.Connection):
        return path.execute("PRAGMA user_version").fetchone()[0]
    with sqlite3.connect(str(path)) as conn:
        return conn.execute("PRAGMA user_version").fetchone()[0]


def _seed_v3_db(path) -> None:
    v3_steps = [m for m in get_migration_registry() if m.version <= 3]
    run_migrations(path, migrations=v3_steps)
    with sqlite3.connect(str(path)) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute(
            "INSERT INTO peers (peer_id, created_at) VALUES ('p1', '2026-10-16T00:00:00Z')"
        )
        conn.execute(
            "INSERT INTO streams (stream_id, state, revision, created_at) "
            "VALUES ('s1', 'OPEN', 1, '2026-10-16T00:00:00Z')"
        )
        conn.execute(
            "INSERT INTO stream_members (stream_id, peer_id) VALUES ('s1', 'p1')"
        )
        conn.execute(
            "INSERT INTO offsets (peer_id, stream_id, read_through_position, revision) "
            "VALUES ('p1', 's1', 0, 1)"
        )
        conn.commit()


# -------------------------------------------------------------------------
# 1. Connection Factory & Core Routing
# -------------------------------------------------------------------------
def test_connection_factory_sets_wal_busy_timeout_fk_recursive_triggers(tmp_path, monkeypatch):
    db = tmp_path / "cf_test.db"
    conn_w = create_connection(db)
    try:
        assert conn_w.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        assert conn_w.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
        assert conn_w.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert conn_w.execute("PRAGMA recursive_triggers").fetchone()[0] == 1
    finally:
        conn_w.close()

    conn_ro = create_connection(db, readonly=True)
    try:
        assert conn_ro.execute("PRAGMA query_only").fetchone()[0] == 1
        assert conn_ro.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert conn_ro.execute("PRAGMA recursive_triggers").fetchone()[0] == 1
        assert conn_ro.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
    finally:
        conn_ro.close()

    # Verify CoreStore routes checkout through create_connection
    calls: list[dict[str, Any]] = []
    real_create = conn_mod.create_connection

    def spy_create(path, *, readonly=False, timeout_ms=5000):
        calls.append({"path": path, "readonly": readonly, "timeout_ms": timeout_ms})
        return real_create(path, readonly=readonly, timeout_ms=timeout_ms)

    monkeypatch.setattr(conn_mod, "create_connection", spy_create)

    store = CoreStore(db)
    calls.clear()

    c = store.connect()
    c.close()
    assert any(call["readonly"] is False for call in calls), "store.connect() must call create_connection(readonly=False)"

    calls.clear()
    with store.read_uow() as ruow:
        ruow.execute("SELECT 1")
    assert any(call["readonly"] is True for call in calls), "store.read_uow() must call create_connection(readonly=True)"


# -------------------------------------------------------------------------
# 2. Store open migrates v3 to v4 and sets user_version=4 last
# -------------------------------------------------------------------------
def test_store_open_migrates_v3_to_v4_and_sets_user_version_4_last(tmp_path):
    db = tmp_path / "migrate_v3_v4.db"
    _seed_v3_db(db)
    assert _version(db) == 3

    executed_statements: list[str] = []

    run_migrations(db, trace=lambda s: executed_statements.append(s.strip()))

    assert _version(db) == 4

    # Statement-order trace adjacency: user_version assignment is the last statement before COMMIT,
    # with no filtering that hides intervening SQL
    commit_indices = [i for i, s in enumerate(executed_statements) if s.upper() == "COMMIT"]
    assert len(commit_indices) >= 1, "Expected COMMIT in trace"
    commit_idx = commit_indices[-1]
    preceding_stmt = executed_statements[commit_idx - 1].upper()
    assert preceding_stmt == "PRAGMA USER_VERSION = 4", (
        f"Expected PRAGMA USER_VERSION = 4 immediately adjacent before COMMIT, got: {preceding_stmt}"
    )


# -------------------------------------------------------------------------
# 3. Future schema refusal clean error (v0.16.0 guard & CoreStore)
# -------------------------------------------------------------------------
def test_future_schema_refusal_clean_error(tmp_path):
    db = tmp_path / "v4_refusal.db"
    _seed_v3_db(db)
    with sqlite3.connect(str(db)) as conn:
        conn.execute("PRAGMA user_version = 4")
        conn.commit()

    # Frozen v0.16.0 refusal guard check
    def v0_16_0_refusal_guard(path):
        with sqlite3.connect(str(path)) as conn:
            found = conn.execute("PRAGMA user_version").fetchone()[0]
        if found > 3:
            raise SchemaVersionError(future_schema_message(found, 3))

    with pytest.raises(SchemaVersionError) as exc_info:
        v0_16_0_refusal_guard(db)
    assert "database schema version 4 is newer than supported 3" in str(exc_info.value)
    assert "nothing was changed" in str(exc_info.value)

    assert SUPPORTED_SCHEMA_VERSION == 4

    # Future schema version 5 refusal by current binary
    db_v5 = tmp_path / "v5.db"
    _seed_v3_db(db_v5)
    with sqlite3.connect(str(db_v5)) as conn:
        conn.execute("PRAGMA user_version = 5")
        conn.commit()

    with pytest.raises(SchemaVersionError) as exc_v5:
        CoreStore(db_v5)
    assert "database schema version 5 is newer than supported 4" in str(exc_v5.value)


# -------------------------------------------------------------------------
# 4. Migration registry ordering and uniqueness
# -------------------------------------------------------------------------
def test_migration_registry_is_single_ordered_owner():
    registry = get_migration_registry()
    assert isinstance(registry, list)
    assert len(registry) >= 4

    versions = [m.version for m in registry]
    assert versions == sorted(versions), "Migration registry must be ordered by version"
    assert len(versions) == len(set(versions)), "Migration IDs must be unique"

    for m in registry:
        assert isinstance(m, MigrationStep)
        assert m.component
        assert m.name
        assert m.checksum
        assert callable(m.apply)


# -------------------------------------------------------------------------
# 5. Parent-table rebuild preserves cascade children under foreign_keys=OFF
# -------------------------------------------------------------------------
def test_child_rows_survive_parent_table_rebuild(tmp_path):
    db = tmp_path / "rebuild_test.db"
    _seed_v3_db(db)
    with sqlite3.connect(str(db)) as conn:
        before_members = conn.execute("SELECT stream_id, peer_id FROM stream_members ORDER BY 1, 2").fetchall()
        assert len(before_members) > 0

    # Migration step that drops and rebuilds streams (parent with stream_members children)
    def rebuild_streams(conn: sqlite3.Connection) -> None:
        conn.execute("CREATE TABLE streams_tmp AS SELECT * FROM streams")
        conn.execute("DROP TABLE streams")
        conn.execute(
            "CREATE TABLE streams ("
            "stream_id TEXT PRIMARY KEY, title TEXT, state TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 1, "
            "metadata_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL)"
        )
        conn.execute("INSERT INTO streams SELECT * FROM streams_tmp")
        conn.execute("DROP TABLE streams_tmp")

    step_rebuild = MigrationStep(4, "core", "rebuild_parent_table", "chk_reb", rebuild_streams)

    executed_statements: list[str] = []
    run_migrations(
        db,
        migrations=[*get_migration_registry()[:3], step_rebuild],
        trace=lambda s: executed_statements.append(s.strip()),
    )

    assert _version(db) == 4
    with sqlite3.connect(str(db)) as conn:
        after_members = conn.execute("SELECT stream_id, peer_id FROM stream_members ORDER BY 1, 2").fetchall()
        assert after_members == before_members, "Child rows in stream_members must survive parent table rebuild"
        violations = conn.execute("PRAGMA foreign_key_check").fetchall()
        assert violations == []

    # Trace adjacency: user_version is adjacent to COMMIT
    commit_idx = [i for i, s in enumerate(executed_statements) if s.upper() == "COMMIT"][-1]
    assert executed_statements[commit_idx - 1].upper() == "PRAGMA USER_VERSION = 4"


# -------------------------------------------------------------------------
# 6. Rollback on migration failure
# -------------------------------------------------------------------------
def test_migration_failure_rolls_back_user_version_and_ddl(tmp_path):
    db_fault = tmp_path / "fault.db"
    _seed_v3_db(db_fault)
    assert _version(db_fault) == 3

    def step_with_table(conn: sqlite3.Connection) -> None:
        conn.execute("CREATE TABLE canary_table (id TEXT PRIMARY KEY)")

    step_fail = MigrationStep(4, "core", "canary_migration", "chk_canary", step_with_table)

    class InjectFault(RuntimeError):
        pass

    with pytest.raises(InjectFault):
        run_migrations(
            db_fault,
            migrations=[*get_migration_registry()[:3], step_fail],
            fault=lambda point: (_ for _ in ()).throw(InjectFault("abort before commit"))
            if point == "migration.before_commit"
            else None,
        )

    assert _version(db_fault) == 3
    with sqlite3.connect(str(db_fault)) as conn:
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='canary_table'"
            ).fetchone()[0]
            == 0
        )


# -------------------------------------------------------------------------
# 7. Legacy released-schema regression (v0.16.0 extension schema self-upgrade)
# -------------------------------------------------------------------------
def test_legacy_released_schema_regression_v0_16_0(tmp_path):
    """Build a v3 store with Core tables PLUS released extension tables as v0.16.0 created them
    (bridge_deliveries without session_handle, bridge_sessions without updated_at, etc.),
    run Store open + Bridge/registry/observation constructors with current code, assert reaches
    user_version 4, extension tables self-upgrade as today, data preserved, no 'no such column' errors.
    """
    db = tmp_path / "legacy_v0_16_0.db"
    _seed_v3_db(db)
    assert _version(db) == 3

    # Create released extension tables exactly as v0.16.0 created them
    with sqlite3.connect(str(db)) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        # v0.16.0 bridge_deliveries (WITHOUT session_handle, WITHOUT session_snapshot_json)
        conn.execute("""
            CREATE TABLE bridge_deliveries (
                delivery_id TEXT PRIMARY KEY, seq INTEGER NOT NULL, stream_id TEXT NOT NULL, peer_id TEXT NOT NULL,
                record_id TEXT NOT NULL, record_position INTEGER NOT NULL, attempt INTEGER NOT NULL, certainty TEXT NOT NULL,
                claim_generation INTEGER NOT NULL, external_session_id TEXT, session_generation INTEGER, execution_id TEXT,
                result_json TEXT, result_digest TEXT, terminal_at TEXT, response_record_id TEXT, acked INTEGER NOT NULL DEFAULT 0,
                reconcile_record_id TEXT, UNIQUE (stream_id, peer_id, record_id, attempt)
            )
        """)
        conn.execute("""
            CREATE TABLE bridge_evidence (
                seq INTEGER PRIMARY KEY AUTOINCREMENT, delivery_id TEXT, stream_id TEXT NOT NULL, peer_id TEXT NOT NULL,
                kind TEXT NOT NULL, certainty TEXT, detail TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE bridge_session_events (
                seq INTEGER PRIMARY KEY AUTOINCREMENT, stream_id TEXT NOT NULL, peer_id TEXT NOT NULL, event TEXT NOT NULL,
                from_state TEXT NOT NULL, to_state TEXT NOT NULL, generation INTEGER NOT NULL, detail TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE bridge_reconciliations (
                delivery_id TEXT PRIMARY KEY, reconcile_record_id TEXT NOT NULL, decision TEXT NOT NULL, consumed_attempt INTEGER
            )
        """)
        conn.execute("""
            CREATE TABLE bridge_controls (
                record_id TEXT NOT NULL, stream_id TEXT NOT NULL, peer_id TEXT NOT NULL, kind TEXT NOT NULL, position INTEGER NOT NULL,
                effect TEXT NOT NULL, outcome TEXT, error TEXT, delivery_id TEXT, lease_id TEXT, lease_expires REAL,
                attempts INTEGER NOT NULL DEFAULT 0, accepted_seq INTEGER NOT NULL DEFAULT 0, target_delivery_id TEXT,
                target_session_id TEXT, target_claim_generation INTEGER, PRIMARY KEY (record_id, peer_id)
            )
        """)
        conn.execute("""
            CREATE TABLE bridge_invocations (
                delivery_id TEXT NOT NULL, invocation_no INTEGER NOT NULL, stream_id TEXT NOT NULL, peer_id TEXT NOT NULL,
                claim_generation INTEGER NOT NULL CHECK (claim_generation >= 0), external_session_id TEXT, session_generation INTEGER,
                PRIMARY KEY (delivery_id, invocation_no), CHECK (invocation_no >= 1)
            )
        """)
        conn.execute("""
            CREATE TABLE bridge_invocation_resolutions (
                delivery_id TEXT NOT NULL, invocation_no INTEGER NOT NULL CHECK (invocation_no >= 1),
                reason TEXT NOT NULL CHECK (reason IN ('prespawn_failure', 'control_halt', 'promoted_may_have_started')),
                claim_generation INTEGER NOT NULL CHECK (claim_generation >= 0),
                PRIMARY KEY (delivery_id, invocation_no)
            )
        """)
        conn.execute("""
            CREATE TABLE bridge_claim_generations (
                peer_id TEXT NOT NULL, stream_id TEXT NOT NULL, token_id TEXT NOT NULL, owner_id TEXT NOT NULL,
                generation INTEGER NOT NULL CHECK (generation >= 1), acquired_at REAL NOT NULL, lease_sec REAL NOT NULL,
                PRIMARY KEY (peer_id, stream_id, generation), UNIQUE (token_id)
            )
        """)
        conn.execute("""
            CREATE TABLE bridge_finalizations (
                token_id TEXT PRIMARY KEY REFERENCES bridge_claim_generations(token_id), result_json TEXT NOT NULL
            )
        """)
        # Pre-refactor legacy bridge_sessions (before pointer table was split; WITHOUT updated_at)
        conn.execute("""
            CREATE TABLE bridge_sessions (
                stream_id TEXT NOT NULL, peer_id TEXT NOT NULL, runtime_kind TEXT NOT NULL,
                external_session_id TEXT NOT NULL, session_generation INTEGER NOT NULL,
                adapter_fingerprint TEXT NOT NULL, binding TEXT NOT NULL, resumable INTEGER NOT NULL,
                state TEXT NOT NULL, last_seen REAL NOT NULL, PRIMARY KEY(stream_id, peer_id)
            )
        """)

        # Insert legacy seed data to assert data preservation
        conn.execute("""
            INSERT INTO bridge_deliveries (
                delivery_id, seq, stream_id, peer_id, record_id, record_position,
                attempt, certainty, claim_generation, external_session_id, session_generation
            ) VALUES (
                'del-1', 1, 's1', 'p1', 'rec-1', 1, 1, 'TERMINAL', 1, 'ext-sess-1', 1
            )
        """)
        conn.execute("""
            INSERT INTO bridge_sessions (
                stream_id, peer_id, runtime_kind, external_session_id, session_generation,
                adapter_fingerprint, binding, resumable, state, last_seen
            ) VALUES (
                's1', 'p1', 'local', 'ext-sess-1', 1, 'fp-1', 'bind-1', 1, 'ACTIVE', 1000.0
            )
        """)
        conn.commit()

    # Step A: CoreStore open migrates to user_version 4
    core_store = CoreStore(db)
    assert _version(db) == 4

    # Step B: Bridge, Claims, and ObservationStore open with current code
    claims_store = ClaimStore(db, lambda: "g1")
    bridge = Bridge(core_store, claims_store)
    obs_store = ObservationStore(db)

    # Step C: Assert schema self-upgrade occurred cleanly
    with sqlite3.connect(str(db)) as conn:
        conn.row_factory = sqlite3.Row

        # bridge_deliveries columns upgraded (session_handle, session_snapshot_json added)
        deliv_cols = {r[1] for r in conn.execute("PRAGMA table_info(bridge_deliveries)")}
        assert "session_handle" in deliv_cols, "bridge_deliveries missing upgraded session_handle"
        assert "session_snapshot_json" in deliv_cols, "bridge_deliveries missing upgraded session_snapshot_json"

        # Seed data preserved
        row = conn.execute("SELECT * FROM bridge_deliveries WHERE delivery_id = 'del-1'").fetchone()
        assert row is not None
        assert row["delivery_id"] == "del-1"
        assert row["certainty"] == "TERMINAL"

        # bridge_sessions pointer table reconstructed and session history populated
        sess_cols = {r[1] for r in conn.execute("PRAGMA table_info(bridge_sessions)")}
        assert "session_handle" in sess_cols, "bridge_sessions missing upgraded session_handle"
        assert "updated_at" in sess_cols, "bridge_sessions missing upgraded updated_at"

        sess_row = conn.execute("SELECT * FROM bridge_sessions WHERE stream_id = 's1' AND peer_id = 'p1'").fetchone()
        assert sess_row is not None
        assert sess_row["session_handle"] is not None

        hist_row = conn.execute("SELECT * FROM bridge_session_history WHERE session_handle = ?", (sess_row["session_handle"],)).fetchone()
        assert hist_row is not None
        assert hist_row["external_session_id"] == "ext-sess-1"

        # Observation tables created by ObservationStore
        obs_tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert "observations" in obs_tables
        assert "resource_pools" in obs_tables

        # Version remains 4
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 4
