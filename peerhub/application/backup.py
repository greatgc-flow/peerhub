"""Workspace backup and restore (item 10, dotdir consolidation, ratified
2026-09-09).

The live database is captured with SQLite's online-backup API
(``sqlite3.Connection.backup()``) -- never a plain file copy of a live
database, which could capture a torn write, and never by copying
``-wal``/``-shm`` sidecars independently, since those are only consistent
together with the main file at a checkpoint boundary the backup API
manages internally. The same primitive is used again on restore, when the
bundled database is copied into its final place, for the identical
reason.

Bundled alongside the database are the workspace's ``config/`` tier files
(``arbiter.json``, ``proposals.json``, ``ask.toml`` -- whatever is
present; the set is read from disk, never hardcoded). Legacy
un-migrated ``.peerhub/{arbiter,proposals}.json`` files are deliberately
NOT bundled -- run ``peerhub config migrate`` first so there is exactly
one authoritative config location to snapshot.

``dispatch_transcripts`` rows are durable and sensitive (raw dispatch
text). Their inclusion is never implicit: ``include_transcripts`` is a
required keyword argument at the call site, and when it is ``False`` the
table is emptied and the backup database is ``VACUUM``ed so the bytes are
not merely unreferenced but actually gone from the file.

Restore validates schema version and workspace identity before
activation by staging the restored database under the workspace's own
OS-temp namespace and running it through ``SqliteStateStore.initialize()``
-- reusing its existing checks (``WorkspaceIdentityMismatchError`` for an
identity mismatch, and its "records migrations this build does not
provide" refusal for a bundle newer than this build) rather than
duplicating them. Only after that succeeds is the staged database
activated in place.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from peerhub.application.config_paths import (
    resolve_workspace_config_home,
    resolve_workspace_temp,
)
from peerhub.application.workspace_identity import detect_workspace_home_id
from peerhub.core.context import PathLayout
from peerhub.persistence.sqlite import SqliteStateStore

_MANIFEST_NAME = "MANIFEST.json"
_DATABASE_FILENAME = "peerhub.sqlite3"
_CONFIG_DIRNAME = "config"
_BACKUP_SCHEMA_VERSION = 1


class BackupBundleError(ValueError):
    """Raised for a missing or malformed backup bundle."""


@dataclass(frozen=True, slots=True)
class BackupManifest:
    """Metadata describing one backup bundle."""

    backup_schema_version: int
    workspace_home_id: str
    created_at: str
    include_transcripts: bool
    config_files: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "backup_schema_version": self.backup_schema_version,
            "workspace_home_id": self.workspace_home_id,
            "created_at": self.created_at,
            "include_transcripts": self.include_transcripts,
            "config_files": list(self.config_files),
        }


def _sqlite_backup_copy(source_path: Path, dest_path: Path) -> None:
    """Copy one SQLite database via the online-backup API."""

    source = sqlite3.connect(str(source_path))
    try:
        dest = sqlite3.connect(str(dest_path))
        try:
            source.backup(dest)
        finally:
            dest.close()
    finally:
        source.close()


def _strip_transcripts(database_path: Path) -> None:
    connection = sqlite3.connect(str(database_path))
    try:
        connection.execute("DELETE FROM dispatch_transcripts")
        connection.commit()
        connection.execute("VACUUM")
    finally:
        connection.close()


def _copy_config_files(source_dir: Path, dest_dir: Path) -> tuple[str, ...]:
    if not source_dir.is_dir():
        return ()
    names: list[str] = []
    for entry in sorted(source_dir.iterdir()):
        if entry.is_file():
            dest_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(entry, dest_dir / entry.name)
            names.append(entry.name)
    return tuple(names)


def create_workspace_backup(
    workspace_root: Path,
    *,
    output_dir: Path,
    include_transcripts: bool,
    now: str,
) -> Path:
    """Create one backup bundle directory under ``output_dir``.

    ``now`` is an ISO-8601 UTC timestamp supplied by the caller (never
    read from the wall clock here) so the result is deterministic and
    testable; it also names the bundle directory.
    """

    layout = PathLayout.for_workspace(workspace_root)
    workspace_home_id = detect_workspace_home_id(
        layout.database_path, workspace_root.name
    )

    bundle_name = f"peerhub-backup-{workspace_home_id}-{now.replace(':', '')}"
    bundle_dir = output_dir / bundle_name
    bundle_dir.mkdir(parents=True, exist_ok=False)

    dest_db_path = bundle_dir / _DATABASE_FILENAME
    _sqlite_backup_copy(layout.database_path, dest_db_path)
    if not include_transcripts:
        _strip_transcripts(dest_db_path)

    config_files = _copy_config_files(
        resolve_workspace_config_home(workspace_root).path,
        bundle_dir / _CONFIG_DIRNAME,
    )

    manifest = BackupManifest(
        backup_schema_version=_BACKUP_SCHEMA_VERSION,
        workspace_home_id=workspace_home_id,
        created_at=now,
        include_transcripts=include_transcripts,
        config_files=config_files,
    )
    (bundle_dir / _MANIFEST_NAME).write_text(
        json.dumps(manifest.as_dict(), indent=2), encoding="utf-8"
    )
    return bundle_dir


def load_manifest(bundle_dir: Path) -> BackupManifest:
    """Load and validate one bundle's manifest, without touching its database."""

    manifest_path = bundle_dir / _MANIFEST_NAME
    if not manifest_path.is_file():
        raise BackupBundleError(
            f"{bundle_dir} is not a peerhub backup bundle (missing {_MANIFEST_NAME})"
        )
    raw_object: object = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(raw_object, dict):
        raise BackupBundleError(f"{manifest_path} must contain a JSON object")
    raw = cast("dict[str, Any]", raw_object)
    if raw.get("backup_schema_version") != _BACKUP_SCHEMA_VERSION:
        raise BackupBundleError(
            f"{manifest_path} has an unsupported backup_schema_version: "
            f"{raw.get('backup_schema_version')!r} (this build supports "
            f"{_BACKUP_SCHEMA_VERSION})"
        )
    return BackupManifest(
        backup_schema_version=_BACKUP_SCHEMA_VERSION,
        workspace_home_id=str(raw["workspace_home_id"]),
        created_at=str(raw["created_at"]),
        include_transcripts=bool(raw["include_transcripts"]),
        config_files=tuple(raw.get("config_files", ())),
    )


