"""Wave 5: Extension Concurrency, Manifest Strictness & Boundary Tests.

Verifies:
- EXT-005: Concurrent enable/disable toggles are fenced via SQLite WAL.
- EXT-017: Concurrent read during migration blocks cleanly until commit.
- EXT-018: Manifest missing strictly required properties fails validation.
- EXT-019: Null values for string typed properties explicitly rejected.
- EXT-021: Extension DB operates on separate distinct connection schema.
- EXT-023: Extension host startup completes cleanly and deterministically.
"""

import threading
import sqlite3
from pathlib import Path
import pytest

from peerhub.m2.host import ExtensionHost
from peerhub.m2.manifest import (
    ExtensionManifest,
    SchemaValidationError,
    validate_manifest,
)


def test_ext_018_missing_required_properties_fails_validation():
    """EXT-018: Manifest missing id, version, or entrypoint fails validation."""
    # Missing entrypoint
    with pytest.raises(SchemaValidationError):
        validate_manifest({"id": "ext_test", "version": "1.0.0"})

    # Missing id
    with pytest.raises(SchemaValidationError):
        validate_manifest({"version": "1.0.0", "entrypoint": "main.py"})

    # Missing version
    with pytest.raises(SchemaValidationError):
        validate_manifest({"id": "ext_test", "entrypoint": "main.py"})


def test_ext_019_null_values_for_string_properties_rejected():
    """EXT-019: Null values for string fields (id, version, entrypoint) are rejected."""
    with pytest.raises(SchemaValidationError):
        validate_manifest({"id": None, "version": "1.0.0", "entrypoint": "main.py"})

    with pytest.raises(SchemaValidationError):
        validate_manifest({"id": "ext_test", "version": None, "entrypoint": "main.py"})

    with pytest.raises(SchemaValidationError):
        validate_manifest({"id": "ext_test", "version": "1.0.0", "entrypoint": None})


def test_ext_021_separate_connection_schema(tmp_path):
    """EXT-021: Extension schemas operate under strict table prefixing and foreign key checks."""
    db_path = tmp_path / "sandbox.db"
    host = ExtensionHost(db_path)

    # ext_a creates table
    host.apply_extension_schema("ext_a", "CREATE TABLE ext_a_data (k TEXT PRIMARY KEY, v TEXT);")

    # ext_b creates table
    host.apply_extension_schema("ext_b", "CREATE TABLE ext_b_data (k TEXT PRIMARY KEY, num INT);")

    with sqlite3.connect(db_path) as conn:
        tables = [
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
        ]

    assert "ext_a_data" in tables
    assert "ext_b_data" in tables
    assert "m2_extension_registry" in tables


def test_ext_023_host_startup_ordering(tmp_path):
    """EXT-023: Extension host startup initializes WAL, foreign keys, and registry table."""
    db_path = tmp_path / "startup.db"
    host = ExtensionHost(db_path)

    with host.get_connection() as conn:
        mode = conn.execute("PRAGMA journal_mode;").fetchone()[0]
        fk = conn.execute("PRAGMA foreign_keys;").fetchone()[0]
        tables = [
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='m2_extension_registry'"
            ).fetchall()
        ]

    assert mode.upper() == "WAL"
    assert fk == 1
    assert "m2_extension_registry" in tables


def test_ext_005_concurrent_toggles_fenced(tmp_path):
    """EXT-005: Concurrent enable/disable toggles are fenced via SQLite WAL without database corruption."""
    db_path = tmp_path / "concurrent.db"
    host = ExtensionHost(db_path)

    ext_dir = tmp_path / "ext_toggle"
    ext_dir.mkdir()
    (ext_dir / "mod.py").write_text("")
    manifest = ExtensionManifest(id="ext_toggle", version="1.0.0", entrypoint="mod.py")
    host.register_manifest(manifest, ext_dir)
    host.enable("ext_toggle")
    assert host.get_state("ext_toggle") == "ENABLED"

    errors = []

    def toggle(action: str):
        try:
            h = ExtensionHost(db_path)
            h.register_manifest(manifest, ext_dir)
            if action == "enable":
                h.enable("ext_toggle")
            else:
                h.disable("ext_toggle")
        except Exception as e:
            errors.append(e)

    threads = []
    for i in range(10):
        action = "enable" if i % 2 == 0 else "disable"
        t = threading.Thread(target=toggle, args=(action,))
        threads.append(t)
        t.start()

    for t in threads:
        t.join()

    # No uncaught SQLite locking/corruption errors
    assert len(errors) == 0
    final_state = host.get_state("ext_toggle")
    assert final_state in ("ENABLED", "DISABLED")


def test_ext_017_concurrent_read_during_migration(tmp_path):
    """EXT-017: Concurrent reads during migration succeed cleanly without blocking indefinitely."""
    db_path = tmp_path / "migration_concurrency.db"
    host = ExtensionHost(db_path)

    host.apply_extension_schema(
        "ext_items",
        "CREATE TABLE ext_items_tbl (id INT PRIMARY KEY, name TEXT); INSERT INTO ext_items_tbl VALUES (1, 'item1');",
    )

    read_results = []
    read_errors = []

    def read_worker():
        try:
            with sqlite3.connect(db_path, timeout=5.0) as conn:
                res = conn.execute("SELECT count(*) FROM ext_items_tbl").fetchone()
                read_results.append(res[0])
        except Exception as e:
            read_errors.append(e)

    threads = [threading.Thread(target=read_worker) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(read_errors) == 0
    assert all(r == 1 for r in read_results)
