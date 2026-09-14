"""Workspace backup and restore (item 10, dotdir consolidation, ratified
2026-09-09; hardened for R3, section 6.3, 2026-09-14).

The live database is captured with SQLite's online-backup API
(``sqlite3.Connection.backup()``) -- never a plain file copy of a live
database, which could capture a torn write, and never by copying
``-wal``/``-shm`` sidecars independently, since those are only consistent
together with the main file at a checkpoint boundary the backup API
manages internally. The same primitive is used again on restore, when the
bundled database is copied into its final place, for the identical
reason.

Bundled alongside the database are the workspace's ``config/`` tier files
-- but ONLY the named allowlist (``ask.toml``, ``arbiter.json``,
``proposals.json``); any other file present in the config directory is
excluded and reported in the manifest, never silently copied (section
2.4's finding: membership must be a strict named allowlist, not
"whatever is present"). Legacy un-migrated
``.peerhub/{arbiter,proposals}.json`` files are deliberately NOT bundled
-- run ``peerhub config migrate`` first so there is exactly one
authoritative config location to snapshot.

``dispatch_transcripts`` rows are durable and sensitive (raw dispatch
response text). Their inclusion is never implicit: ``include_transcripts``
is a required keyword argument at the call site, and when it is
``False`` the table is emptied. Excluding one table is not a privacy
boundary on its own (section 6.3's own warning), so
``dispatch_requests.params_json``'s ``prompt`` key is redacted the same
way whenever present -- as defense-in-depth for any command type whose
params legitimately carry one (e.g. the legacy ``dispatch.submit*``
commands in ``application.commands.dispatch``), even though neither of
the two CURRENT ``peer.ask`` producers (``execute_direct_ask``,
``BroadcastCoordinator``) persists the raw prompt into params_json today
(direct ask stores none; broadcast stores only a digest). The backup
database is then ``VACUUM``ed so redacted bytes are not merely
unreferenced but actually gone from the file. ``session_context_observations``
and ``readiness_observations`` were audited and contain only numeric/
metadata fields (token counts, evidence states, timestamps) -- no raw
prompt/response content, so they need no redaction.

Restore is journaled and maintenance-locked (section 6.3 points 2-6):

- An EXCLUSIVE ``WorkspaceGuard`` is held for the entire activation, and
  entering it (via ``require_recovered``) refuses outright if a prior
  restore was interrupted before reaching a terminal journal phase.
- The bundle's schema/identity are validated by staging it under the
  workspace's own temp namespace and running it through
  ``SqliteStateStore.initialize()`` before anything at ``workspace_root``
  is touched, exactly as before -- but now ``require_quiescent()`` is
  also checked against the CURRENT target before activation begins
  (an SQLite write lock alone does not establish application
  quiescence), and the restore-journal records staging, prior-generation
  backup, and activation as distinct phases so a crash between any two
  of them leaves the workspace either on the untouched old generation or
  in an explicit, blocking recovery-required state -- never a partial
  mix of old DB and new config or vice versa.
- A target with no existing database is an independent clone: it mints
  its own fresh identity and epoch (``mint_new_identity_and_epoch``)
  rather than being validated against a nonexistent prior identity.
  A target with an existing, matching identity is a same-identity
  restore: its epoch advances (``mint_new_epoch``) and all prior-epoch
  authority is invalidated (``invalidate_restored_authority``) so
  nothing from before the restore can be replayed as authoritative.
  Relocation (same identity at a new path) is not a restore scenario at
  all -- it is handled entirely by ``resolve_workspace()``'s discovery,
  since identity lives in the database content, not the path.
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
from peerhub.persistence.maintenance import WorkspaceGuard, reject_redirected
from peerhub.persistence.restore_authority import (
    invalidate_restored_authority,
    require_quiescent,
)
from peerhub.persistence.sqlite import SqliteStateStore

_MANIFEST_NAME = "MANIFEST.json"
_DATABASE_FILENAME = "peerhub.sqlite3"
_CONFIG_DIRNAME = "config"
_BACKUP_SCHEMA_VERSION = 1
_JOURNAL_NAME = "restore-journal.json"

# Section 2.4: membership must be a strict named allowlist, not "whatever
# is present" (is_file() previously copied any immediate entry).
ALLOWED_CONFIG_FILES = frozenset({"ask.toml", "arbiter.json", "proposals.json"})


class BackupBundleError(ValueError):
    """Raised for a missing, malformed, or unsafe backup bundle."""


@dataclass(frozen=True, slots=True)
class BackupManifest:
    """Metadata describing one backup bundle."""

    backup_schema_version: int
    workspace_home_id: str
    created_at: str
    include_transcripts: bool
    config_files: tuple[str, ...]
    excluded_config_files: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, object]:
        return {
            "backup_schema_version": self.backup_schema_version,
            "workspace_home_id": self.workspace_home_id,
            "created_at": self.created_at,
            "include_transcripts": self.include_transcripts,
            "config_files": list(self.config_files),
            "excluded_config_files": list(self.excluded_config_files),
        }


def _validate_config_filename(name: str) -> None:
    """Reject anything that is not a bare, known-safe filename.

    A strict-equality allowlist already rejects every traversal/absolute-
    path payload implicitly (none of them equal one of the three known
    names), but the dedicated checks below give a specific, testable
    refusal reason rather than relying on that as an accident of the
    allowlist's shape.
    """

    if not name or name in (".", ".."):
        raise BackupBundleError(f"unsafe config file name in bundle: {name!r}")
    candidate = Path(name)
    if candidate.is_absolute() or candidate.name != name:
        raise BackupBundleError(
            f"config file name must be a bare filename, not a path: {name!r}"
        )
    if name not in ALLOWED_CONFIG_FILES:
        raise BackupBundleError(f"unknown config file name in bundle: {name!r}")


def _unlink_retrying(path: Path) -> None:
    """Unlink a just-closed file, retrying briefly on Windows sharing
    violations. sqlite3.Connection.close() releases the handle
    synchronously from Python's point of view, but the OS can still hold
    the file transiently (antivirus/indexer scans of a freshly-written
    file are a common cause) -- a handful of short retries clears this in
    practice without masking a genuine, persistent lock."""

    import gc
    import time

    delay = 0.05
    for attempt in range(8):
        try:
            path.unlink()
            return
        except PermissionError:
            if attempt == 7:
                raise
            # A sqlite3 connection that failed mid-backup() can leave its
            # C-level handle pinned by a lingering Python reference (a
            # cursor, or the exception's own traceback frame) even after
            # .close() returns -- gc.collect() drops it so Windows actually
            # releases the OS-level lock rather than just retrying blind.
            gc.collect()
            time.sleep(delay)
            delay *= 2


def _sqlite_backup_copy(source_path: Path, dest_path: Path) -> None:
    """Copy one SQLite database via the online-backup API.

    If dest_path already exists but is not a valid SQLite database (a torn
    write left over from an interrupted prior copy -- exactly the state
    recover_workspace_restore() restores over), the backup API fails with
    "file is not a database" before a single page is copied, since it
    opens the destination as a real database first. That case is detected
    and retried once against a freshly emptied file. The common case --
    dest_path absent, or already a valid database -- is left untouched
    going in, since deleting it unconditionally can itself fail on Windows
    with a sharing violation if some other handle briefly still holds it.
    """

    source = sqlite3.connect(str(source_path))
    try:
        try:
            dest = sqlite3.connect(str(dest_path))
            try:
                source.backup(dest)
            finally:
                dest.close()
        except sqlite3.DatabaseError:
            _unlink_retrying(dest_path)
            for suffix in ("-wal", "-shm"):
                sidecar = dest_path.with_name(dest_path.name + suffix)
                if sidecar.exists():
                    _unlink_retrying(sidecar)
            dest = sqlite3.connect(str(dest_path))
            try:
                source.backup(dest)
            finally:
                dest.close()
    finally:
        source.close()


def _strip_sensitive_content(database_path: Path) -> None:
    """Remove raw dispatch response/prompt text (section 6.3: "excluding
    one table is not a privacy boundary"). Empties dispatch_transcripts
    and redacts dispatch_requests.params_json's "prompt" key, leaving the
    rest of that JSON payload (correlation/routing metadata) intact."""

    connection = sqlite3.connect(str(database_path))
    try:
        connection.execute("DELETE FROM dispatch_transcripts")
        rows = connection.execute(
            "SELECT command_id, params_json FROM dispatch_requests"
        ).fetchall()
        for command_id, params_json in rows:
            try:
                params: object = json.loads(params_json)
            except (ValueError, TypeError):
                continue
            if isinstance(params, dict) and "prompt" in cast("dict[str, Any]", params):
                redacted = dict(cast("dict[str, Any]", params))
                redacted["prompt"] = "<redacted: include_transcripts=False>"
                connection.execute(
                    "UPDATE dispatch_requests SET params_json = ? WHERE command_id = ?",
                    (json.dumps(redacted), command_id),
                )
        connection.commit()
        connection.execute("VACUUM")
    finally:
        connection.close()


def _copy_config_files(source_dir: Path, dest_dir: Path) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Copy only the named allowlist from source_dir. Returns (copied, excluded)."""

    if not source_dir.is_dir():
        return (), ()
    copied: list[str] = []
    excluded: list[str] = []
    for entry in sorted(source_dir.iterdir()):
        if not entry.is_file():
            continue
        reject_redirected(entry)
        if entry.name in ALLOWED_CONFIG_FILES:
            dest_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(entry, dest_dir / entry.name)
            copied.append(entry.name)
        else:
            excluded.append(entry.name)
    return tuple(copied), tuple(excluded)


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

    Held under a SHARED WorkspaceGuard for the duration of the capture
    (section 6.3 point 3: "capture DB and config coherently under
    maintenance coordination") so a concurrent restore cannot activate a
    new generation mid-backup.
    """

    layout = PathLayout.for_workspace(workspace_root)
    home = layout.database_path.parent
    reject_redirected(home)
    workspace_home_id = detect_workspace_home_id(
        layout.database_path, workspace_root.name
    )

    bundle_name = f"peerhub-backup-{workspace_home_id}-{now.replace(':', '')}"
    bundle_dir = output_dir / bundle_name
    bundle_dir.mkdir(parents=True, exist_ok=False)

    with WorkspaceGuard(home, exclusive=False, readonly=True):
        dest_db_path = bundle_dir / _DATABASE_FILENAME
        _sqlite_backup_copy(layout.database_path, dest_db_path)
        if not include_transcripts:
            _strip_sensitive_content(dest_db_path)

        config_files, excluded_config_files = _copy_config_files(
            resolve_workspace_config_home(workspace_root).path,
            bundle_dir / _CONFIG_DIRNAME,
        )

    manifest = BackupManifest(
        backup_schema_version=_BACKUP_SCHEMA_VERSION,
        workspace_home_id=workspace_home_id,
        created_at=now,
        include_transcripts=include_transcripts,
        config_files=config_files,
        excluded_config_files=excluded_config_files,
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
    config_files = tuple(str(name) for name in raw.get("config_files", ()))
    for name in config_files:
        _validate_config_filename(name)
    return BackupManifest(
        backup_schema_version=_BACKUP_SCHEMA_VERSION,
        workspace_home_id=str(raw["workspace_home_id"]),
        created_at=str(raw["created_at"]),
        include_transcripts=bool(raw["include_transcripts"]),
        config_files=config_files,
        excluded_config_files=tuple(
            str(name) for name in raw.get("excluded_config_files", ())
        ),
    )


def _write_journal(journal_path: Path, **fields: object) -> None:
    journal_path.write_text(json.dumps(fields, indent=2), encoding="utf-8")


def _read_journal(journal_path: Path) -> dict[str, Any] | None:
    if not journal_path.exists():
        return None
    try:
        raw: object = json.loads(journal_path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None
    return cast("dict[str, Any]", raw) if isinstance(raw, dict) else None


def _backup_prior_generation(
    layout: PathLayout, workspace_root: Path, prior_dir: Path
) -> None:
    prior_dir.mkdir(parents=True, exist_ok=True)
    if layout.database_path.exists():
        _sqlite_backup_copy(layout.database_path, prior_dir / _DATABASE_FILENAME)
    config_home = resolve_workspace_config_home(workspace_root).path
    for name in ALLOWED_CONFIG_FILES:
        source = config_home / name
        if source.is_file():
            shutil.copy2(source, prior_dir / name)


def _restore_prior_generation(
    layout: PathLayout, workspace_root: Path, prior_dir: Path
) -> None:
    """Copy every file captured in prior_dir back into place. Used both by
    an in-process rollback and by recover_workspace_restore() after a
    crash left a non-terminal journal behind."""

    prior_db = prior_dir / _DATABASE_FILENAME
    if prior_db.is_file():
        layout.database_path.parent.mkdir(parents=True, exist_ok=True)
        _sqlite_backup_copy(prior_db, layout.database_path)
    elif layout.database_path.exists():
        # The prior generation had no database at all (a fresh target that
        # a since-rolled-back restore had begun activating into).
        layout.database_path.unlink()
    config_home = resolve_workspace_config_home(workspace_root).path
    for name in ALLOWED_CONFIG_FILES:
        prior_file = prior_dir / name
        target_file = config_home / name
        if prior_file.is_file():
            config_home.mkdir(parents=True, exist_ok=True)
            shutil.copy2(prior_file, target_file)
        elif target_file.exists():
            target_file.unlink()


def recover_workspace_restore(workspace_root: Path) -> str:
    """Resolve a workspace stuck in a recovery-required state after a
    restore was interrupted before reaching a terminal journal phase
    (this can only happen if the process was killed outright -- a normal
    exception during restore_workspace_backup() already rolls back
    in-process). Returns the resulting terminal phase.

    Safe to call on a workspace that is NOT stuck: a missing or already-
    terminal journal is a no-op that returns its current/absent state
    without touching anything.
    """

    layout = PathLayout.for_workspace(workspace_root)
    home = layout.database_path.parent
    journal_path = home / _JOURNAL_NAME
    journal = _read_journal(journal_path)
    if journal is None:
        return "no_journal"
    phase = journal.get("phase")
    if phase in ("completed", "rolled_back"):
        return str(phase)

    with WorkspaceGuard(home, exclusive=True, recovery=True):
        prior_dir_str = journal.get("prior_generation_dir")
        if isinstance(prior_dir_str, str) and Path(prior_dir_str).is_dir():
            _restore_prior_generation(layout, workspace_root, Path(prior_dir_str))
            shutil.rmtree(prior_dir_str, ignore_errors=True)
        _write_journal(
            journal_path,
            phase="rolled_back",
            recovered_from_phase=phase,
        )
    return "rolled_back"


def restore_workspace_backup(
    bundle_dir: Path, *, workspace_root: Path
) -> BackupManifest:
    """Restore one backup bundle into ``workspace_root``.

    Raises ``BackupBundleError`` for a malformed/unsafe bundle,
    ``WorkspaceIdentityMismatchError`` for a schema-version or identity
    mismatch, or ``WorkspaceMaintenanceError`` if the target is not
    quiescent or a prior restore left it in a recovery-required state --
    in every case before anything at ``workspace_root`` is touched.
    """

    reject_redirected(bundle_dir)
    manifest = load_manifest(bundle_dir)
    source_db = bundle_dir / _DATABASE_FILENAME
    reject_redirected(source_db)
    if not source_db.is_file():
        raise BackupBundleError(f"{bundle_dir} is missing its {_DATABASE_FILENAME}")

    bundle_config_dir = bundle_dir / _CONFIG_DIRNAME
    if bundle_config_dir.exists():
        reject_redirected(bundle_config_dir)
        for entry in bundle_config_dir.iterdir():
            reject_redirected(entry)
            if entry.is_file():
                _validate_config_filename(entry.name)

    layout = PathLayout.for_workspace(workspace_root)
    home = layout.database_path.parent
    reject_redirected(home)
    is_independent_clone = not layout.database_path.exists()

    with WorkspaceGuard(home, exclusive=True):
        if not is_independent_clone:
            target_identity = detect_workspace_home_id(
                layout.database_path, workspace_root.name
            )
            # A plain connection, not SqliteStateStore._connect(): the
            # exclusive WorkspaceGuard already held for this whole `with`
            # block IS the maintenance exclusion (section 6.3 point 2) --
            # acquiring a second, independent guard on the same home from
            # within the same process would conflict with the first
            # (OS-level file locks are per-handle, not per-process).
            connection = sqlite3.connect(str(layout.database_path))
            connection.row_factory = sqlite3.Row
            try:
                require_quiescent(connection)
            finally:
                connection.close()
        else:
            # No existing target to validate against. The staged database is
            # a full copy of the BUNDLE's own database, which already has
            # the SOURCE workspace's persisted identity -- so the expected
            # identity passed into initialize() below must be read from the
            # bundle itself (never the target's directory name), or the
            # identity check just below would spuriously raise a mismatch
            # between the source's real identity and an unrelated fallback
            # name. mint_new_identity_and_epoch() then replaces it with a
            # genuinely fresh identity for the clone.
            target_identity = detect_workspace_home_id(source_db, workspace_root.name)

        staging_root = resolve_workspace_temp(workspace_root).path / "restore-staging"
        if staging_root.exists():
            shutil.rmtree(staging_root)
        staging_root.mkdir(parents=True)
        journal_path = home / _JOURNAL_NAME
        # Deliberately NOT nested under staging_root: staging_root is
        # unconditionally removed once this function reaches a safe
        # terminal outcome, but prior_dir must survive an in-process
        # rollback failure (or a hard process kill) so
        # recover_workspace_restore() can still find and replay it later.
        prior_dir = (
            resolve_workspace_temp(workspace_root).path
            / "restore-prior-generation"
        )
        if prior_dir.exists():
            shutil.rmtree(prior_dir)

        try:
            staged_db = staging_root / _DATABASE_FILENAME
            _sqlite_backup_copy(source_db, staged_db)

            # Validates schema version and workspace identity before
            # activation: a bundle recorded by a newer build, or
            # belonging to a different workspace identity, raises here --
            # workspace_root is untouched.
            store = SqliteStateStore(staged_db, workspace_home_id=target_identity)
            store.initialize()
            if is_independent_clone:
                store.mint_new_identity_and_epoch()
            else:
                store.mint_new_epoch()

            _write_journal(
                journal_path,
                phase="staging",
                bundle_dir=str(bundle_dir),
                workspace_root=str(workspace_root),
                prior_generation_dir=str(prior_dir),
            )

            _backup_prior_generation(layout, workspace_root, prior_dir)
            _write_journal(
                journal_path,
                phase="backing_up_prior_generation",
                bundle_dir=str(bundle_dir),
                workspace_root=str(workspace_root),
                prior_generation_dir=str(prior_dir),
            )

            _write_journal(
                journal_path,
                phase="activating",
                bundle_dir=str(bundle_dir),
                workspace_root=str(workspace_root),
                prior_generation_dir=str(prior_dir),
            )
            layout.database_path.parent.mkdir(parents=True, exist_ok=True)
            _sqlite_backup_copy(staged_db, layout.database_path)

            # Restore the EXACT intended known config set: a known file
            # absent from the bundle removes any stale target copy rather
            # than leaving an unrelated version silently active. Unknown
            # (non-allowlisted) target content is never touched.
            config_home = resolve_workspace_config_home(workspace_root).path
            for name in ALLOWED_CONFIG_FILES:
                source_file = bundle_config_dir / name
                target_file = config_home / name
                if name in manifest.config_files and source_file.is_file():
                    config_home.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source_file, target_file)
                elif target_file.exists():
                    target_file.unlink()

            # Same reasoning as the quiescence check above: a plain
            # connection, since the outer exclusive guard already covers
            # this whole activation.
            connection = sqlite3.connect(str(layout.database_path))
            connection.row_factory = sqlite3.Row
            try:
                invalidate_restored_authority(connection)
            finally:
                connection.close()

            _write_journal(
                journal_path,
                phase="completed",
                bundle_dir=str(bundle_dir),
                workspace_root=str(workspace_root),
            )
            shutil.rmtree(prior_dir, ignore_errors=True)
        except BaseException:
            if prior_dir.exists():
                # If this rollback itself raises, the journal is left in
                # whatever phase it was last written at (never "rolled_back"),
                # which is NOT a terminal phase -- require_recovered() then
                # correctly blocks all further use until
                # recover_workspace_restore() is run explicitly. prior_dir
                # is deliberately left in place for it to find.
                _restore_prior_generation(layout, workspace_root, prior_dir)
                _write_journal(
                    journal_path,
                    phase="rolled_back",
                    bundle_dir=str(bundle_dir),
                    workspace_root=str(workspace_root),
                )
                shutil.rmtree(prior_dir, ignore_errors=True)
            # If prior_dir was never captured (failure happened before the
            # prior-generation backup step), nothing was activated yet --
            # no journal to reconcile, workspace_root is untouched.
            raise
        finally:
            shutil.rmtree(staging_root, ignore_errors=True)

    return manifest
