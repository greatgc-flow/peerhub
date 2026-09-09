"""Item 10 (dotdir consolidation, ratified 2026-09-09): workspace backup
and restore via SQLite's online-backup API."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from peerhub.application.backup import (
    BackupBundleError,
    create_workspace_backup,
    load_manifest,
    restore_workspace_backup,
)
from peerhub.application.direct_ask import DirectAskRequest, execute_direct_ask
from peerhub.core.context import PathLayout
from peerhub.core.errors import WorkspaceIdentityMismatchError
from peerhub.core.execution import TransportLimits
from peerhub.core.identity import AuthenticatedSubject
from peerhub.dispatch.capability import CapabilityTier
from peerhub.persistence.sqlite import SqliteStateStore
from tests.integration.application.test_direct_ask import (
    DummyClock,
    DummyIds,
    _continuity_target,
    _patch_direct_ask,
)

_NOW = "2026-09-10T00:00:00+00:00"


def _seed_workspace_with_dispatch(
    workspace_root: Path, monkeypatch: pytest.MonkeyPatch, response: str = "backup fixture response"
) -> None:
    """Populate workspace_root with a real, FK-respecting dispatch_transcripts
    row via the actual ask pipeline (hand-crafting one directly would need to
    satisfy dispatch_requests/leases/dispatch_attempts' full CHECK/FK chain).
    execute_direct_ask() always records workspace_home_id="cli"."""

    adapter, target = _continuity_target(response)
    _patch_direct_ask(monkeypatch, target)
    request = DirectAskRequest(
        workspace_root=workspace_root,
        peer_name="fake",
        prompt="hello",
        required_capability_tier=CapabilityTier.READ_ONLY,
        profile_id=target.profile.profile_id,
        limits=TransportLimits(
            process_timeout_ms=10_000,
            silence_timeout_ms=10_000,
            max_output_bytes=1_000_000,
        ),
    )
    execute_direct_ask(
        request,
        clock=DummyClock(),
        ids=DummyIds(),
        authenticated_subject=AuthenticatedSubject("local-cli:test-user", "test"),
    )


def _transcript_count(database_path: Path) -> int:
    with sqlite3.connect(database_path) as conn:
        return conn.execute("SELECT COUNT(*) FROM dispatch_transcripts").fetchone()[0]


def _write_arbiter_config(workspace_root: Path) -> None:
    config_dir = workspace_root / ".peerhub" / "config"
    config_dir.mkdir(parents=True)
    (config_dir / "arbiter.json").write_text(
        json.dumps({"schema_version": 1, "enabled": True}), encoding="utf-8"
    )


def test_create_backup_bundles_database_and_config_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    _seed_workspace_with_dispatch(workspace_root, monkeypatch)
    _write_arbiter_config(workspace_root)

    bundle_dir = create_workspace_backup(
        workspace_root,
        output_dir=tmp_path / "backups",
        include_transcripts=False,
        now=_NOW,
    )

    assert (bundle_dir / "peerhub.sqlite3").is_file()
    assert (bundle_dir / "MANIFEST.json").is_file()
    assert (bundle_dir / "config" / "arbiter.json").is_file()

    manifest = load_manifest(bundle_dir)
    assert manifest.workspace_home_id == "cli"
    assert manifest.include_transcripts is False
    assert manifest.config_files == ("arbiter.json",)


def test_create_backup_excludes_transcripts_by_default_and_leaves_source_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    _seed_workspace_with_dispatch(workspace_root, monkeypatch)
    source_db = PathLayout.for_workspace(workspace_root).database_path
    assert _transcript_count(source_db) == 1

    bundle_dir = create_workspace_backup(
        workspace_root,
        output_dir=tmp_path / "backups",
        include_transcripts=False,
        now=_NOW,
    )

    assert _transcript_count(bundle_dir / "peerhub.sqlite3") == 0
    # The live source database is never mutated by taking a backup.
    assert _transcript_count(source_db) == 1


def test_create_backup_includes_transcripts_when_requested(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    _seed_workspace_with_dispatch(workspace_root, monkeypatch, response="keep me")

    bundle_dir = create_workspace_backup(
        workspace_root,
        output_dir=tmp_path / "backups",
        include_transcripts=True,
        now=_NOW,
    )

    with sqlite3.connect(bundle_dir / "peerhub.sqlite3") as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT transcript_text FROM dispatch_transcripts").fetchone()
    assert row is not None
    assert row["transcript_text"] == "keep me"


def test_create_backup_refuses_to_overwrite_an_existing_bundle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    _seed_workspace_with_dispatch(workspace_root, monkeypatch)
    output_dir = tmp_path / "backups"

    create_workspace_backup(
        workspace_root, output_dir=output_dir, include_transcripts=False, now=_NOW
    )

    with pytest.raises(FileExistsError):
        create_workspace_backup(
            workspace_root, output_dir=output_dir, include_transcripts=False, now=_NOW
        )


def test_load_manifest_missing_raises(tmp_path: Path) -> None:
    with pytest.raises(BackupBundleError, match="MANIFEST.json"):
        load_manifest(tmp_path / "not-a-bundle")


def test_load_manifest_rejects_unsupported_schema_version(tmp_path: Path) -> None:
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    (bundle_dir / "MANIFEST.json").write_text(
        json.dumps({"backup_schema_version": 99}), encoding="utf-8"
    )

    with pytest.raises(BackupBundleError, match="backup_schema_version"):
        load_manifest(bundle_dir)


def test_restore_into_matching_identity_workspace_activates_database_and_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_root = tmp_path / "source"
    source_root.mkdir()
    _seed_workspace_with_dispatch(source_root, monkeypatch, response="restored text")
    _write_arbiter_config(source_root)

    bundle_dir = create_workspace_backup(
        source_root, output_dir=tmp_path / "backups", include_transcripts=True, now=_NOW
    )

    target_root = tmp_path / "target"
    target_root.mkdir()
    target_db = PathLayout.for_workspace(target_root).database_path
    SqliteStateStore(target_db, workspace_home_id="cli").initialize()

    manifest = restore_workspace_backup(bundle_dir, workspace_root=target_root)

    assert manifest.workspace_home_id == "cli"
    assert _transcript_count(target_db) == 1
    restored_arbiter = (
        target_root / ".peerhub" / "config" / "arbiter.json"
    ).read_text(encoding="utf-8")
    original_arbiter = (
        source_root / ".peerhub" / "config" / "arbiter.json"
    ).read_text(encoding="utf-8")
    assert restored_arbiter == original_arbiter


def test_restore_rejects_workspace_identity_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_root = tmp_path / "source"
    source_root.mkdir()
    _seed_workspace_with_dispatch(source_root, monkeypatch)

    bundle_dir = create_workspace_backup(
        source_root, output_dir=tmp_path / "backups", include_transcripts=False, now=_NOW
    )

    target_root = tmp_path / "target"
    target_root.mkdir()
    target_db = PathLayout.for_workspace(target_root).database_path
    SqliteStateStore(target_db, workspace_home_id="a-different-workspace").initialize()

    with pytest.raises(WorkspaceIdentityMismatchError):
        restore_workspace_backup(bundle_dir, workspace_root=target_root)

    # Activation never happened: the target's own database is untouched.
    assert _transcript_count(target_db) == 0
    with sqlite3.connect(target_db) as conn:
        row = conn.execute(
            "SELECT workspace_home_id FROM workspace_identity WHERE singleton = 1"
        ).fetchone()
    assert row[0] == "a-different-workspace"


def test_restore_missing_bundle_raises(tmp_path: Path) -> None:
    with pytest.raises(BackupBundleError):
        restore_workspace_backup(
            tmp_path / "absent-bundle", workspace_root=tmp_path / "target"
        )


def test_restore_bundle_missing_database_raises(tmp_path: Path) -> None:
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    (bundle_dir / "MANIFEST.json").write_text(
        json.dumps(
            {
                "backup_schema_version": 1,
                "workspace_home_id": "cli",
                "created_at": _NOW,
                "include_transcripts": False,
                "config_files": [],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(BackupBundleError, match="peerhub.sqlite3"):
        restore_workspace_backup(bundle_dir, workspace_root=tmp_path / "target")
