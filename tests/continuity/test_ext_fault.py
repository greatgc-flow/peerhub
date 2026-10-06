"""Wave 2: EXT-008 - Extension Schema Migration Crash Rollback.

Verifies:
- EXT-008: If an extension schema migration encounters an error or crash halfway through,
  SQLite WAL transaction rollback guarantees the database reverts to its prior valid state with no partial tables.
"""

import sqlite3
from pathlib import Path
import pytest

from peerhub.extensions.host import ExtensionHost


@pytest.mark.fault
def test_ext_008_migration_crash_rolls_back_cleanly(tmp_path):
    """EXT-008: A multi-statement migration that fails halfway leaves zero partial tables."""
    db_path = tmp_path / "test_migration_crash.db"
    host = ExtensionHost(db_path)

    # Broken migration: first creates a valid table, then raises a syntax error or constraint error
    broken_migration_sql = """
    CREATE TABLE ext_crash_partial (
        id TEXT PRIMARY KEY,
        val TEXT
    );
    INSERT INTO ext_crash_partial (id, val) VALUES ('1', 'ok');
    THIS IS INVALID SQL TO SIMULATE RUNTIME MIGRATION CRASH;
    """

    with pytest.raises(sqlite3.OperationalError):
        host.apply_extension_schema("ext_crash", broken_migration_sql)

    # Verify atomic rollback: ext_crash_partial must NOT exist in the database
    with sqlite3.connect(db_path) as conn:
        tables = [
            row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
        ]

    assert "ext_crash_partial" not in tables, (
        f"Partial table leaked despite migration failure: {tables}"
    )
