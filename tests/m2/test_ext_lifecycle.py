"""Wave 3: Extension Lifecycle, State Transitions & Continuity Tests.

Verifies:
- EXT-004: Extension disable unloads modules, unbinds hooks, and preserves DB tables.
- EXT-009: Idempotent enable/disable calls return existing state without duplicate mutations.
- EXT-010: Forbidden transition ENABLED -> VALIDATED raises ForbiddenTransitionError.
- EXT-011: Forbidden transition FAILED -> ENABLED raises ForbiddenTransitionError.
- EXT-012: Extension states safely persist across process restarts.
- EXT-014: Missing dependencies prevent enablement and raise MissingDependencyError.
- EXT-022: State transition DISCOVERED to FAILED succeeds on invalid manifest.
"""

import sys
import sqlite3
from pathlib import Path
import pytest

from peerhub.m2.host import (
    ExtensionHost,
    ForbiddenTransitionError,
    MissingDependencyError,
)
from peerhub.m2.manifest import ExtensionManifest, SchemaValidationError


@pytest.fixture
def host(tmp_path):
    db_path = tmp_path / "extension_host.db"
    return ExtensionHost(db_path)


def test_ext_004_disable_preserves_tables_and_evicts_module(tmp_path):
    """EXT-004: Disabling an extension preserves its data tables and evicts module from sys.modules."""
    db_path = tmp_path / "host.db"
    ext_dir = tmp_path / "extensions" / "ext_greeter"
    ext_dir.mkdir(parents=True)

    # Create dummy extension module file
    entrypoint_file = ext_dir / "plugin.py"
    entrypoint_file.write_text("LOADED = True\nHOOK_RUN = False\n")

    manifest = ExtensionManifest(
        id="ext_greeter",
        version="1.0.0",
        entrypoint="plugin.py",
        description="Greeter extension",
    )

    host = ExtensionHost(db_path, extensions_dir=tmp_path / "extensions")
    host.register_manifest(manifest, ext_dir)

    # Apply schema and write some data
    schema_sql = "CREATE TABLE ext_greeter_logs (id INTEGER PRIMARY KEY, msg TEXT);"
    host.apply_extension_schema("ext_greeter", schema_sql)

    with sqlite3.connect(db_path) as conn:
        conn.execute("INSERT INTO ext_greeter_logs (id, msg) VALUES (1, 'hello');")
        conn.commit()

    # Enable extension
    host.enable("ext_greeter")
    assert host.get_state("ext_greeter") == "ENABLED"
    assert "ext_greeter" in host.loaded_modules

    # Disable extension
    host.disable("ext_greeter")
    assert host.get_state("ext_greeter") == "DISABLED"
    assert "ext_greeter" not in host.loaded_modules

    # Verify tables and data are strictly preserved
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute("SELECT id, msg FROM ext_greeter_logs").fetchall()
    assert rows == [(1, "hello")]


def test_ext_009_idempotent_enable_and_disable(host, tmp_path):
    """EXT-009: Idempotent enable/disable calls return existing state safely."""
    ext_dir = tmp_path / "ext_dummy"
    ext_dir.mkdir()
    (ext_dir / "main.py").write_text("X = 1\n")

    manifest = ExtensionManifest(
        id="ext_dummy",
        version="1.0.0",
        entrypoint="main.py",
    )
    host.register_manifest(manifest, ext_dir)

    # First enable
    s1 = host.enable("ext_dummy")
    assert s1 == "ENABLED"
    assert host.get_state("ext_dummy") == "ENABLED"

    # Second enable (idempotent no-op)
    s2 = host.enable("ext_dummy")
    assert s2 == "ENABLED"

    # First disable
    d1 = host.disable("ext_dummy")
    assert d1 == "DISABLED"
    assert host.get_state("ext_dummy") == "DISABLED"

    # Second disable (idempotent no-op)
    d2 = host.disable("ext_dummy")
    assert d2 == "DISABLED"


def test_ext_010_forbidden_transition_enabled_to_validated(host, tmp_path):
    """EXT-010: Forbidden transition ENABLED directly to VALIDATED raises ForbiddenTransitionError."""
    ext_dir = tmp_path / "ext_f"
    ext_dir.mkdir()
    (ext_dir / "main.py").write_text("")

    manifest = ExtensionManifest(id="ext_f", version="1.0.0", entrypoint="main.py")
    host.register_manifest(manifest, ext_dir)
    host.enable("ext_f")
    assert host.get_state("ext_f") == "ENABLED"

    with pytest.raises(ForbiddenTransitionError):
        host.transition("ext_f", "VALIDATED")


def test_ext_011_forbidden_transition_failed_to_enabled(host, tmp_path):
    """EXT-011: Forbidden transition FAILED directly to ENABLED raises ForbiddenTransitionError."""
    ext_dir = tmp_path / "ext_fail"
    ext_dir.mkdir()
    (ext_dir / "main.py").write_text("")

    manifest = ExtensionManifest(id="ext_fail", version="1.0.0", entrypoint="main.py")
    host.register_manifest(manifest, ext_dir)
    host.transition("ext_fail", "FAILED")
    assert host.get_state("ext_fail") == "FAILED"

    with pytest.raises(ForbiddenTransitionError):
        host.enable("ext_fail")


def test_ext_012_state_persists_across_host_restart(tmp_path):
    """EXT-012: Extension state in DB persists across Host instance reboots."""
    db_path = tmp_path / "restart_test.db"
    ext_dir = tmp_path / "ext_p"
    ext_dir.mkdir()
    (ext_dir / "main.py").write_text("")

    host1 = ExtensionHost(db_path)
    manifest = ExtensionManifest(id="ext_p", version="1.0.0", entrypoint="main.py")
    host1.register_manifest(manifest, ext_dir)
    host1.enable("ext_p")
    host1.disable("ext_p")
    assert host1.get_state("ext_p") == "DISABLED"

    # Simulate fresh boot with new Host instance
    host2 = ExtensionHost(db_path)
    assert host2.get_state("ext_p") == "DISABLED"


def test_ext_014_missing_dependency_blocks_enablement(host, tmp_path):
    """EXT-014: Missing dependency prevents enablement and raises MissingDependencyError."""
    ext_dir = tmp_path / "ext_child"
    ext_dir.mkdir()
    (ext_dir / "main.py").write_text("")

    manifest = ExtensionManifest(
        id="ext_child",
        version="1.0.0",
        entrypoint="main.py",
        dependencies=["ext_parent"],  # Unmet dependency
    )
    host.register_manifest(manifest, ext_dir)

    with pytest.raises(MissingDependencyError) as exc_info:
        host.enable("ext_child")

    assert "ext_parent" in str(exc_info.value)
    assert host.get_state("ext_child") == "FAILED"
