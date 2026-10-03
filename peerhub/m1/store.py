"""PeerHub M1 Core Persistence Layer.

Authoritative SQLite store supporting:
- WAL mode & foreign keys, serialized write transactions (BEGIN IMMEDIATE)
- Per-stream monotonically increasing append position assigned inside the transaction (TD-01)
- Idempotent append scoped (stream_id, author_peer_id, key) (TD-20); conflict on changed canonical payload (TD-02)
- Revision-CAS Stream mutation (TD-09) and Offset CAS (TD-03/TD-10)
- Core invariant: Does NOT import any extension.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable

from .migrations import rollback_quietly, run_migrations
from .models import (
    AppendRequest,
    Offset,
    Peer,
    Record,
    Stream,
    StreamState,
    assert_json_value,
    compute_record_digest,
    utc_now_iso,
)


class IdempotencyConflictError(Exception):
    """Same (stream, author, idempotency key) reused with a different canonical payload."""


class CasMismatchError(Exception):
    """Revision CAS lost (stale expected revision); nothing was mutated."""


class UnknownReferenceError(LookupError):
    """Referenced Peer/Stream does not exist; nothing was mutated."""


class AlreadyExistsError(ValueError):
    """Stream already exists (create is not an upsert)."""


class StreamClosedError(ValueError):
    """Append to a CLOSED Stream (TD-09)."""


class IllegalTransitionError(ValueError):
    """Stream transition not allowed in M1 (no reopen, TD-09)."""


class OffsetRegressionError(ValueError):
    """Offset may not move backward (TD-03)."""


class OffsetBeyondHeadError(ValueError):
    """Offset may not exceed the committed Stream head (TD-10)."""


class StorageReadOnlyError(sqlite3.OperationalError):
    """The database/filesystem is read-only; the operation failed closed with no change (FLT-012)."""


class StorageFullError(sqlite3.OperationalError):
    """SQLITE_FULL: the write was rolled back atomically (FLT-011)."""


class StorageCorruptError(sqlite3.DatabaseError):
    """The database file is corrupt/not a database; nothing was recreated, repaired or written (FLT-013)."""


_STORAGE_ERRORS = (StorageReadOnlyError, StorageFullError, StorageCorruptError)
_CODE_READONLY, _CODE_FULL, _CODE_CORRUPT, _CODE_NOTADB = 8, 13, 11, 26  # SQLite primary result codes


def classify_storage_error(e: sqlite3.DatabaseError) -> sqlite3.DatabaseError | None:
    """Map a raw SQLite failure to a precise storage error (None = not a storage-health failure: callers re-raise it unchanged).
    Busy/locked stays the raw OperationalError (explicit, retryable, MP-001)."""
    if isinstance(e, _STORAGE_ERRORS):
        return None
    code = (getattr(e, "sqlite_errorcode", None) or 0) & 0xFF
    cls = {_CODE_READONLY: StorageReadOnlyError, _CODE_FULL: StorageFullError, _CODE_CORRUPT: StorageCorruptError,
           _CODE_NOTADB: StorageCorruptError}.get(code)
    return None if cls is None else cls(str(e))


@contextmanager
def storage_errors():
    """Re-raise SQLite storage faults as precise Storage*Error (original kept as __cause__); everything else is untouched."""
    try:
        yield
    except sqlite3.DatabaseError as e:
        mapped = classify_storage_error(e)
        if mapped is None:
            raise
        raise mapped from e


DEFAULT_QUICK_CHECK_MAX_BYTES = 256 * 1024 * 1024  # open-time full quick_check is O(size): larger files get the light probe (Q-W6-2)

STREAM_MUTATION_KEYS = frozenset({"state", "members", "title", "metadata"})



class CoreStore:
    def __init__(self, db_path: str | Path, fault_hook: Callable[[str], None] | None = None, *,
                 busy_timeout_ms: int = 30000, conn_init: Callable[[sqlite3.Connection], None] | None = None,
                 quick_check_max_bytes: int = DEFAULT_QUICK_CHECK_MAX_BYTES) -> None:
        """Test seams: `fault_hook(point)` (CrashInjector points: append.begin, append.before_commit, append.after_commit,
        offset.before_head_check); `conn_init(conn)` runs on every store connection (e.g. a real `PRAGMA max_page_count`);
        `busy_timeout_ms` bounds how long a writer waits for the database write lock before failing explicitly."""
        self.db_path = str(db_path)
        self.fault_hook = fault_hook
        self.busy_timeout_ms = int(busy_timeout_ms)
        self.conn_init = conn_init
        self.quick_check_max_bytes = int(quick_check_max_bytes)
        self.preflight_mode = "none"  # none (no file yet) | full (quick_check) | light (header+schema+sampled rows); see _preflight
        with storage_errors():
            self._preflight()  # fail closed on a corrupt/foreign file BEFORE any migration/WAL change (FLT-013)
            run_migrations(self.db_path)  # ordered, transactional, no-op at current version; rejects future versions (TD-14)

    def _preflight(self) -> None:
        """Read-only integrity probe of an EXISTING non-empty file: never creates, repairs or migrates anything.
        size <= quick_check_max_bytes: full `PRAGMA quick_check`. Larger: light probe (header + schema read + first/last row of every
        table); deeper damage then surfaces lazily as StorageCorruptError on access (every read/write path maps it). Diag always runs the full check."""
        path = Path(self.db_path)
        if not path.exists() or path.stat().st_size == 0:
            return
        conn = sqlite3.connect(self.db_path, timeout=self.busy_timeout_ms / 1000.0)
        try:
            if path.stat().st_size <= self.quick_check_max_bytes:
                self.preflight_mode = "full"
                rows = conn.execute("PRAGMA quick_check").fetchall()
                if rows != [("ok",)]:
                    raise StorageCorruptError(f"integrity check failed: {[r[0] for r in rows[:3]]}")
            else:
                self.preflight_mode = "light"
                for (name,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall():
                    for order in ("ASC", "DESC"):
                        conn.execute(f'SELECT * FROM "{name}" ORDER BY rowid {order} LIMIT 1').fetchall()
        finally:
            conn.close()

    def _fire(self, point: str) -> None:
        if self.fault_hook is not None:
            self.fault_hook(point)

    def connect(self) -> sqlite3.Connection:
        """Raw connection with the Core pragmas (WAL, foreign_keys, busy_timeout). Caller closes it."""
        return self._get_connection()

    @contextmanager
    def read_uow(self):
        """Read-only unit of work: `mode=ro` + `query_only`; any write is rejected by SQLite itself."""
        conn = sqlite3.connect(Path(self.db_path).resolve().as_uri() + "?mode=ro", uri=True, timeout=30.0)
        try:
            with storage_errors():
                conn.row_factory = sqlite3.Row
                conn.isolation_level = None
                conn.execute("PRAGMA query_only = ON;")
                conn.execute("PRAGMA foreign_keys = ON;")
                conn.execute("BEGIN")  # one read transaction = one committed snapshot for the whole UoW
                conn.execute("SELECT COUNT(*) FROM sqlite_master").fetchone()  # pins the snapshot now (WAL read mark)
                yield conn
        finally:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            conn.close()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=self.busy_timeout_ms / 1000.0)
        try:
            with storage_errors():
                conn.row_factory = sqlite3.Row
                conn.execute("PRAGMA journal_mode = WAL;")
                conn.execute("PRAGMA foreign_keys = ON;")
                conn.execute("PRAGMA recursive_triggers = ON;")  # REPLACE deletes must fire the immutability trigger
                conn.execute(f"PRAGMA busy_timeout = {self.busy_timeout_ms};")
                if self.conn_init is not None:
                    self.conn_init(conn)
        except BaseException:
            conn.close()
            raise
        return conn

    @contextmanager
    def _tx(self, point: str | None = None):
        """Serialized write transaction (BEGIN IMMEDIATE); rolls back on any error, always closes.
        With `point`, fires fault hook `<point>.before_commit` after the body and before COMMIT."""
        conn = self._get_connection()
        conn.isolation_level = None
        try:
            with storage_errors():
                conn.execute("BEGIN IMMEDIATE")
                try:
                    yield conn
                    if point is not None:
                        self._fire(f"{point}.before_commit")
                except BaseException:
                    rollback_quietly(conn)  # SQLite may already have rolled back (SQLITE_FULL): never mask the original error
                    raise
                conn.execute("COMMIT")
        finally:
            conn.close()

    @contextmanager
    def _read(self):
        conn = self._get_connection()
        try:
            with storage_errors():
                yield conn
        finally:
            conn.close()

    # --- Peer
    def register_peer(self, peer: Peer) -> Peer:
        """Upsert by peer_id; identity and original created_at are never rewritten (CORE-001)."""
        with self._tx() as conn:
            conn.execute(
                """
                INSERT INTO peers (peer_id, display_name, adapter_ref, metadata_json, created_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(peer_id) DO UPDATE SET
                    display_name=excluded.display_name,
                    adapter_ref=excluded.adapter_ref,
                    metadata_json=excluded.metadata_json
                """,
                (peer.peer_id, peer.display_name, peer.adapter_ref,
                 json.dumps(peer.metadata, ensure_ascii=False, allow_nan=False), peer.created_at),
            )
            row = conn.execute("SELECT * FROM peers WHERE peer_id = ?", (peer.peer_id,)).fetchone()
        return self._row_to_peer(row)

    @staticmethod
    def _row_to_peer(row: sqlite3.Row) -> Peer:
        return Peer(peer_id=row["peer_id"], display_name=row["display_name"], adapter_ref=row["adapter_ref"],
                    metadata=json.loads(row["metadata_json"]), created_at=row["created_at"])

    def get_peer(self, peer_id: str) -> Peer | None:
        with self._read() as conn:
            row = conn.execute("SELECT * FROM peers WHERE peer_id = ?", (peer_id,)).fetchone()
            return self._row_to_peer(row) if row else None

    @staticmethod
    def _require_peers(conn: sqlite3.Connection, peer_ids: list[str]) -> None:
        for pid in peer_ids:
            if conn.execute("SELECT 1 FROM peers WHERE peer_id = ?", (pid,)).fetchone() is None:
                raise UnknownReferenceError(f"unknown peer {pid!r}")

    @staticmethod
    def _require_stream(conn: sqlite3.Connection, stream_id: str) -> sqlite3.Row:
        row = conn.execute("SELECT * FROM streams WHERE stream_id = ?", (stream_id,)).fetchone()
        if row is None:
            raise UnknownReferenceError(f"unknown stream {stream_id!r}")
        return row

    # --- Stream
    @staticmethod
    def _set_members(conn: sqlite3.Connection, stream_id: str, members: list[str]) -> None:
        conn.execute("DELETE FROM stream_members WHERE stream_id = ?", (stream_id,))
        for m in members:  # rowid preserves declared order; member order is never delivery order
            conn.execute("INSERT INTO stream_members (stream_id, peer_id) VALUES (?, ?)", (stream_id, m))

    def create_stream(self, stream: Stream) -> Stream:
        with self._tx() as conn:
            if conn.execute("SELECT 1 FROM streams WHERE stream_id = ?", (stream.stream_id,)).fetchone():
                raise AlreadyExistsError(f"stream {stream.stream_id!r} exists")
            self._require_peers(conn, stream.members)
            conn.execute(
                "INSERT INTO streams (stream_id, title, state, revision, metadata_json, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (stream.stream_id, stream.title, stream.state.value, stream.revision,
                 json.dumps(stream.metadata, ensure_ascii=False, allow_nan=False), stream.created_at),
            )
            self._set_members(conn, stream.stream_id, stream.members)
        return stream

    @staticmethod
    def _load_stream(conn: sqlite3.Connection, row: sqlite3.Row) -> Stream:
        members = [r["peer_id"] for r in conn.execute(
            "SELECT peer_id FROM stream_members WHERE stream_id = ? ORDER BY rowid", (row["stream_id"],))]
        return Stream(stream_id=row["stream_id"], title=row["title"], state=StreamState(row["state"]), members=members,
                      revision=row["revision"], metadata=json.loads(row["metadata_json"]), created_at=row["created_at"])

    def get_stream(self, stream_id: str) -> Stream | None:
        with self._read() as conn:
            row = conn.execute("SELECT * FROM streams WHERE stream_id = ?", (stream_id,)).fetchone()
            return self._load_stream(conn, row) if row else None

    def cas_stream(self, stream_id: str, expected_revision: int, mutation: dict[str, Any]) -> Stream:
        """Revision-CAS mutation (TD-09). Keys: state (OPEN->CLOSED only), members (replacement), title, metadata."""
        if not isinstance(mutation, dict) or not mutation or set(mutation) - STREAM_MUTATION_KEYS:
            raise ValueError(f"mutation must be a non-empty dict with keys from {sorted(STREAM_MUTATION_KEYS)}")
        if "metadata" in mutation:
            assert_json_value(mutation["metadata"], "metadata")
        with self._tx() as conn:
            row = self._require_stream(conn, stream_id)
            if row["revision"] != expected_revision:
                raise CasMismatchError(
                    f"stream {stream_id!r}: expected revision {expected_revision}, actual {row['revision']}")
            cur = self._load_stream(conn, row)
            data = cur.model_dump()
            if "state" in mutation:
                new_state = StreamState(mutation["state"])
                if cur.state is StreamState.CLOSED and new_state is StreamState.OPEN:
                    raise IllegalTransitionError("reopen is not an M1 API (TD-09)")
                data["state"] = new_state
            for k in ("members", "title", "metadata"):
                if k in mutation:
                    data[k] = mutation[k]
            new = Stream.model_validate({**data, "revision": cur.revision + 1})
            self._require_peers(conn, new.members)
            conn.execute(
                "UPDATE streams SET title=?, state=?, metadata_json=?, revision=? WHERE stream_id=? AND revision=?",
                (new.title, new.state.value, json.dumps(new.metadata, ensure_ascii=False, allow_nan=False),
                 new.revision, stream_id, expected_revision),
            )
            if "members" in mutation:
                self._set_members(conn, stream_id, new.members)
            return new

    # --- Record
    def _insert_record(self, conn: sqlite3.Connection, req: AppendRequest, digest: str) -> Record:
        """Validate references and append one Record inside the caller's write transaction (position = MAX + 1, TD-01)."""
        stream = self._require_stream(conn, req.stream_id)
        self._require_peers(conn, [req.author_peer_id])
        if stream["state"] == StreamState.CLOSED.value:
            raise StreamClosedError(f"stream {req.stream_id!r} is CLOSED")
        next_pos = conn.execute(
            "SELECT COALESCE(MAX(position), 0) + 1 FROM records WHERE stream_id = ?", (req.stream_id,)).fetchone()[0]
        now = utc_now_iso()
        rec_id = f"rec-{uuid.uuid4().hex[:12]}"
        conn.execute(
            """
            INSERT INTO records (record_id, stream_id, position, author_peer_id, kind, body_json, targets_json,
                reply_to, refs_json, metadata_json, idempotency_key, payload_digest, created_at, appended_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (rec_id, req.stream_id, next_pos, req.author_peer_id, req.kind,
             json.dumps(req.body, ensure_ascii=False, allow_nan=False),
             json.dumps(req.targets, ensure_ascii=False), req.reply_to, json.dumps(req.refs, ensure_ascii=False),
             json.dumps(req.metadata, ensure_ascii=False, allow_nan=False), req.idempotency_key, digest,
             req.created_at, now),
        )
        return self._row_to_record(conn.execute("SELECT * FROM records WHERE record_id = ?", (rec_id,)).fetchone())

    @contextmanager
    def transaction(self, point: str | None = None):
        """Public write transaction for multi-statement units of work (e.g. the legacy importer): BEGIN IMMEDIATE, all-or-nothing."""
        with self._tx(point) as conn:
            yield conn

    def append_record(self, *, guard: Callable[..., None] | None = None, **request: Any) -> Record:
        """Append (TD-20 scope: stream_id, author_peer_id, idempotency_key). Server-owned fields are rejected (TD-21).
        `guard(conn, stream_id=, peer_id=)` runs first inside the write transaction with the write target scope
        (fencing, TD-25); raising aborts with no mutation."""
        req = AppendRequest(**request)
        digest = compute_record_digest(req.model_dump())
        self._fire("append.begin")
        with self._tx("append") as conn:
            if guard is not None:
                guard(conn, stream_id=req.stream_id, peer_id=req.author_peer_id)
            existing = conn.execute(
                "SELECT * FROM records WHERE stream_id = ? AND author_peer_id = ? AND idempotency_key = ?",
                (req.stream_id, req.author_peer_id, req.idempotency_key),
            ).fetchone()
            if existing:
                if existing["payload_digest"] != digest:
                    raise IdempotencyConflictError(
                        f"idempotency key {req.idempotency_key!r} reused with different payload "
                        f"(stored {existing['payload_digest']}, got {digest})")
                return self._row_to_record(existing)
            rec = self._insert_record(conn, req, digest)
        self._fire("append.after_commit")  # committed; the caller has not been told yet (lost-response seam)
        return rec

    def read_records(self, stream_id: str, after_position: int = 0, limit: int = 100) -> list[Record]:
        """Exclusive of after_position; limit must be positive (TD-22)."""
        if isinstance(after_position, bool) or not isinstance(after_position, int) or after_position < 0:
            raise ValueError("after_position must be an integer >= 0")
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise ValueError("limit must be a positive integer")
        with self._read() as conn:
            rows = conn.execute(
                "SELECT * FROM records WHERE stream_id = ? AND position > ? ORDER BY position ASC LIMIT ?",
                (stream_id, after_position, limit),
            ).fetchall()
            return [self._row_to_record(r) for r in rows]

    def stream_head(self, stream_id: str) -> int:
        with self._read() as conn:
            self._require_stream(conn, stream_id)
            return conn.execute(
                "SELECT COALESCE(MAX(position), 0) FROM records WHERE stream_id = ?", (stream_id,)).fetchone()[0]

    # --- Offset
    def get_offset(self, peer_id: str, stream_id: str) -> Offset:
        """Absent row == effective zero (position 0, revision 1); never implies completion (OFF-001)."""
        with self._read() as conn:
            self._require_peers(conn, [peer_id])
            self._require_stream(conn, stream_id)
            row = conn.execute(
                "SELECT * FROM offsets WHERE peer_id = ? AND stream_id = ?", (peer_id, stream_id)).fetchone()
            if not row:
                return Offset(peer_id=peer_id, stream_id=stream_id, read_through_position=0, revision=1)
            return Offset(peer_id=peer_id, stream_id=stream_id,
                          read_through_position=row["read_through_position"], revision=row["revision"])

    def advance_offset_cas(self, peer_id: str, stream_id: str, new_position: int, expected_revision: int,
                           guard: Callable[..., None] | None = None) -> Offset:
        """Checks in order: references, revision CAS, monotonic (TD-03), head bound (TD-10). Accepted write bumps revision once."""
        if isinstance(new_position, bool) or not isinstance(new_position, int) or new_position < 0:
            raise ValueError("new_position must be an integer >= 0")
        self._fire("offset.begin")
        with self._tx() as conn:
            if guard is not None:
                guard(conn, stream_id=stream_id, peer_id=peer_id)
            self._require_peers(conn, [peer_id])
            self._require_stream(conn, stream_id)
            row = conn.execute(
                "SELECT revision, read_through_position FROM offsets WHERE peer_id = ? AND stream_id = ?",
                (peer_id, stream_id)).fetchone()
            cur_rev, cur_pos = (row["revision"], row["read_through_position"]) if row else (1, 0)
            if cur_rev != expected_revision:
                raise CasMismatchError(
                    f"offset ({peer_id}, {stream_id}): expected revision {expected_revision}, actual {cur_rev}")
            if new_position < cur_pos:
                raise OffsetRegressionError(f"offset cannot move backward ({cur_pos} -> {new_position})")
            self._fire("offset.before_head_check")
            head = conn.execute(
                "SELECT COALESCE(MAX(position), 0) FROM records WHERE stream_id = ?", (stream_id,)).fetchone()[0]
            if new_position > head:
                raise OffsetBeyondHeadError(f"offset {new_position} exceeds committed head {head}")
            if row:
                conn.execute(
                    "UPDATE offsets SET read_through_position = ?, revision = revision + 1 "
                    "WHERE peer_id = ? AND stream_id = ? AND revision = ?",
                    (new_position, peer_id, stream_id, expected_revision))
            else:
                conn.execute(
                    "INSERT INTO offsets (peer_id, stream_id, read_through_position, revision) VALUES (?, ?, ?, 2)",
                    (peer_id, stream_id, new_position))
            return Offset(peer_id=peer_id, stream_id=stream_id, read_through_position=new_position,
                          revision=cur_rev + 1)

    @staticmethod
    def _row_to_record(row: sqlite3.Row) -> Record:
        return Record(
            record_id=row["record_id"],
            stream_id=row["stream_id"],
            position=row["position"],
            author_peer_id=row["author_peer_id"],
            kind=row["kind"],
            body=json.loads(row["body_json"]) if row["body_json"] is not None else None,
            targets=json.loads(row["targets_json"]),
            reply_to=row["reply_to"],
            refs=json.loads(row["refs_json"]),
            metadata=json.loads(row["metadata_json"]),
            idempotency_key=row["idempotency_key"],
            payload_digest=row["payload_digest"],
            created_at=row["created_at"],
            appended_at=row["appended_at"],
        )
