"""Wave 1: EXT-003 - Extension Schema Prefix Isolation.

Verifies:
- EXT-003: Extension DB tables are strictly prefixed with 'ext_' to prevent collisions with Core tables.
"""

import sqlite3
from pathlib import Path
import pytest

from peerhub.extensions.host import ExtensionHost, SchemaPrefixViolationError


@pytest.mark.schema
def test_ext_003_extension_tables_must_be_strictly_prefixed(tmp_path):
    """EXT-003: Applying an extension schema with un-prefixed tables raises SchemaPrefixViolationError."""
    db_path = tmp_path / "test_ext_registry.db"
    host = ExtensionHost(db_path)

    # 1. Valid extension with prefixed tables
    valid_migration_sql = """
    CREATE TABLE IF NOT EXISTS ext_mock_items (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL
    );
    """
    host.apply_extension_schema("ext_mock", valid_migration_sql)

    conn = sqlite3.connect(db_path)
    tables = [
        row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
    ]
    conn.close()

    assert "ext_mock_items" in tables
    assert all(t.startswith("ext_") or t.startswith("m2_") for t in tables)

    # 2. Rogue extension attempting to create un-prefixed table (e.g. attempting to hijack 'peers')
    rogue_migration_sql = """
    CREATE TABLE rogue_table (
        id TEXT PRIMARY KEY
    );
    """
    with pytest.raises(SchemaPrefixViolationError, match="rogue_table"):
        host.apply_extension_schema("ext_rogue", rogue_migration_sql)
