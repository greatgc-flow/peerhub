"""Fenced single-owner delivery claims (minimal kernel; TD-04 fencing, TD-19 expiry, TD-25 guard).

Extension-owned table `bridge_claims`; Core never imports this module. A token carries workspace generation + claim
generation + owner id. Takeover of an expired claim (`now >= expires_at`) increments the claim generation; a claim minted
under another workspace generation is stale. `guard(token)` plugs into Core writes (append response / Offset ack) so the
fence check and the write share one transaction. Full Bridge semantics (sessions, certainty) arrive in Wave 3.
"""
from __future__ import annotations

from peerhub.extensions.schema_guard import refuse_future_schema

import json
import os
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Callable

from peerhub.core.migrations import rollback_quietly
from peerhub.core.store import UnknownReferenceError


class ClaimHeldError(RuntimeError):
    """Another owner holds an unexpired claim."""


class LaunchBusyError(RuntimeError):
    """The store already has activity and the resource gate is waiting."""


class StaleClaimError(RuntimeError):
    """Token is not the current claim (superseded, expired, wrong owner/generation, or another workspace generation)."""


class ClaimFinalizedError(RuntimeError):
    """This claim generation already recorded its terminal finalization."""


class ClaimScopeError(RuntimeError):
    """A valid claim was presented for a write targeting a different (stream, peer) scope."""


@dataclass(frozen=True)
class ClaimToken:
    workspace_generation: str
    generation: int
    owner_id: str
    peer_id: str
    stream_id: str


_DDL = """
CREATE TABLE IF NOT EXISTS bridge_claims (
    stream_id TEXT NOT NULL,
    peer_id TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    generation INTEGER NOT NULL,
    expires_at REAL NOT NULL,
    lease_sec REAL NOT NULL,
    workspace_generation TEXT NOT NULL,
    PRIMARY KEY (stream_id, peer_id)
)"""
_DDL_FINAL = """
CREATE TABLE IF NOT EXISTS bridge_finalizations (
    stream_id TEXT NOT NULL,
    peer_id TEXT NOT NULL,
    claim_generation INTEGER NOT NULL,
    result_json TEXT NOT NULL,
    PRIMARY KEY (stream_id, peer_id, claim_generation)
)"""
_DDL_CALLERS = """
CREATE TABLE IF NOT EXISTS bridge_claim_callers (
    stream_id TEXT NOT NULL,
    peer_id TEXT NOT NULL,
    claim_generation INTEGER NOT NULL,
    detail TEXT NOT NULL,
    PRIMARY KEY (stream_id, peer_id)
)"""


_FINALIZATION_TRIGGERS: dict[str, str] = {
    'bridge_finalizations_no_update': "CREATE TRIGGER bridge_finalizations_no_update BEFORE UPDATE ON bridge_finalizations BEGIN SELECT RAISE(ABORT, 'bridge_finalizations is append-only'); END",
    'bridge_finalizations_no_replace': "CREATE TRIGGER bridge_finalizations_no_replace BEFORE INSERT ON bridge_finalizations WHEN EXISTS (SELECT 1 FROM bridge_finalizations WHERE stream_id = NEW.stream_id AND peer_id = NEW.peer_id AND claim_generation = NEW.claim_generation) BEGIN SELECT RAISE(ABORT, 'bridge_finalizations is append-only'); END",
    'bridge_finalizations_no_rowid_replace': "CREATE TRIGGER bridge_finalizations_no_rowid_replace BEFORE INSERT ON bridge_finalizations WHEN NEW.rowid IS NOT NULL AND EXISTS (SELECT 1 FROM bridge_finalizations WHERE rowid = NEW.rowid) BEGIN SELECT RAISE(ABORT, 'bridge_finalizations is append-only'); END",
    'bridge_finalizations_no_delete': "CREATE TRIGGER bridge_finalizations_no_delete BEFORE DELETE ON bridge_finalizations BEGIN SELECT RAISE(ABORT, 'bridge_finalizations is append-only'); END",
}


