"""Tests for PeerHub smart lifecycle parity features (v0.10.0):
- ALLOWED_CONFIG_FILES expansion (models.toml, dispatch-policy.toml, routing.toml)
- peerhub backup global
- peerhub backup restore --apply (Dry-run by default)
- peerhub workspace reset [--apply] (2PC Fail-Closed snapshot & clean sweep)
"""

from __future__ import annotations

import json
from pathlib import Path
import pytest
import sqlite3

from peerhub.application.backup import (
    ALLOWED_CONFIG_FILES,
    create_workspace_backup,
    create_global_backup,
    restore_workspace_backup,
    reset_workspace,
    BackupBundleError,
    _MANIFEST_NAME,
)
from peerhub.core.context import PathLayout
from peerhub.persistence.sqlite import SqliteStateStore


def test_allowed_config_files_includes_latest_specs() -> None:
    """Verify models.toml, dispatch-policy.toml, routing.toml are in ALLOWED_CONFIG_FILES."""
    required = {"ask.toml", "arbiter.json", "proposals.json", "models.toml", "dispatch-policy.toml", "routing.toml"}
    assert required.issubset(ALLOWED_CONFIG_FILES), f"Missing required configs: {required - ALLOWED_CONFIG_FILES}"


def test_create_workspace_backup_bundles_new_config_files(tmp_path: Path) -> None:
    """Workspace backup bundles models.toml, dispatch-policy.toml without exclusion."""
    ws = tmp_path / "ws"
    ws.mkdir()
    layout = PathLayout.for_workspace(ws)
    layout.database_path.parent.mkdir(parents=True)
    store = SqliteStateStore(layout.database_path, workspace_home_id="test-ws")
    store.initialize()

    cfg = layout.workspace_config_home
    cfg.mkdir(parents=True)
    (cfg / "models.toml").write_text("[models]\ndefault='claude'", encoding="utf-8")
    (cfg / "dispatch-policy.toml").write_text("[policy]\nmode='auto'", encoding="utf-8")
    (cfg / "custom_unregistered.txt").write_text("extra", encoding="utf-8")

    out = tmp_path / "out"
    bundle = create_workspace_backup(
        ws,
        output_dir=out,
        include_transcripts=False,
        now="2026-09-30T12:00:00Z",
    )

    manifest_data = json.loads((bundle / _MANIFEST_NAME).read_text(encoding="utf-8"))
    bundled_configs = manifest_data.get("config_files", [])
    assert "models.toml" in bundled_configs
    assert "dispatch-policy.toml" in bundled_configs
    assert (bundle / "config" / "models.toml").is_file()
    assert (bundle / "config" / "dispatch-policy.toml").is_file()


def test_create_global_backup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """peerhub backup global bundles global config home."""
    global_home = tmp_path / "global_peerhub" / "config"
    global_home.mkdir(parents=True)
    (global_home / "models.toml").write_text("global_model = true", encoding="utf-8")
    (global_home / "ask.toml").write_text("global_ask = true", encoding="utf-8")

    monkeypatch.setenv("PEERHUB_CONFIG_HOME", str(global_home))

    out = tmp_path / "backups"
    bundle = create_global_backup(output_dir=out, now="2026-09-30T12:00:00Z")

    assert bundle.is_dir()
    manifest_data = json.loads((bundle / _MANIFEST_NAME).read_text(encoding="utf-8"))
    assert manifest_data.get("is_global_backup") is True
    assert (bundle / "models.toml").is_file()
    assert (bundle / "ask.toml").is_file()


def test_restore_workspace_backup_dry_run_leaves_filesystem_untouched(tmp_path: Path) -> None:
    """restore_workspace_backup with apply=False returns plan and touches nothing."""
    ws = tmp_path / "ws"
    ws.mkdir()
    layout = PathLayout.for_workspace(ws)
    layout.database_path.parent.mkdir(parents=True)
    store = SqliteStateStore(layout.database_path, workspace_home_id="test-ws")
    store.initialize()

    out = tmp_path / "out"
    bundle = create_workspace_backup(
        ws,
        output_dir=out,
        include_transcripts=False,
        now="2026-09-30T12:00:00Z",
    )

    # Modify live DB
    with sqlite3.connect(layout.database_path) as conn:
        conn.execute("CREATE TABLE live_marker (x INT)")

    # Dry-run restore
    plan = restore_workspace_backup(bundle, workspace_root=ws, apply=False)
    assert plan.dry_run is True
    assert plan.target_database == layout.database_path

    # Verify live DB is untouched (marker still exists)
    with sqlite3.connect(layout.database_path) as conn:
        cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='live_marker'")
        assert cursor.fetchone() is not None


def test_reset_workspace_dry_run_and_apply(tmp_path: Path) -> None:
    """reset_workspace requires apply=True to purge .peerhub, creates safety snapshot."""
    ws = tmp_path / "ws"
    ws.mkdir()
    layout = PathLayout.for_workspace(ws)
    layout.database_path.parent.mkdir(parents=True)
    store = SqliteStateStore(layout.database_path, workspace_home_id="test-ws")
    store.initialize()
    (layout.workspace_home / "marker.txt").write_text("hello", encoding="utf-8")

    # Dry-run
    plan = reset_workspace(ws, apply=False)
    assert plan.dry_run is True
    assert layout.workspace_home.exists()

    # Apply
    res = reset_workspace(ws, apply=True)
    assert res.dry_run is False
    assert not layout.workspace_home.exists()
    assert res.snapshot_path is not None
    assert res.snapshot_path.exists()
