"""PeerHub M1 First-Party Extension: Observation store (append-only evidence) + Resource Pool registry.

Rules (OBSERVATION_AND_DIAG.md, PEER_CARDINALITY_RESOURCE_POOL.md, TD-13/TD-23):
- Observations are immutable evidence; refresh = new observation identity. UPDATE/DELETE/replace are rejected by triggers.
- Freshness is derived at read time (kind TTL policy + clock); stored rows are never rewritten.
- Local `captured_at` (else `observed_at`) is the latest-selection clock; ties are broken by the persisted capture ordinal.
- Quota != rate limit; unmeasured values are never turned into 0/healthy/unlimited.
- A probe failure becomes explicit ERROR evidence; the store never touches Core tables, routing or runtimes.
"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable

from .observation_model import (
    EvidenceState,
    FreshnessPolicy,
    InvalidObservationError,
    KindSemanticError,
    Observation,
    ObservationExistsError,
    ObservationView,
    PoolConflictError,
    ResourcePool,
    SourceReading,
    UnknownResourcePoolError,
    check_honesty,
    effective_us,
    epoch_to_iso,
    evaluate,
    row_to_observation,
)

_DDL = """
CREATE TABLE IF NOT EXISTS resource_pools (
    resource_pool_id TEXT PRIMARY KEY,
    provider TEXT NOT NULL,
    kind TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS observations (
    capture_seq INTEGER PRIMARY KEY AUTOINCREMENT,
    observation_id TEXT NOT NULL UNIQUE,
    subject_ref TEXT NOT NULL,
    resource_pool_ref TEXT REFERENCES resource_pools(resource_pool_id),
    kind TEXT NOT NULL,
    source TEXT NOT NULL,
    state TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    captured_at TEXT,
    effective_at_us INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_obs_subject_kind ON observations(subject_ref, kind, effective_at_us, capture_seq);
CREATE INDEX IF NOT EXISTS idx_obs_pool ON observations(resource_pool_ref, capture_seq);
"""
_TRIGGERS = (
    "CREATE TRIGGER observations_no_update BEFORE UPDATE ON observations BEGIN SELECT RAISE(ABORT, 'observations are immutable evidence'); END",
    "CREATE TRIGGER observations_no_delete BEFORE DELETE ON observations BEGIN SELECT RAISE(ABORT, 'observations are immutable evidence'); END",
    # blocks INSERT OR REPLACE even when recursive_triggers is OFF (REPLACE's implicit delete would not fire the delete trigger)
    "CREATE TRIGGER observations_no_replace BEFORE INSERT ON observations "
    "WHEN EXISTS (SELECT 1 FROM observations WHERE observation_id = NEW.observation_id) "
    "BEGIN SELECT RAISE(ABORT, 'observations are immutable evidence (append only)'); END",
    "CREATE TRIGGER resource_pools_no_update BEFORE UPDATE ON resource_pools BEGIN SELECT RAISE(ABORT, 'resource pools are immutable'); END",
    "CREATE TRIGGER resource_pools_no_delete BEFORE DELETE ON resource_pools BEGIN SELECT RAISE(ABORT, 'resource pools are immutable'); END",
)
_OWNED_TABLES = ("observations", "resource_pools")
_SELECT = ("SELECT capture_seq, observation_id, subject_ref, resource_pool_ref, kind, source, state, payload_json, observed_at, captured_at "
           "FROM observations")
_ORDER_LATEST = " ORDER BY effective_at_us DESC, capture_seq DESC"


def _canon(d: dict) -> str:
    return json.dumps(d, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


class ObservationStore:
    def __init__(self, db_path: str | Path, *, clock: Callable[[], float] | None = None, policy: FreshnessPolicy | None = None,
                 id_source: Callable[[], str] | None = None, fault_hook: Callable[[str], None] | None = None) -> None:
        """`clock()` = epoch seconds (ManualClock in tests); `fault_hook(point)` fires `<op>.before_commit` (CrashInjector seam)."""
        self.db_path = str(db_path)
        self.clock = clock or time.time
        self.policy = policy or FreshnessPolicy()
        self._new_id = id_source or (lambda: f"obs-{uuid.uuid4().hex}")
        self.fault_hook = fault_hook
        self._init_schema()

    # ------------------------------------------------------------------ plumbing
    def _connect(self, *, read_only: bool = False) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.isolation_level = None
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.execute("PRAGMA recursive_triggers = ON;")
        conn.execute("PRAGMA busy_timeout = 30000;")
        if read_only:
            conn.execute("PRAGMA query_only = ON;")  # reads can never write (freshness is derived, not persisted)
        return conn

    @contextmanager
    def _tx(self, point: str | None = None):
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
                if point is not None and self.fault_hook is not None:
                    self.fault_hook(f"{point}.before_commit")
            except BaseException:
                conn.execute("ROLLBACK")
                raise
            conn.execute("COMMIT")
        finally:
            conn.close()

    @contextmanager
    def _read(self):
        conn = self._connect(read_only=True)
        try:
            yield conn
        finally:
            conn.close()

    def _init_schema(self) -> None:
        with self._tx() as conn:
            for stmt in _DDL.split(";"):
                if stmt.strip():
                    conn.execute(stmt)
            for (name,) in conn.execute(  # reopen: drop EVERY trigger on owned tables (stale/legacy ones included), then reinstall
                    "SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name IN (?, ?)", _OWNED_TABLES).fetchall():
                conn.execute(f'DROP TRIGGER IF EXISTS "{name}"')
            for t in _TRIGGERS:
                conn.execute(t)

    # ------------------------------------------------------------------ Resource Pool
    @staticmethod
    def _pool_from_row(row: sqlite3.Row) -> ResourcePool:
        return ResourcePool(resource_pool_id=row["resource_pool_id"], provider=row["provider"], kind=row["kind"],
                            metadata=json.loads(row["metadata_json"]))

    def get_resource_pool(self, resource_pool_id: str) -> ResourcePool | None:
        with self._read() as conn:
            row = conn.execute("SELECT * FROM resource_pools WHERE resource_pool_id = ?", (resource_pool_id,)).fetchone()
            return self._pool_from_row(row) if row else None

    def register_resource_pool(self, pool: ResourcePool) -> ResourcePool:
        """Idempotent for identical content; a different definition for an existing id is a conflict (pools are immutable)."""
        with self._tx() as conn:
            row = conn.execute("SELECT * FROM resource_pools WHERE resource_pool_id = ?", (pool.resource_pool_id,)).fetchone()
            if row is not None:
                existing = self._pool_from_row(row)
                if existing != pool:
                    raise PoolConflictError(f"resource pool {pool.resource_pool_id!r} already registered with different content")
                return existing
            conn.execute("INSERT INTO resource_pools (resource_pool_id, provider, kind, metadata_json) VALUES (?, ?, ?, ?)",
                         (pool.resource_pool_id, pool.provider, pool.kind, _canon(pool.metadata)))
            return pool

    @staticmethod
    def _require_pool(conn: sqlite3.Connection, ref: str | None) -> None:
        if ref is not None and conn.execute("SELECT 1 FROM resource_pools WHERE resource_pool_id = ?", (ref,)).fetchone() is None:
            raise UnknownResourcePoolError(f"unknown resource pool {ref!r}")

    # ------------------------------------------------------------------ write path
    def _insert(self, obs: Observation, point: str) -> Observation:
        check_honesty(obs)
        with self._tx(point) as conn:
            self._require_pool(conn, obs.resource_pool_ref)  # binding validated before any mutation
            if conn.execute("SELECT 1 FROM observations WHERE observation_id = ?", (obs.observation_id,)).fetchone():
                raise ObservationExistsError(f"observation {obs.observation_id!r} already exists (evidence is immutable)")
            conn.execute(
                "INSERT INTO observations (observation_id, subject_ref, resource_pool_ref, kind, source, state, payload_json, "
                "observed_at, captured_at, effective_at_us) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (obs.observation_id, obs.subject_ref, obs.resource_pool_ref, obs.kind, obs.source, obs.state.value,
                 _canon(obs.payload), obs.observed_at, obs.captured_at, effective_us(obs)))
        return obs

    def persist(self, obs: Observation) -> Observation:
        """Append a fully formed Observation (wire/import path). The capture ordinal is assigned by the store."""
        return self._insert(obs, "observation.persist")

    def capture(self, subject_ref: str, kind: str, source: Any, *, resource_pool_ref: str | None = None) -> Observation:
        """Probe `source` once and persist the outcome as evidence. Probe failures become ERROR evidence (never an exception,
        never a Core/routing side effect). A source semantic that contradicts `kind` is rejected and nothing is stored."""
        if not isinstance(subject_ref, str) or not subject_ref or not isinstance(kind, str) or not kind:
            raise InvalidObservationError("subject_ref and kind must be non-empty strings")
        with self._read() as conn:  # validate the pool binding BEFORE the probe runs
            self._require_pool(conn, resource_pool_ref)
        src_name = str(getattr(source, "name", None) or type(source).__name__)
        failure: tuple[str, str] | None = None
        reading: Any = None
        try:
            reading = source.probe()
        except Exception as e:  # BaseException (interrupt/process death) is deliberately not swallowed
            failure = ("timeout" if isinstance(e, TimeoutError) else f"exception:{type(e).__name__}", str(e))
        captured_at = epoch_to_iso(self.clock())

        def evidence(state: EvidenceState, payload: dict, observed_at: str | None = None) -> Observation:
            return Observation(observation_id=self._new_id(), subject_ref=subject_ref, resource_pool_ref=resource_pool_ref, kind=kind,
                               source=src_name, observed_at=observed_at or captured_at, captured_at=captured_at, state=state, payload=payload)

        def error(condition: str, detail: str) -> Observation:
            return evidence(EvidenceState.ERROR, {"condition": condition, "detail": detail})

        if failure is not None:
            obs = error(*failure)
        elif not isinstance(reading, SourceReading):
            obs = error("malformed_source_reading", f"probe returned {type(reading).__name__}, expected SourceReading")
        else:
            if reading.semantic is not None and reading.semantic != kind:
                raise KindSemanticError(f"source reports semantic {reading.semantic!r}; refusing to store it as kind {kind!r}")
            try:
                if reading.observed_at is not None and not isinstance(reading.observed_at, str):
                    raise ValueError("observed_at is not a string")
                if not isinstance(reading.payload, dict):
                    raise ValueError("payload is not an object")
                obs = evidence(EvidenceState(reading.state), dict(reading.payload), reading.observed_at)
                check_honesty(obs)
            except (ValueError, InvalidObservationError) as e:  # includes pydantic ValidationError and bad state values
                obs = error("malformed_source_reading", str(e).splitlines()[0] if str(e) else type(e).__name__)
        return self._insert(obs, "observation.capture")

    # ------------------------------------------------------------------ read path (freshness derived, nothing written)
    def _read_us(self, read_at: float | None) -> int:
        return round((self.clock() if read_at is None else read_at) * 1_000_000)

    def latest(self, subject_ref: str, kind: str, read_at: float | None = None, *, resource_pool_ref: str | None = None) -> ObservationView | None:
        read_us = self._read_us(read_at)
        with self._read() as conn:
            self._require_pool(conn, resource_pool_ref)
            sql, args = _SELECT + " WHERE subject_ref = ? AND kind = ?", [subject_ref, kind]
            if resource_pool_ref is not None:
                sql, args = sql + " AND resource_pool_ref = ?", args + [resource_pool_ref]
            row = conn.execute(sql + _ORDER_LATEST + " LIMIT 1", args).fetchone()
        if row is None:
            return None
        obs, seq = row_to_observation(row)
        return evaluate(obs, seq, read_us, self.policy)

    def list_for_resource_pool(self, resource_pool_ref: str, read_at: float | None = None, *, subject_ref: str | None = None,
                               kind: str | None = None) -> list[ObservationView]:
        read_us = self._read_us(read_at)
        with self._read() as conn:
            self._require_pool(conn, resource_pool_ref)
            sql, args = _SELECT + " WHERE resource_pool_ref = ?", [resource_pool_ref]
            if subject_ref is not None:
                sql, args = sql + " AND subject_ref = ?", args + [subject_ref]
            if kind is not None:
                sql, args = sql + " AND kind = ?", args + [kind]
            rows = conn.execute(sql + " ORDER BY capture_seq ASC", args).fetchall()
        return [evaluate(*row_to_observation(r), read_us, self.policy) for r in rows]
