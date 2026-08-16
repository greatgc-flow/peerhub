import sqlite3
from pathlib import Path
import pytest
from peerhub.persistence.sqlite import SqliteStateStore

def _store(path: Path) -> SqliteStateStore:
    return SqliteStateStore(path, workspace_home_id="test-workspace")

def test_v22_migrates_to_v23_cleanly(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db_path = tmp_path / "peerhub.sqlite3"

    # Init up to v22
    from importlib import resources
    real_migrations = Path(str(resources.files("peerhub.persistence.migrations")))
    migration_dir = tmp_path / "migrations"
    migration_dir.mkdir()

    import shutil
    for entry in sorted(real_migrations.glob("[0-9][0-9][0-9][0-9]_*.sql")):
        version = int(entry.name[:4])
        if version <= 22:
            shutil.copy2(entry, migration_dir / entry.name)

    monkeypatch.setattr(
        SqliteStateStore,
        "_migration_directory",
        staticmethod(lambda: migration_dir),
        raising=False,
    )

    store = _store(db_path)
    store.initialize()
    store.close()

    # Now add v23 and initialize to migrate
    shutil.copy2(real_migrations / "0023_evidence_artifacts.sql", migration_dir / "0023_evidence_artifacts.sql")

    store = _store(db_path)
    store.initialize()
    store.close()

    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        violations = conn.execute("PRAGMA foreign_key_check").fetchall()
        assert not violations
        
        # Check table exists and schema
        row = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='evidence_artifacts'").fetchone()
        assert row is not None

        # Insert a row to verify constraints
        conn.execute("INSERT INTO evidence_artifacts (artifact_id, source_tool_name, content_length, sha256_hex, created_at, expires_at) VALUES ('art1', 'tool1', 10, 'hash', 1, 2)")
        
        # Missing field (should raise IntegrityError due to NOT NULL constraint)
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO evidence_artifacts (artifact_id, source_tool_name, content_length, sha256_hex, created_at) VALUES ('art2', 'tool1', 10, 'hash', 1)")
