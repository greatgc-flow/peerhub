"""M2.0 Generic Extension Host Implementation.

Adheres strictly to M2_0_EXTENSION_HOST_CONTRACT.md:
- SQLite WAL transaction isolation for extension schemas
- Strict prefix enforcement: all extension tables must begin with 'ext_' (or 'm2_')
"""

from __future__ import annotations

from pathlib import Path
import re
import sqlite3


class SchemaPrefixViolationError(ValueError):
    """Extension attempted to create or alter an unprefixed database table."""


CREATE_TABLE_PATTERN = re.compile(
    r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?([\"`\[]?([a-zA-Z0-9_]+)[\"`\]]?)",
    re.IGNORECASE,
)


class ExtensionHost:
    """Core extension host managing discovery, lifecycle, and schema boundaries."""

    def __init__(self, db_path: Path | str) -> None:
        self.db_path = Path(db_path).resolve()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("PRAGMA journal_mode = WAL;")
            conn.execute("PRAGMA foreign_keys = ON;")
            conn.execute("""
            CREATE TABLE IF NOT EXISTS m2_extension_registry (
                id TEXT PRIMARY KEY,
                version TEXT NOT NULL,
                entrypoint TEXT NOT NULL,
                state TEXT NOT NULL,
                schema_version INTEGER NOT NULL DEFAULT 1,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                installed_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
            );
            """)

    def apply_extension_schema(self, ext_id: str, sql: str) -> None:
        """Apply an extension schema migration under strict table prefix enforcement."""
        # 1. Scan and validate all table declarations in SQL
        matches = CREATE_TABLE_PATTERN.findall(sql)
        for _, raw_name in matches:
            clean_name = raw_name.strip("\"'`[]")
            if not (clean_name.startswith("ext_") or clean_name.startswith("m2_")):
                raise SchemaPrefixViolationError(
                    f"Extension {ext_id!r} attempted to create un-prefixed table {clean_name!r}; "
                    f"all extension tables must start with 'ext_'"
                )

        # 2. Execute migration inside transaction
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("PRAGMA foreign_keys = ON;")
            conn.executescript(sql)
