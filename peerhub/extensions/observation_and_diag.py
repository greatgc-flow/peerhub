"""PeerHub M1 First-Party Extension: Observation & Readonly Diag.

Observation:
- Quota / Rate limit distinction
- Freshness evaluation at read-time
- Evidence vocabulary (MEASURED, ABSENT, UNAVAILABLE, ERROR, STALE, UNKNOWN)
- Honesty: Unmeasured values remain UNKNOWN (never defaulted to 0 or healthy)

Diag:
- Strict read-only observer over Core & Observation
- Never mutates state or appends records
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any
from pydantic import BaseModel, ConfigDict, Field

from peerhub.m1.models import Offset, Record, Stream, utc_now_iso


class EvidenceState(str, Enum):
    MEASURED = "MEASURED"
    ABSENT = "ABSENT"
    UNAVAILABLE = "UNAVAILABLE"
    ERROR = "ERROR"
    STALE = "STALE"
    UNKNOWN = "UNKNOWN"


class ObservationKind(str, Enum):
    REACHABILITY = "reachability"
    CLI_VERSION = "cli_version"
    RUNTIME_CAPABILITY = "runtime_capability"
    SESSION = "session"
    QUOTA = "quota"
    RATE_LIMIT = "rate_limit"
    ACTIVITY = "activity"
    EXECUTION_FAILURE = "execution_failure"


class Observation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    observation_id: str = Field(..., min_length=1)
    subject_ref: str = Field(..., min_length=1)
    resource_pool_ref: str | None = None
    kind: ObservationKind
    source: str = Field(..., min_length=1)
    state: EvidenceState = EvidenceState.MEASURED
    payload: dict[str, Any] = Field(default_factory=dict)
    observed_at: str = Field(default_factory=utc_now_iso)
    captured_at: str = Field(default_factory=utc_now_iso)
    ttl_seconds: int = 300  # Default 5 min freshness window

    def evaluated_state(self, as_of: datetime | None = None) -> EvidenceState:
        """Evaluate freshness on-demand during read."""
        if self.state != EvidenceState.MEASURED:
            return self.state
        now = as_of or datetime.now(timezone.utc)
        obs_dt = datetime.fromisoformat(self.observed_at)
        delta = (now - obs_dt).total_seconds()
        if delta > self.ttl_seconds:
            return EvidenceState.STALE
        return EvidenceState.MEASURED


class ObservationStore:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = str(db_path)
        self._init_schema()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL;")
        return conn

    def _init_schema(self) -> None:
        with self._get_connection() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS observations (
                    observation_id TEXT PRIMARY KEY,
                    subject_ref TEXT NOT NULL,
                    resource_pool_ref TEXT,
                    kind TEXT NOT NULL,
                    source TEXT NOT NULL,
                    state TEXT NOT NULL,
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    observed_at TEXT NOT NULL,
                    captured_at TEXT NOT NULL,
                    ttl_seconds INTEGER NOT NULL DEFAULT 300
                );
                CREATE INDEX IF NOT EXISTS idx_obs_subject_kind ON observations(subject_ref, kind);
            """)

    def record_observation(self, obs: Observation) -> None:
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO observations (
                    observation_id, subject_ref, resource_pool_ref, kind, source,
                    state, payload_json, observed_at, captured_at, ttl_seconds
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(observation_id) DO UPDATE SET
                    state=excluded.state,
                    payload_json=excluded.payload_json,
                    observed_at=excluded.observed_at,
                    captured_at=excluded.captured_at,
                    ttl_seconds=excluded.ttl_seconds
                """,
                (
                    obs.observation_id,
                    obs.subject_ref,
                    obs.resource_pool_ref,
                    obs.kind.value,
                    obs.source,
                    obs.state.value,
                    json.dumps(obs.payload, ensure_ascii=False),
                    obs.observed_at,
                    obs.captured_at,
                    obs.ttl_seconds,
                ),
            )

    def get_latest_observation(
        self, subject_ref: str, kind: ObservationKind
    ) -> Observation | None:
        with self._get_connection() as conn:
            row = conn.execute(
                """
                SELECT * FROM observations
                WHERE subject_ref = ? AND kind = ?
                ORDER BY observed_at DESC LIMIT 1
                """,
                (subject_ref, kind.value),
            ).fetchone()
            if not row:
                return None
            obs = Observation(
                observation_id=row["observation_id"],
                subject_ref=row["subject_ref"],
                resource_pool_ref=row["resource_pool_ref"],
                kind=ObservationKind(row["kind"]),
                source=row["source"],
                state=EvidenceState(row["state"]),
                payload=json.loads(row["payload_json"]),
                observed_at=row["observed_at"],
                captured_at=row["captured_at"],
                ttl_seconds=row["ttl_seconds"],
            )
            # Freshness evaluated at read time
            obs.state = obs.evaluated_state()
            return obs


class ReadonlyDiag:
    """Strict read-only diagnostics view."""

    def __init__(self, core_db_path: str | Path, obs_db_path: str | Path) -> None:
        self.core_db_path = str(core_db_path)
        self.obs_db_path = str(obs_db_path)

    def _open_readonly(self, db_path: str) -> sqlite3.Connection:
        uri = f"file:{Path(db_path).resolve().as_posix()}?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
        conn.row_factory = sqlite3.Row
        return conn

    def inspect_stream_health(self, stream_id: str) -> dict[str, Any]:
        with self._open_readonly(self.core_db_path) as conn:
            stream_row = conn.execute(
                "SELECT * FROM streams WHERE stream_id = ?", (stream_id,)
            ).fetchone()
            if not stream_row:
                return {"status": "NOT_FOUND", "stream_id": stream_id}

            members = [
                r["peer_id"]
                for r in conn.execute(
                    "SELECT peer_id FROM stream_members WHERE stream_id = ?", (stream_id,)
                ).fetchall()
            ]

            max_pos_row = conn.execute(
                "SELECT COALESCE(MAX(position), 0) AS max_pos FROM records WHERE stream_id = ?",
                (stream_id,),
            ).fetchone()
            head_position = max_pos_row["max_pos"] if max_pos_row else 0

            offsets = {
                r["peer_id"]: r["read_through_position"]
                for r in conn.execute(
                    "SELECT peer_id, read_through_position FROM offsets WHERE stream_id = ?",
                    (stream_id,),
                ).fetchall()
            }

        return {
            "status": "OK",
            "stream_id": stream_id,
            "title": stream_row["title"],
            "state": stream_row["state"],
            "head_position": head_position,
            "members": members,
            "offsets": offsets,
        }
