"""Retained Bridge sessions, MRU pointers and fenced transaction operations."""
from __future__ import annotations

import secrets
import sqlite3
from typing import Any, Callable, Mapping

from peerhub.extensions import session_receipts

MAX_RETAINED = 20
MAX_AGE_S = 30 * 86400
POINTER_DDL = """CREATE TABLE IF NOT EXISTS bridge_sessions (
    stream_id TEXT NOT NULL, peer_id TEXT NOT NULL, session_handle TEXT NOT NULL REFERENCES bridge_session_history(session_handle),
    session_generation INTEGER NOT NULL, updated_at REAL NOT NULL, PRIMARY KEY(stream_id,peer_id))"""
DDL = (
    """CREATE TABLE IF NOT EXISTS bridge_session_history (
        session_handle TEXT PRIMARY KEY, stream_id TEXT NOT NULL, peer_id TEXT NOT NULL, runtime_kind TEXT NOT NULL,
        external_session_id TEXT NOT NULL, session_generation INTEGER NOT NULL, adapter_fingerprint TEXT NOT NULL,
        binding TEXT NOT NULL, resumable INTEGER NOT NULL CHECK(resumable IN (0,1)),
        state TEXT NOT NULL CHECK(state IN ('ACTIVE','FRESH','LOST','TOMBSTONED')), last_seen REAL NOT NULL,
        workspace_generation TEXT NOT NULL DEFAULT '', vendor_session_id TEXT, context_watermark INTEGER,
        bootstrap_truncated INTEGER, usage_json TEXT, context_watermark_version INTEGER NOT NULL DEFAULT 1,
        created_at REAL NOT NULL, tombstoned_at REAL, UNIQUE(stream_id,peer_id,session_generation))""",
    """CREATE TABLE IF NOT EXISTS bridge_session_counters (
        stream_id TEXT NOT NULL, peer_id TEXT NOT NULL, next_generation INTEGER NOT NULL CHECK(next_generation>0), PRIMARY KEY(stream_id,peer_id))""",
    "CREATE INDEX IF NOT EXISTS idx_bsh_monitor_scope ON bridge_session_history(stream_id,peer_id,last_seen DESC,session_generation DESC,session_handle DESC)",
    "CREATE INDEX IF NOT EXISTS idx_bsh_monitor_live ON bridge_session_history(stream_id,peer_id,last_seen DESC,session_generation DESC,session_handle DESC) WHERE state!='TOMBSTONED'",
    "CREATE INDEX IF NOT EXISTS idx_bsh_monitor_active ON bridge_session_history(stream_id,peer_id,last_seen) WHERE state='ACTIVE' AND resumable=1 AND vendor_session_id IS NOT NULL AND context_watermark IS NOT NULL",
    """CREATE TRIGGER IF NOT EXISTS bridge_history_identity_immutable BEFORE UPDATE OF
        session_handle,stream_id,peer_id,session_generation,runtime_kind,external_session_id,adapter_fingerprint,binding,workspace_generation,created_at
        ON bridge_session_history BEGIN SELECT RAISE(ABORT,'session identity is immutable'); END""",
    "CREATE TRIGGER IF NOT EXISTS bridge_history_no_delete BEFORE DELETE ON bridge_session_history BEGIN SELECT RAISE(ABORT,'session audit rows are retained'); END",
    """CREATE TRIGGER IF NOT EXISTS bridge_history_no_replace BEFORE INSERT ON bridge_session_history
        WHEN EXISTS(SELECT 1 FROM bridge_session_history WHERE rowid=NEW.rowid OR session_handle=NEW.session_handle OR
            (stream_id=NEW.stream_id AND peer_id=NEW.peer_id AND session_generation=NEW.session_generation))
        BEGIN SELECT RAISE(ABORT,'session history is never replaced'); END""",
    """CREATE TRIGGER IF NOT EXISTS bridge_history_tombstone_final BEFORE UPDATE ON bridge_session_history
        WHEN OLD.state='TOMBSTONED' BEGIN SELECT RAISE(ABORT,'session tombstones are final'); END""",
)


def generate_session_handle() -> str:
    return "sess_" + secrets.token_hex(8)


def current(conn: sqlite3.Connection, stream: str, peer: str) -> sqlite3.Row | None:
    return conn.execute("SELECT h.* FROM bridge_sessions p JOIN bridge_session_history h ON h.session_handle=p.session_handle "
                        "AND h.stream_id=p.stream_id AND h.peer_id=p.peer_id AND h.session_generation=p.session_generation "
                        "WHERE p.stream_id=? AND p.peer_id=?", (stream, peer)).fetchone()


