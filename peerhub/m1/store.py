"""PeerHub M1 Core Persistence Layer.

Authoritative SQLite store supporting:
- WAL mode & foreign keys
- Monotonically increasing append position
- Idempotent append (reuse on same key+digest, conflict error on mismatch)
- Compare-And-Swap (CAS) Offset tracking
- Core invariant: Does NOT import any extension.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any
import uuid

from .models import Offset, Peer, Record, Stream, StreamState, compute_payload_digest, utc_now_iso


class IdempotencyConflictError(Exception):
    """Raised when an idempotency key is reused with a different payload digest."""


class CasMismatchError(Exception):
    """Raised when Offset update fails due to a revision mismatch."""


class CoreStore:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = str(db_path)
        self._init_schema()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.execute("PRAGMA busy_timeout = 30000;")
        return conn

    def _init_schema(self) -> None:
        with self._get_connection() as conn:
            conn.executescript("""
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
                    UNIQUE (stream_id, idempotency_key)
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
            """)

    def register_peer(self, peer: Peer) -> Peer:
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO peers (peer_id, display_name, adapter_ref, metadata_json, created_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(peer_id) DO UPDATE SET
                    display_name=excluded.display_name,
                    adapter_ref=excluded.adapter_ref,
                    metadata_json=excluded.metadata_json
                """,
                (
                    peer.peer_id,
                    peer.display_name,
                    peer.adapter_ref,
                    json.dumps(peer.metadata, ensure_ascii=False),
                    peer.created_at,
                ),
            )
        return peer

    def get_peer(self, peer_id: str) -> Peer | None:
        with self._get_connection() as conn:
            row = conn.execute("SELECT * FROM peers WHERE peer_id = ?", (peer_id,)).fetchone()
            if not row:
                return None
            return Peer(
                peer_id=row["peer_id"],
                display_name=row["display_name"],
                adapter_ref=row["adapter_ref"],
                metadata=json.loads(row["metadata_json"]),
                created_at=row["created_at"],
            )

    def create_stream(self, stream: Stream) -> Stream:
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO streams (stream_id, title, state, revision, metadata_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    stream.stream_id,
                    stream.title,
                    stream.state.value,
                    stream.revision,
                    json.dumps(stream.metadata, ensure_ascii=False),
                    stream.created_at,
                ),
            )
            for m in stream.members:
                conn.execute(
                    "INSERT OR IGNORE INTO stream_members (stream_id, peer_id) VALUES (?, ?)",
                    (stream.stream_id, m),
                )
        return stream

    def get_stream(self, stream_id: str) -> Stream | None:
        with self._get_connection() as conn:
            row = conn.execute("SELECT * FROM streams WHERE stream_id = ?", (stream_id,)).fetchone()
            if not row:
                return None
            member_rows = conn.execute(
                "SELECT peer_id FROM stream_members WHERE stream_id = ? ORDER BY peer_id",
                (stream_id,),
            ).fetchall()
            members = [r["peer_id"] for r in member_rows]
            return Stream(
                stream_id=row["stream_id"],
                title=row["title"],
                state=StreamState(row["state"]),
                members=members,
                revision=row["revision"],
                metadata=json.loads(row["metadata_json"]),
                created_at=row["created_at"],
            )

    def append_record(
        self,
        *,
        stream_id: str,
        author_peer_id: str,
        kind: str,
        body: Any,
        idempotency_key: str,
        targets: list[str] | None = None,
        reply_to: str | None = None,
        refs: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        record_id: str | None = None,
    ) -> Record:
        targets = targets or []
        refs = refs or []
        metadata = metadata or {}
        payload_digest = compute_payload_digest(kind, body)
        rec_id = record_id or f"rec-{uuid.uuid4().hex[:12]}"
        now = utc_now_iso()

        with self._get_connection() as conn:
            # Check existing idempotency_key for the stream
            existing = conn.execute(
                "SELECT * FROM records WHERE stream_id = ? AND idempotency_key = ?",
                (stream_id, idempotency_key),
            ).fetchone()
            if existing:
                if existing["payload_digest"] != payload_digest:
                    raise IdempotencyConflictError(
                        f"Idempotency key '{idempotency_key}' mismatch: "
                        f"expected {existing['payload_digest']}, got {payload_digest}"
                    )
                return self._row_to_record(existing)

            # Atomic monotonic position assignment within transaction
            pos_row = conn.execute(
                "SELECT COALESCE(MAX(position), 0) + 1 AS next_pos FROM records WHERE stream_id = ?",
                (stream_id,),
            ).fetchone()
            next_pos = pos_row["next_pos"] if pos_row else 1

            conn.execute(
                """
                INSERT INTO records (
                    record_id, stream_id, position, author_peer_id, kind, body_json,
                    targets_json, reply_to, refs_json, metadata_json,
                    idempotency_key, payload_digest, created_at, appended_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    rec_id,
                    stream_id,
                    next_pos,
                    author_peer_id,
                    kind,
                    json.dumps(body, ensure_ascii=False),
                    json.dumps(targets, ensure_ascii=False),
                    reply_to,
                    json.dumps(refs, ensure_ascii=False),
                    json.dumps(metadata, ensure_ascii=False),
                    idempotency_key,
                    payload_digest,
                    now,
                    now,
                ),
            )
            created_row = conn.execute(
                "SELECT * FROM records WHERE record_id = ?", (rec_id,)
            ).fetchone()
            return self._row_to_record(created_row)

    def read_records(
        self,
        stream_id: str,
        after_position: int = 0,
        limit: int = 100,
    ) -> list[Record]:
        with self._get_connection() as conn:
            rows = conn.execute(
                """
                SELECT * FROM records
                WHERE stream_id = ? AND position > ?
                ORDER BY position ASC
                LIMIT ?
                """,
                (stream_id, after_position, limit),
            ).fetchall()
            return [self._row_to_record(r) for r in rows]

    def get_offset(self, peer_id: str, stream_id: str) -> Offset:
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM offsets WHERE peer_id = ? AND stream_id = ?",
                (peer_id, stream_id),
            ).fetchone()
            if not row:
                return Offset(peer_id=peer_id, stream_id=stream_id, read_through_position=0, revision=1)
            return Offset(
                peer_id=row["peer_id"],
                stream_id=row["stream_id"],
                read_through_position=row["read_through_position"],
                revision=row["revision"],
            )

    def advance_offset_cas(
        self,
        peer_id: str,
        stream_id: str,
        new_position: int,
        expected_revision: int,
    ) -> Offset:
        with self._get_connection() as conn:
            # Check if row exists
            existing = conn.execute(
                "SELECT revision, read_through_position FROM offsets WHERE peer_id = ? AND stream_id = ?",
                (peer_id, stream_id),
            ).fetchone()

            if not existing:
                if expected_revision != 1:
                    raise CasMismatchError(
                        f"Offset does not exist; expected revision 1, got {expected_revision}"
                    )
                conn.execute(
                    """
                    INSERT INTO offsets (peer_id, stream_id, read_through_position, revision)
                    VALUES (?, ?, ?, 2)
                    """,
                    (peer_id, stream_id, new_position),
                )
                return Offset(
                    peer_id=peer_id,
                    stream_id=stream_id,
                    read_through_position=new_position,
                    revision=2,
                )

            if existing["revision"] != expected_revision:
                raise CasMismatchError(
                    f"CAS failed for offset ({peer_id}, {stream_id}): "
                    f"expected revision {expected_revision}, actual {existing['revision']}"
                )

            cur = conn.execute(
                """
                UPDATE offsets
                SET read_through_position = ?, revision = revision + 1
                WHERE peer_id = ? AND stream_id = ? AND revision = ?
                """,
                (new_position, peer_id, stream_id, expected_revision),
            )
            if cur.rowcount == 0:
                raise CasMismatchError("CAS update failed due to concurrent modification")

            return Offset(
                peer_id=peer_id,
                stream_id=stream_id,
                read_through_position=new_position,
                revision=expected_revision + 1,
            )

    def _row_to_record(self, row: sqlite3.Row) -> Record:
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
