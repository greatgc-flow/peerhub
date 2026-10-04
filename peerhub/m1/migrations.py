"""Ordered, transactional schema migrations for the Core store (TD-14).

Schema version lives in `PRAGMA user_version` (no extra table: the Core table set stays exactly
peers/streams/stream_members/records/offsets). Version 0 = empty or the unversioned Wave-1 schema; the baseline step is
idempotent (`IF NOT EXISTS`), so legacy unversioned stores migrate to 1 without touching data.
Rules: all pending steps and their version stamps commit atomically; a future/unknown version is rejected (no downgrade,
no repair); a crash before COMMIT leaves the previous version untouched.
"""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from typing import Callable

from .schema_version import SUPPORTED_SCHEMA_VERSION, SchemaVersionError, future_schema_message

_BASELINE = """
CREATE TABLE IF NOT EXISTS peers (
    peer_id TEXT PRIMARY KEY,
    display_name TEXT,
    adapter_ref TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS streams (
    stream_id TEXT PRIMARY KEY,
    title TEXT,
    state TEXT NOT NULL,
    revision INTEGER NOT NULL DEFAULT 1,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS stream_members (
    stream_id TEXT NOT NULL,
    peer_id TEXT NOT NULL,
    PRIMARY KEY (stream_id, peer_id),
    FOREIGN KEY (stream_id) REFERENCES streams(stream_id) ON DELETE CASCADE,
    FOREIGN KEY (peer_id) REFERENCES peers(peer_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS records (
    record_id TEXT PRIMARY KEY,
    stream_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    author_peer_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    body_json TEXT,
    targets_json TEXT NOT NULL DEFAULT '[]',
    reply_to TEXT,
    refs_json TEXT NOT NULL DEFAULT '[]',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    idempotency_key TEXT NOT NULL,
    payload_digest TEXT NOT NULL,
    created_at TEXT NOT NULL,
    appended_at TEXT NOT NULL,
    FOREIGN KEY (stream_id) REFERENCES streams(stream_id) ON DELETE CASCADE,
    FOREIGN KEY (author_peer_id) REFERENCES peers(peer_id) ON DELETE RESTRICT,
    UNIQUE (stream_id, position),
    UNIQUE (stream_id, author_peer_id, idempotency_key)
);

CREATE TABLE IF NOT EXISTS offsets (
    peer_id TEXT NOT NULL,
    stream_id TEXT NOT NULL,
    read_through_position INTEGER NOT NULL DEFAULT 0,
    revision INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (peer_id, stream_id),
    FOREIGN KEY (peer_id) REFERENCES peers(peer_id) ON DELETE CASCADE,
    FOREIGN KEY (stream_id) REFERENCES streams(stream_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_records_stream_position ON records(stream_id, position);

CREATE TRIGGER IF NOT EXISTS records_immutable_update BEFORE UPDATE ON records
BEGIN SELECT RAISE(ABORT, 'records are immutable'); END;
CREATE TRIGGER IF NOT EXISTS records_immutable_delete BEFORE DELETE ON records
BEGIN SELECT RAISE(ABORT, 'records are immutable'); END;
"""


def split_statements(script: str) -> list[str]:
    """Split a SQL script into complete statements (trigger bodies contain ';'), so DDL runs inside one transaction."""
    out: list[str] = []
    buf = ""
    for line in script.splitlines(keepends=True):
        buf += line
        if sqlite3.complete_statement(buf):
            if buf.strip():
                out.append(buf.strip())
            buf = ""
    if buf.strip():
        raise ValueError(f"incomplete SQL statement: {buf.strip()[:60]!r}")
    return out


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    apply: Callable[[sqlite3.Connection], None]


def _baseline(conn: sqlite3.Connection) -> None:
    for stmt in split_statements(_BASELINE):
        conn.execute(stmt)


# v2: pragma-independent INSERT OR REPLACE guard. REPLACE resolves a UNIQUE/PK collision by an implicit DELETE that fires
# delete triggers only under recursive_triggers=ON (a per-connection pragma); a BEFORE INSERT guard fires regardless.
# Offsets/streams/peers/stream_members are mutable by design (spec STREAM_RECORD_OFFSET); records are the only append-only Core table.
_GUARD_V2 = """
CREATE TRIGGER IF NOT EXISTS records_no_replace BEFORE INSERT ON records
WHEN EXISTS (SELECT 1 FROM records WHERE record_id = NEW.record_id
    OR (stream_id = NEW.stream_id AND position = NEW.position)
    OR (stream_id = NEW.stream_id AND author_peer_id = NEW.author_peer_id AND idempotency_key = NEW.idempotency_key))
BEGIN SELECT RAISE(ABORT, 'records are immutable (append only)'); END;
"""


