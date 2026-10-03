"""Migration test fixtures: legacy unversioned schema (what Wave 1 shipped) and a test-only N->N+1 step."""
from __future__ import annotations

import sqlite3

from peerhub.m1.migrations import MIGRATIONS, Migration

# Wave-1 production schema, frozen as the "prior supported schema" (user_version 0, no metadata).
LEGACY_V0_SCHEMA = """
CREATE TABLE peers (peer_id TEXT PRIMARY KEY, display_name TEXT, adapter_ref TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL);
CREATE TABLE streams (stream_id TEXT PRIMARY KEY, title TEXT, state TEXT NOT NULL,
    revision INTEGER NOT NULL DEFAULT 1, metadata_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL);
CREATE TABLE stream_members (stream_id TEXT NOT NULL, peer_id TEXT NOT NULL, PRIMARY KEY (stream_id, peer_id),
    FOREIGN KEY (stream_id) REFERENCES streams(stream_id) ON DELETE CASCADE,
    FOREIGN KEY (peer_id) REFERENCES peers(peer_id) ON DELETE CASCADE);
CREATE TABLE records (record_id TEXT PRIMARY KEY, stream_id TEXT NOT NULL, position INTEGER NOT NULL,
    author_peer_id TEXT NOT NULL, kind TEXT NOT NULL, body_json TEXT, targets_json TEXT NOT NULL DEFAULT '[]',
    reply_to TEXT, refs_json TEXT NOT NULL DEFAULT '[]', metadata_json TEXT NOT NULL DEFAULT '{}',
    idempotency_key TEXT NOT NULL, payload_digest TEXT NOT NULL, created_at TEXT NOT NULL, appended_at TEXT NOT NULL,
    FOREIGN KEY (stream_id) REFERENCES streams(stream_id) ON DELETE CASCADE,
    FOREIGN KEY (author_peer_id) REFERENCES peers(peer_id) ON DELETE RESTRICT,
    UNIQUE (stream_id, position), UNIQUE (stream_id, author_peer_id, idempotency_key));
CREATE TABLE offsets (peer_id TEXT NOT NULL, stream_id TEXT NOT NULL, read_through_position INTEGER NOT NULL DEFAULT 0,
    revision INTEGER NOT NULL DEFAULT 1, PRIMARY KEY (peer_id, stream_id),
    FOREIGN KEY (peer_id) REFERENCES peers(peer_id) ON DELETE CASCADE,
    FOREIGN KEY (stream_id) REFERENCES streams(stream_id) ON DELETE CASCADE);
CREATE INDEX idx_records_stream_position ON records(stream_id, position);
CREATE TRIGGER records_immutable_update BEFORE UPDATE ON records BEGIN SELECT RAISE(ABORT, 'records are immutable'); END;
CREATE TRIGGER records_immutable_delete BEFORE DELETE ON records BEGIN SELECT RAISE(ABORT, 'records are immutable'); END;
"""


def _step_add_peer_note(conn: sqlite3.Connection) -> None:
    conn.execute("ALTER TABLE peers ADD COLUMN note TEXT")
    conn.execute("CREATE INDEX idx_records_author ON records(author_peer_id)")


FIXTURE_MIGRATIONS = [*MIGRATIONS, Migration(MIGRATIONS[-1].version + 1, "fixture_add_peer_note", _step_add_peer_note)]