def restore_workspace_backup(
    bundle_dir: Path, *, workspace_root: Path
) -> BackupManifest:
    """Restore one backup bundle into ``workspace_root``.

    Raises whatever ``SqliteStateStore.initialize()`` raises for a
    schema-version or workspace-identity mismatch, before anything at
    ``workspace_root`` is touched.
    """

    manifest = load_manifest(bundle_dir)
    source_db = bundle_dir / _DATABASE_FILENAME
    if not source_db.is_file():
        raise BackupBundleError(f"{bundle_dir} is missing its {_DATABASE_FILENAME}")

    layout = PathLayout.for_workspace(workspace_root)
    target_identity = detect_workspace_home_id(
        layout.database_path, workspace_root.name
    )

    staging_root = resolve_workspace_temp(workspace_root).path / "restore-staging"
    if staging_root.exists():
        shutil.rmtree(staging_root)
    staging_root.mkdir(parents=True)
    try:
        staged_db = staging_root / _DATABASE_FILENAME
        _sqlite_backup_copy(source_db, staged_db)

        # Validates schema version and workspace identity before activation:
        # a bundle recorded by a newer build, or belonging to a different
        # workspace identity, raises here -- workspace_root is untouched.
        SqliteStateStore(staged_db, workspace_home_id=target_identity).initialize()

        layout.database_path.parent.mkdir(parents=True, exist_ok=True)
        _sqlite_backup_copy(staged_db, layout.database_path)
    finally:
        shutil.rmtree(staging_root, ignore_errors=True)

    config_home = resolve_workspace_config_home(workspace_root).path
    _copy_config_files(bundle_dir / _CONFIG_DIRNAME, config_home)

    return manifest