def _guard_v2(conn: sqlite3.Connection) -> None:
    for stmt in split_statements(_GUARD_V2):
        conn.execute(stmt)


# v3: INSERT OR REPLACE INTO records(rowid, ...) with an existing implicit rowid replaces the row without any declared-key collision.
_GUARD_V3 = """
CREATE TRIGGER IF NOT EXISTS records_no_rowid_replace BEFORE INSERT ON records
WHEN NEW.rowid IS NOT NULL AND EXISTS (SELECT 1 FROM records WHERE rowid = NEW.rowid)
BEGIN SELECT RAISE(ABORT, 'records are immutable (append only)'); END;
"""


def _guard_v3(conn: sqlite3.Connection) -> None:
    for stmt in split_statements(_GUARD_V3):
        conn.execute(stmt)


MIGRATIONS: list[Migration] = [Migration(1, "baseline_core_tables", _baseline),
                               Migration(2, "records_no_replace_guard", _guard_v2),
                               Migration(3, "records_no_rowid_replace_guard", _guard_v3)]
CURRENT_VERSION: int = MIGRATIONS[-1].version
assert CURRENT_VERSION == SUPPORTED_SCHEMA_VERSION, "bump schema_version.SUPPORTED_SCHEMA_VERSION together with MIGRATIONS"


def rollback_quietly(conn: sqlite3.Connection) -> None:
    """ROLLBACK only if a transaction is still open: SQLite auto-rolls back on some errors (e.g. SQLITE_FULL), and a second
    ROLLBACK raising 'cannot rollback' must never mask the real failure."""
    if conn.in_transaction:
        try:
            conn.execute("ROLLBACK")
        except sqlite3.Error:
            pass  # closing the connection discards the transaction


class MigrationIntegrityError(RuntimeError):
    """Migration would commit referential-integrity violations; rolled back."""


def _version(conn: sqlite3.Connection) -> int:
    return conn.execute("PRAGMA user_version").fetchone()[0]


def run_migrations(db_path: str | os.PathLike[str], migrations: list[Migration] | None = None,
                   fault: Callable[[str], None] | None = None) -> list[int]:
    """Apply pending migrations once; returns the versions applied ([] when already current).

    `fault(point)` is a test seam: points `migration.step` (after each step) and `migration.before_commit`.
    """
    steps = sorted(MIGRATIONS if migrations is None else migrations, key=lambda m: m.version)
    latest = steps[-1].version
    conn = sqlite3.connect(str(db_path), timeout=30.0, isolation_level=None)
    try:
        conn.execute("PRAGMA busy_timeout = 30000;")

        def check(v: int) -> None:
            if v > latest:
                raise SchemaVersionError(future_schema_message(v, latest))

        check(_version(conn))  # reject a future schema BEFORE any persistent mutation (e.g. WAL conversion)
        if conn.execute("PRAGMA journal_mode").fetchone()[0].lower() != "wal":
            conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA foreign_keys = ON;")  # must be set outside the transaction
        if _version(conn) == latest:
            return []
        conn.execute("BEGIN IMMEDIATE")
        try:
            current = _version(conn)  # re-read under the write lock: another process may have migrated meanwhile
            check(current)
            applied: list[int] = []
            for m in steps:
                if m.version > current:
                    m.apply(conn)
                    conn.execute(f"PRAGMA user_version = {int(m.version)}")
                    applied.append(m.version)
                    if fault is not None:
                        fault("migration.step")
            violations = conn.execute("PRAGMA foreign_key_check").fetchall()
            if violations:
                raise MigrationIntegrityError(f"foreign key violations after migration: {[tuple(v) for v in violations[:5]]}")
            if fault is not None:
                fault("migration.before_commit")
        except BaseException:
            rollback_quietly(conn)
            raise
        conn.execute("COMMIT")
        return applied
    finally:
        conn.close()
