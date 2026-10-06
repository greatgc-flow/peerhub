"""Extension stores share the Core database file: they must refuse a future/unknown Core schema BEFORE any DDL/DML (MIG-003, TD-14)."""
from __future__ import annotations

import sqlite3
import struct
from pathlib import Path

from peerhub.core.schema_version import SUPPORTED_SCHEMA_VERSION, SchemaVersionError, future_schema_message


def _stored_version(path: Path) -> int:
    try:
        conn = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=5.0)
        try:
            return conn.execute("PRAGMA user_version").fetchone()[0]
        finally:
            conn.close()
    except sqlite3.DatabaseError:
        with open(path, "rb") as f:  # header fallback (byte offset 60, big-endian): still read-only
            head = f.read(100)
        if len(head) < 64 or not head.startswith(b"SQLite format 3\x00"):
            return 0  # not a SQLite file: the store's own open reports it
        return struct.unpack(">I", head[60:64])[0]


def refuse_future_schema(db_path: str | Path) -> None:
    """Raise SchemaVersionError when the file is stamped newer than this build supports. Never writes."""
    p = Path(db_path)
    if not p.exists() or p.stat().st_size == 0:
        return
    found = _stored_version(p)
    if found > SUPPORTED_SCHEMA_VERSION:
        raise SchemaVersionError(future_schema_message(found, SUPPORTED_SCHEMA_VERSION))
