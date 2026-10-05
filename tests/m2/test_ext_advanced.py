"""Wave 4: Extension Advanced Hooks, Schema Evolution & Conflict Tests.

Verifies:
- EXT-013: Extension hook runtime exception is trapped safely, does not crash Core, maps extension to FAILED.
- EXT-015: Duplicate manifest registration with different content raises RegistrationConflictError.
- EXT-016: Rebuilding extension registry clears internal memory cache and evicts loaded modules.
- EXT-020: Schema migration successfully handles ADD COLUMN and preserves existing data.
- EXT-024: Attempted schema downgrade raises DowngradeNotSupportedError.
"""

import sqlite3
from pathlib import Path
import pytest

from peerhub.m2.host import (
    ExtensionHost,
    RegistrationConflictError,
    DowngradeNotSupportedError,
)
from peerhub.m2.manifest import ExtensionManifest


def test_ext_013_hook_exception_trapped_safely(tmp_path):
    """EXT-013: Hook runtime exception is caught by host, Core continues, extension becomes FAILED."""
    db_path = tmp_path / "hooks.db"
    host = ExtensionHost(db_path)

    manifest = ExtensionManifest(id="ext_buggy", version="1.0.0", entrypoint="mod.py")
    ext_dir = tmp_path / "ext_buggy"
    ext_dir.mkdir()
    (ext_dir / "mod.py").write_text("")
    host.register_manifest(manifest, ext_dir)
    host.enable("ext_buggy")
    assert host.get_state("ext_buggy") == "ENABLED"

    def faulty_hook(payload):
        raise RuntimeError("Crash inside extension hook")

    host.register_hook("ext_buggy", "on_record_saved", faulty_hook)

    # Core dispatches event: must not crash!
    host.dispatch_event("on_record_saved", {"record_id": "r1"})

    # Extension state must have transitioned to FAILED
    assert host.get_state("ext_buggy") == "FAILED"


def test_ext_015_duplicate_manifest_registration_conflict(tmp_path):
    """EXT-015: Attempt to register conflicting manifest with same ID raises RegistrationConflictError."""
    db_path = tmp_path / "conflict.db"
    host = ExtensionHost(db_path)

    m1 = ExtensionManifest(id="ext_chat", version="1.0.0", entrypoint="v1.py")
    d1 = tmp_path / "chat1"
    d1.mkdir()
    host.register_manifest(m1, d1)

    m2 = ExtensionManifest(id="ext_chat", version="2.0.0", entrypoint="v2.py")
    d2 = tmp_path / "chat2"
    d2.mkdir()

    with pytest.raises(RegistrationConflictError):
        host.register_manifest(m2, d2)

    # Original manifest remains registered
    assert host.manifests["ext_chat"].version == "1.0.0"


def test_ext_016_rebuild_clears_memory_cache(tmp_path):
    """EXT-016: Rebuilding registry clears memory cache and preserves DB state."""
    db_path = tmp_path / "rebuild.db"
    ext_dir = tmp_path / "ext_cached"
    ext_dir.mkdir()
    (ext_dir / "app.py").write_text("INITIALIZED = True\n")

    host = ExtensionHost(db_path)
    manifest = ExtensionManifest(id="ext_cached", version="1.0.0", entrypoint="app.py")
    host.register_manifest(manifest, ext_dir)
    host.enable("ext_cached")
    assert "ext_cached" in host.loaded_modules

    # Trigger rebuild
    host.rebuild_registry()

    assert "ext_cached" not in host.loaded_modules
    assert len(host.manifests) == 0

    # DB state remains intact
    assert host.get_state("ext_cached") == "ENABLED"


def test_ext_020_migration_add_column_preserves_data(tmp_path):
    """EXT-020: Schema migration with ADD COLUMN executes cleanly and preserves existing rows."""
    db_path = tmp_path / "migration_v2.db"
    host = ExtensionHost(db_path)

    # V1 Schema
    v1_sql = "CREATE TABLE ext_profiles (id TEXT PRIMARY KEY, username TEXT);"
    host.apply_extension_schema("ext_profiles", v1_sql)

    with sqlite3.connect(db_path) as conn:
        conn.execute("INSERT INTO ext_profiles (id, username) VALUES ('u1', 'alice');")
        conn.commit()

    # V2 Schema: ADD COLUMN
    v2_sql = "ALTER TABLE ext_profiles ADD COLUMN email TEXT DEFAULT 'none@example.com';"
    host.apply_extension_schema("ext_profiles", v2_sql, new_schema_version=2)

    # Verify column added and existing data preserved
    with sqlite3.connect(db_path) as conn:
        cursor = conn.execute("SELECT id, username, email FROM ext_profiles WHERE id = 'u1'")
        row = cursor.fetchone()

    assert row == ("u1", "alice", "none@example.com")


def test_ext_024_schema_downgrade_forbidden(tmp_path):
    """EXT-024: Attempting to register an extension with a lower schema version than DB raises DowngradeNotSupportedError."""
    db_path = tmp_path / "downgrade.db"
    host = ExtensionHost(db_path)

    # Manifest with schema_version in DB set to 3
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO m2_extension_registry (id, version, entrypoint, state, schema_version)
            VALUES ('ext_v3', '3.0.0', 'main.py', 'ENABLED', 3)
            """
        )

    # Downgraded manifest asserting schema version 1
    downgraded_manifest = ExtensionManifest(
        id="ext_v3",
        version="1.0.0",
        entrypoint="main.py",
    )
    ext_dir = tmp_path / "ext_v3"
    ext_dir.mkdir()

    with pytest.raises(DowngradeNotSupportedError):
        host.register_manifest(downgraded_manifest, ext_dir, target_schema_version=1)
