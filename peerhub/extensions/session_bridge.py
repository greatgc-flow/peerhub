"""PeerHub M1 First-Party Extension: Session Bridge.

Key Responsibilities:
- Single-active delivery claim: prevent dual bridges executing same unread record.
- Execution certainty: NOT_STARTED, MAY_HAVE_STARTED, STARTED, TERMINAL.
- Blind replay prevention for MAY_HAVE_STARTED.
"""

from __future__ import annotations

from peerhub.extensions.schema_guard import refuse_future_schema

import sqlite3
import time
from enum import Enum
from pathlib import Path
from pydantic import BaseModel, ConfigDict


class ExecutionCertainty(str, Enum):
    NOT_STARTED = "NOT_STARTED"
    MAY_HAVE_STARTED = "MAY_HAVE_STARTED"
    STARTED = "STARTED"
    TERMINAL = "TERMINAL"


class DeliveryClaim(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str
    stream_id: str
    peer_id: str
    owner_bridge_id: str
    generation: int
    expires_at_timestamp: float


class SessionBridgeStore:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = str(db_path)
        refuse_future_schema(self.db_path)  # MIG-003: refuse a future schema before any DDL/DML
        self._init_schema()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL;")
        return conn

    def _init_schema(self) -> None:
        with self._get_connection() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS delivery_claims (
                    workspace_id TEXT NOT NULL,
                    stream_id TEXT NOT NULL,
                    peer_id TEXT NOT NULL,
                    owner_bridge_id TEXT NOT NULL,
                    generation INTEGER NOT NULL DEFAULT 1,
                    expires_at_timestamp REAL NOT NULL,
                    PRIMARY KEY (workspace_id, stream_id, peer_id)
                );

                CREATE TABLE IF NOT EXISTS runtime_sessions (
                    peer_id TEXT NOT NULL,
                    stream_id TEXT NOT NULL,
                    runtime_kind TEXT NOT NULL,
                    external_session_id TEXT,
                    session_generation INTEGER NOT NULL DEFAULT 1,
                    adapter_fingerprint TEXT NOT NULL,
                    certainty TEXT NOT NULL,
                    resumable INTEGER NOT NULL DEFAULT 1,
                    last_seen TEXT NOT NULL,
                    PRIMARY KEY (peer_id, stream_id)
                );
            """)

    def acquire_delivery_claim(
        self,
        *,
        workspace_id: str,
        stream_id: str,
        peer_id: str,
        bridge_id: str,
        lease_duration_sec: float = 30.0,
    ) -> bool:
        now = time.time()
        expires = now + lease_duration_sec

        with self._get_connection() as conn:
            existing = conn.execute(
                """
                SELECT owner_bridge_id, generation, expires_at_timestamp
                FROM delivery_claims
                WHERE workspace_id = ? AND stream_id = ? AND peer_id = ?
                """,
                (workspace_id, stream_id, peer_id),
            ).fetchone()

            if not existing:
                conn.execute(
                    """
                    INSERT INTO delivery_claims
                    (workspace_id, stream_id, peer_id, owner_bridge_id, generation, expires_at_timestamp)
                    VALUES (?, ?, ?, ?, 1, ?)
                    """,
                    (workspace_id, stream_id, peer_id, bridge_id, expires),
                )
                return True

            # If owned by same bridge or lease expired -> renew/take over
            if existing["owner_bridge_id"] == bridge_id or existing["expires_at_timestamp"] < now:
                new_gen = existing["generation"] + 1
                conn.execute(
                    """
                    UPDATE delivery_claims
                    SET owner_bridge_id = ?, generation = ?, expires_at_timestamp = ?
                    WHERE workspace_id = ? AND stream_id = ? AND peer_id = ?
                    """,
                    (bridge_id, new_gen, expires, workspace_id, stream_id, peer_id),
                )
                return True

            return False
