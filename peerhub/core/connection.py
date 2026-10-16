"""Central SQLite connection factory (Slice 1).

Enforces:
- PRAGMA journal_mode = WAL
- PRAGMA busy_timeout = 5000 (default)
- PRAGMA foreign_keys = ON
- PRAGMA recursive_triggers = ON
Every checkout asserts PRAGMA foreign_keys == 1.
"""
from __future__ import annotations

import os
from pathlib import Path
import sqlite3

from .migrations import enable_wal


def _sqlite3_module():
    import sys
    store_mod = sys.modules.get("peerhub.core.store")
    if store_mod is not None and hasattr(store_mod, "sqlite3"):
        return store_mod.sqlite3
    return sqlite3


def create_connection(
    path: str | os.PathLike[str],
    *,
    readonly: bool = False,
    timeout_ms: int = 5000,
) -> sqlite3.Connection:
    """Create and configure a SQLite connection enforcing standard pragmas."""
    sql = _sqlite3_module()
    db_path = Path(path)
    if readonly:
        uri = db_path.resolve().as_uri() + "?mode=ro"
        conn = sql.connect(uri, uri=True, timeout=timeout_ms / 1000.0)
        conn.execute("PRAGMA query_only = ON;")
    else:
        conn = sql.connect(str(db_path), timeout=timeout_ms / 1000.0)
        enable_wal(conn)

    conn.execute(f"PRAGMA busy_timeout = {int(timeout_ms)};")
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA recursive_triggers = ON;")

    # Assert checkout invariant
    fk = conn.execute("PRAGMA foreign_keys").fetchone()[0]
    if fk != 1:
        conn.close()
        raise RuntimeError("failed to enforce PRAGMA foreign_keys == 1")

    return conn
