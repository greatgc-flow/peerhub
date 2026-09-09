"""Detect a workspace's persisted identity, with a directory-name fallback.

Extracted so both the CLI's ordinary bootstrap path and the backup/restore
path (item 10, dotdir consolidation, ratified 2026-09-09) share the exact
same rule instead of two copies drifting apart.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path


def detect_workspace_home_id(database_path: Path, fallback_name: str) -> str:
    """Read the persisted workspace identity from an existing database.

    Falls back to ``fallback_name`` (typically the workspace directory
    name) when no database exists yet, its identity table is absent, or
    the file cannot be opened as SQLite -- "no identity recorded yet" is
    not an error at this layer; callers that need a database to already
    be initialized enforce that separately.
    """

    if database_path.is_file():
        try:
            connection = sqlite3.connect(str(database_path))
            try:
                row = connection.execute(
                    "SELECT workspace_home_id FROM workspace_identity WHERE singleton = 1"
                ).fetchone()
                if row and row[0]:
                    return str(row[0])
            finally:
                connection.close()
        except sqlite3.Error:
            pass
    return fallback_name or "cli"
