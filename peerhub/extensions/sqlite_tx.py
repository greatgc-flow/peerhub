"""A SQLite transaction that also CLOSES its connection.

`with sqlite3.connect(path) as conn:` commits or rolls back but leaves the connection open until garbage collection. On Windows an
open handle blocks deleting a projection database or swapping a workspace directory (restore), so extension code uses this instead.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import Any


@contextmanager
def sqlite_tx(path: str | Path, **kwargs: Any) -> Generator[sqlite3.Connection]:
    conn = sqlite3.connect(path, **kwargs)
    try:
        with conn:  # commit on success, roll back on error
            yield conn
    finally:
        conn.close()
