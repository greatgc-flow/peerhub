"""Small read-only SQLite checks used before trusting a snapshot."""

from __future__ import annotations

import sqlite3


def probe_table_roots(conn: sqlite3.Connection) -> None:
    """Touch both ends of every table root without scanning complete table contents."""
    tables = conn.execute(
        "SELECT name, sql FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    for table in tables:
        name = str(table[0]).replace('"', '""')
        if "WITHOUT ROWID" in str(table[1] or "").upper():
            conn.execute(f'SELECT 1 FROM "{name}" LIMIT 1').fetchone()
        else:
            conn.execute(f'SELECT rowid FROM "{name}" ORDER BY rowid LIMIT 1').fetchone()
            conn.execute(f'SELECT rowid FROM "{name}" ORDER BY rowid DESC LIMIT 1').fetchone()
