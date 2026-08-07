import pytest
from pathlib import Path
from peerhub.persistence.sqlite import SqliteStateStore

def test_migrations_9_and_10_recorded(tmp_path: Path):
    db_path = tmp_path / "test.db"
    store = SqliteStateStore(db_path, workspace_home_id="home-1")
    store.initialize()
    
    import sqlite3
    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT version FROM schema_migrations WHERE version IN (9, 10)")
        versions = {row[0] for row in cursor.fetchall()}
        
        assert 9 in versions, "Migration 9 not recorded in schema_migrations"
        assert 10 in versions, "Migration 10 not recorded in schema_migrations"
        
        cursor.execute("PRAGMA user_version")
        user_version = cursor.fetchone()[0]
        # At least 11 because 0011 exists, but we just check it's >= 10
        assert user_version >= 10, f"user_version is {user_version}, expected at least 10"