def generation(conn: sqlite3.Connection, stream: str, peer: str, gen: int | None) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM bridge_session_history WHERE stream_id=? AND peer_id=? AND session_generation=?", (stream, peer, gen)).fetchone()


def point(conn: sqlite3.Connection, row: Mapping[str, Any] | sqlite3.Row, now: float) -> None:
    conn.execute("INSERT INTO bridge_sessions VALUES (?,?,?,?,?) ON CONFLICT(stream_id,peer_id) DO UPDATE SET "
                 "session_handle=excluded.session_handle,session_generation=excluded.session_generation,updated_at=excluded.updated_at",
                 (row["stream_id"], row["peer_id"], row["session_handle"], row["session_generation"], now))


def eligible(row: Mapping[str, Any] | sqlite3.Row, now: float) -> bool:
    if row["state"] not in ("ACTIVE", "FRESH") or not row["resumable"] or not 0 <= now-row["last_seen"] <= MAX_AGE_S:
        return False
    if row["runtime_kind"] in ("cc", "cx", "ag") or row["vendor_session_id"] is not None:
        return row["vendor_session_id"] is not None and row["context_watermark"] is not None and row["context_watermark_version"] == 1
    return True  # custom local RuntimeTarget continuity needs no native watermark


def ordered(conn: sqlite3.Connection, stream: str, peer: str) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM bridge_session_history WHERE stream_id=? AND peer_id=? AND state!='TOMBSTONED' "
                        "ORDER BY last_seen DESC,session_generation DESC,session_handle DESC", (stream, peer)).fetchall()


def repair(conn: sqlite3.Connection, stream: str, peer: str, now: float) -> None:
    pointer = current(conn, stream, peer)
    if pointer is not None and eligible(pointer, now):
        return
    candidate = next((r for r in ordered(conn, stream, peer) if eligible(r, now)), None)
    if candidate is None:
        conn.execute("DELETE FROM bridge_sessions WHERE stream_id=? AND peer_id=?", (stream, peer))
    else:
        point(conn, candidate, now)


def prune(conn: sqlite3.Connection, stream: str, peer: str, now: float) -> None:
    retained = ordered(conn, stream, peer)
    alive = [r for r in retained if now-r["last_seen"] <= MAX_AGE_S]
    removed = {r["session_handle"] for r in alive[MAX_RETAINED:]}
    for row in retained:
        age = now-row["last_seen"] > MAX_AGE_S
        if age or row["session_handle"] in removed:
            conn.execute("UPDATE bridge_session_history SET state='TOMBSTONED',tombstoned_at=? WHERE session_handle=? AND state!='TOMBSTONED'",
                         (now, row["session_handle"]))
            conn.execute("INSERT INTO bridge_session_events(stream_id,peer_id,event,from_state,to_state,generation,detail) VALUES (?,?,'tombstoned',?,'TOMBSTONED',?,?)",
                         (stream, peer, row["state"], row["session_generation"], session_receipts.dumps({
                             "session_handle": row["session_handle"], "reason": "retention_max_age" if age else "retention_quota"})))
    repair(conn, stream, peer, now)