class ClaimStore:
    def __init__(self, db_path: str | os.PathLike[str], generation: Callable[[], str], clock: Callable[[], float] = time.time) -> None:
        self.db_path = str(db_path)
        self._generation = generation
        self._clock = clock
        refuse_future_schema(self.db_path)  # MIG-003: refuse a future schema before any DDL/DML
        with self._tx() as conn:
            conn.execute(_DDL)
            conn.execute(_DDL_FINAL)
            conn.execute(_DDL_CALLERS)
            # Inspect under BEGIN IMMEDIATE on EVERY open; never trust a cached
            # schema version or just a trigger name. Preserve SQL body literals.
            installed = dict(conn.execute(
                "SELECT name,sql FROM sqlite_master WHERE type='trigger' "
                "AND tbl_name IN ('bridge_claims','bridge_finalizations')"))
            if installed != _FINALIZATION_TRIGGERS:
                for name in installed:
                    quoted = '"' + name.replace('"', '""') + '"'
                    conn.execute(f"DROP TRIGGER {quoted}")
                for statement in _FINALIZATION_TRIGGERS.values():
                    conn.execute(statement)

    @contextmanager
    def _tx(self):
        conn = sqlite3.connect(self.db_path, timeout=30.0, isolation_level=None)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA busy_timeout = 30000;")
            conn.execute("PRAGMA recursive_triggers = ON;")  # INSERT OR REPLACE must fire the immutability triggers
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
            except BaseException:
                rollback_quietly(conn)
                raise
            conn.execute("COMMIT")
        finally:
            conn.close()

    def workspace_generation(self) -> str:
        return self._generation()

    @staticmethod
    def read_claim(conn: sqlite3.Connection, peer_id: str, stream_id: str) -> sqlite3.Row | None:
        return conn.execute("SELECT * FROM bridge_claims WHERE stream_id=? AND peer_id=?", (stream_id, peer_id)).fetchone()

    @staticmethod
    def check_available(row: sqlite3.Row, peer_id: str, stream_id: str, owner_id: str | None,
                        workspace_generation: str | None, now: float) -> bool:
        live = row["workspace_generation"] == workspace_generation and now < row["expires_at"]
        if live and row["owner_id"] != owner_id:
            raise ClaimHeldError(f"claim for ({peer_id}, {stream_id}) held by {row['owner_id']!r}")
        return live

    def acquire(self, peer_id: str, stream_id: str, owner_id: str, lease_sec: float, *,
                caller: dict[str, Any] | None = None,
                busy: Callable[[sqlite3.Connection, str, float], bool] | None = None) -> ClaimToken:
        gen = self._generation()
        with self._tx() as conn:
            for table, col, val in (("peers", "peer_id", peer_id), ("streams", "stream_id", stream_id)):
                if conn.execute(f"SELECT 1 FROM {table} WHERE {col} = ?", (val,)).fetchone() is None:
                    raise UnknownReferenceError(f"unknown {col} {val!r}")
            now = self._clock()
            row = self.read_claim(conn, peer_id, stream_id)
            if busy is not None and busy(conn, gen, now):
                raise LaunchBusyError()
            live = row is not None and self.check_available(row, peer_id, stream_id, owner_id, gen, now)
            if row is None:
                claim_gen = 1
                conn.execute("INSERT INTO bridge_claims VALUES (?,?,?,?,?,?,?)",
                             (stream_id, peer_id, owner_id, claim_gen, now + lease_sec, lease_sec, gen))
            else:
                claim_gen = row["generation"] if live else row["generation"] + 1  # renewal keeps G; takeover fences old tokens
                conn.execute(
                    "UPDATE bridge_claims SET owner_id=?, generation=?, expires_at=?, lease_sec=?, workspace_generation=? "
                    "WHERE stream_id=? AND peer_id=?",
                    (owner_id, claim_gen, now + lease_sec, lease_sec, gen, stream_id, peer_id))
            if caller is not None:
                conn.execute("INSERT INTO bridge_claim_callers VALUES (?,?,?,?) ON CONFLICT(stream_id,peer_id) "
                             "DO UPDATE SET claim_generation=excluded.claim_generation,detail=excluded.detail",
                             (stream_id, peer_id, claim_gen, json.dumps(caller, sort_keys=True, default=str)))
            elif live:
                conn.execute("DELETE FROM bridge_claim_callers WHERE stream_id=? AND peer_id=?", (stream_id, peer_id))
        return ClaimToken(gen, claim_gen, owner_id, peer_id, stream_id)

    def _check(self, conn: sqlite3.Connection, token: ClaimToken) -> sqlite3.Row:
        row = conn.execute("SELECT * FROM bridge_claims WHERE stream_id=? AND peer_id=?",
                           (token.stream_id, token.peer_id)).fetchone()
        if row is None:
            raise StaleClaimError("no such claim")
        if token.workspace_generation != self._generation() or row["workspace_generation"] != token.workspace_generation:
            raise StaleClaimError("workspace generation changed")
        if row["owner_id"] != token.owner_id or row["generation"] != token.generation:
            raise StaleClaimError(f"claim superseded (current owner {row['owner_id']!r} generation {row['generation']})")
        if self._clock() >= row["expires_at"]:
            raise StaleClaimError("claim expired")
        return row

    def heartbeat(self, token: ClaimToken) -> ClaimToken:
        with self._tx() as conn:
            row = self._check(conn, token)
            conn.execute("UPDATE bridge_claims SET expires_at=? WHERE stream_id=? AND peer_id=?",
                         (self._clock() + row["lease_sec"], token.stream_id, token.peer_id))
        return token

    def renew(self, token: ClaimToken) -> ClaimToken:
        """Extend the current claim before expiry; generation is unchanged (CLM-001). Expired/superseded -> StaleClaimError."""
        return self.heartbeat(token)

    def release(self, token: ClaimToken) -> None:
        """Expire only the current owner's claim; preserve generation/fencing history."""
        with self._tx() as conn:
            self._check(conn, token)
            conn.execute("UPDATE bridge_claims SET expires_at=? WHERE stream_id=? AND peer_id=?",
                         (self._clock(), token.stream_id, token.peer_id))

    def transaction(self):  # public entry for the Bridge (same serialized write transaction)
        return self._tx()

    def now(self) -> float:
        return self._clock()

    def check(self, conn: sqlite3.Connection, token: ClaimToken) -> sqlite3.Row:
        return self._check(conn, token)

    @contextmanager
    def fenced(self, token: ClaimToken):
        """Write transaction that first verifies the current token (TD-25); raising inside rolls everything back."""
        with self._tx() as conn:
            self._check(conn, token)
            yield conn

    def assert_current(self, token: ClaimToken) -> None:
        with self._tx() as conn:
            self._check(conn, token)

    def finalize_terminal(self, token: ClaimToken, result: dict[str, Any]) -> None:
        """Authoritative terminal finalization write; fenced by the current token in the same transaction (TD-25)."""
        with self._tx() as conn:
            self._check(conn, token)
            try:
                conn.execute("INSERT INTO bridge_finalizations VALUES (?,?,?,?)",
                             (token.stream_id, token.peer_id, token.generation,
                              json.dumps(result, sort_keys=True, separators=(",", ":"), allow_nan=False)))
            except sqlite3.IntegrityError as e:
                raise ClaimFinalizedError(f"claim generation {token.generation} already finalized") from e

    def guard(self, token: ClaimToken) -> Callable[[sqlite3.Connection], None]:
        """Fence check to run inside a Core write transaction (same connection, same serialization point)."""

        def check(conn: sqlite3.Connection, *, stream_id: str | None = None, peer_id: str | None = None) -> None:
            if (stream_id is not None and stream_id != token.stream_id) or (peer_id is not None and peer_id != token.peer_id):
                raise ClaimScopeError(f"claim for ({token.peer_id}, {token.stream_id}) cannot authorize ({peer_id}, {stream_id})")
            self._check(conn, token)

        return check