def initialize(conn: sqlite3.Connection, upgrade: Callable[[sqlite3.Connection], None], fire: Callable[[str], None]) -> None:
    columns = {r[1] for r in conn.execute("PRAGMA table_info(bridge_sessions)")}
    legacy = bool(columns) and "session_handle" not in columns
    if legacy:
        for name, kind in (("workspace_generation", "TEXT NOT NULL DEFAULT ''"), ("vendor_session_id", "TEXT"),
                           ("context_watermark", "INTEGER"), ("bootstrap_truncated", "INTEGER"), ("usage_json", "TEXT"),
                           ("context_watermark_version", "INTEGER NOT NULL DEFAULT 0")):
            if name not in columns:
                conn.execute(f"ALTER TABLE bridge_sessions ADD COLUMN {name} {kind}")
        upgrade(conn)  # PR1 consumed-context proof is permanent
    for stmt in DDL:
        conn.execute(stmt)
    if legacy:
        rows = conn.execute("SELECT * FROM bridge_sessions").fetchall()
        conn.execute("DROP TABLE bridge_sessions")
    else:
        rows = []
    conn.execute(POINTER_DDL)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_bs_monitor_peer ON bridge_sessions(peer_id,updated_at DESC,stream_id)")
    for stmt in session_receipts.DDL:
        conn.execute(stmt)
    for row in rows:
        values = dict(row)
        values.update(session_handle=generate_session_handle(), created_at=row["last_seen"], tombstoned_at=None)
        names = list(values)
        conn.execute(f"INSERT INTO bridge_session_history ({','.join(names)}) VALUES ({','.join('?' for _ in names)})", [values[n] for n in names])
        session_receipts.initialize_stats(conn, values, session_receipts.binding_attributes(None), legacy=True)
        # Seed from all durable generation evidence, including overwritten legacy slots.
        maximum = conn.execute("SELECT MAX(g) FROM (SELECT session_generation g FROM bridge_deliveries WHERE stream_id=? AND peer_id=? "
                               "UNION ALL SELECT generation FROM bridge_session_events WHERE stream_id=? AND peer_id=? "
                               "UNION ALL SELECT session_generation FROM bridge_session_history WHERE stream_id=? AND peer_id=?)",
                               (row["stream_id"], row["peer_id"])*3).fetchone()[0]
        conn.execute("INSERT INTO bridge_session_counters VALUES (?,?,?)", (row["stream_id"], row["peer_id"], maximum+1))
        point(conn, values, row["last_seen"])
    # Scopes with only overwritten evidence still need monotonic counters and unknown coverage.
    for scope in conn.execute("SELECT stream_id,peer_id,MAX(g) FROM (SELECT stream_id,peer_id,session_generation g FROM bridge_deliveries "
                              "UNION ALL SELECT stream_id,peer_id,generation g FROM bridge_session_events) GROUP BY stream_id,peer_id").fetchall():
        conn.execute("INSERT INTO bridge_session_counters VALUES (?,?,?) ON CONFLICT(stream_id,peer_id) DO UPDATE SET next_generation=MAX(next_generation,excluded.next_generation)",
                     (scope[0], scope[1], (scope[2] or 0)+1))
        if not conn.execute("SELECT 1 FROM bridge_session_monitor_scope WHERE stream_id=? AND peer_id=?", (scope[0], scope[1])).fetchone():
            high = conn.execute("SELECT COALESCE(MAX(seq),0) FROM bridge_evidence").fetchone()[0]
            conn.execute("INSERT INTO bridge_session_monitor_scope VALUES (?,?,?,?,?,?)", (scope[0], scope[1], *session_receipts.historical_counts(conn, scope[0], scope[1]), high))
    cols = {r[1] for r in conn.execute("PRAGMA table_info(bridge_deliveries)")}
    for name in ("session_handle", "session_snapshot_json"):
        if name not in cols:
            conn.execute(f"ALTER TABLE bridge_deliveries ADD COLUMN {name} TEXT")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_bd_session ON bridge_deliveries(session_handle,seq)")
    if conn.execute("SELECT 1 FROM sqlite_master WHERE name='observations'").fetchone():
        conn.execute("CREATE INDEX IF NOT EXISTS idx_obs_session_quota ON observations(subject_ref,kind,resource_pool_ref,effective_at_us DESC,capture_seq DESC)")
    fire("bridge.registry.before_commit")


def allocate(conn: sqlite3.Connection, values: dict[str, Any], binding: dict[str, Any], now: float) -> dict[str, Any]:
    stream, peer = values["stream_id"], values["peer_id"]
    conn.execute("INSERT OR IGNORE INTO bridge_session_counters VALUES (?,?,1)", (stream, peer))
    gen = conn.execute("SELECT next_generation FROM bridge_session_counters WHERE stream_id=? AND peer_id=?", (stream, peer)).fetchone()[0]
    conn.execute("UPDATE bridge_session_counters SET next_generation=next_generation+1 WHERE stream_id=? AND peer_id=?", (stream, peer))
    while True:
        handle = generate_session_handle()
        if not conn.execute("SELECT 1 FROM bridge_session_history WHERE session_handle=?", (handle,)).fetchone():
            break
    values = dict(values, session_handle=handle, session_generation=gen, last_seen=now, created_at=now,
                  context_watermark_version=1)
    names = list(values)
    conn.execute(f"INSERT INTO bridge_session_history ({','.join(names)}) VALUES ({','.join('?' for _ in names)})", [values[n] for n in names])
    session_receipts.initialize_stats(conn, values, binding)
    point(conn, values, now)
    prune(conn, stream, peer, now)
    # A newly allocated CLI has no vendor id yet, but remains the active allocation pointer.
    point(conn, values, now)
    return values
