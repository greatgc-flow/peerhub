"""Item 10 (dotdir consolidation, ratified 2026-09-09): workspace backup
and restore via SQLite's online-backup API."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from peerhub.application.backup import (
    ALLOWED_CONFIG_FILES,
    BackupBundleError,
    create_workspace_backup,
    load_manifest,
    recover_workspace_restore,
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


def _dispatch_response_text(database_path: Path) -> str | None:
    with sqlite3.connect(database_path) as conn:
        row = conn.execute("SELECT transcript_text FROM dispatch_transcripts LIMIT 1").fetchone()
    return row[0] if row else None


def _identity_and_epoch(database_path: Path) -> tuple[str, int]:
    with sqlite3.connect(database_path) as conn:
        row = conn.execute(
            "SELECT workspace_home_id, activation_epoch FROM workspace_identity WHERE singleton = 1"
        ).fetchone()
    return (row[0], row[1])


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
    # A fresh workspace mints its own opaque identity (R2 section 4.3) --
    # not derived from a directory basename or any caller-supplied literal.
    assert manifest.workspace_home_id
    assert manifest.workspace_home_id != "cli"
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
    source_identity = load_manifest(bundle_dir).workspace_home_id

    target_root = tmp_path / "target"
    target_root.mkdir()
    target_db = PathLayout.for_workspace(target_root).database_path
    SqliteStateStore(target_db, workspace_home_id=source_identity).initialize()
    # A fresh store mints its own opaque identity regardless of the
    # constructor argument (R2 section 4.3) -- force this target to match
    # the source's real minted identity, simulating "this target already
    # shares the source's identity" for the restore-activation scenario
    # this test exercises (a low-level test-only technique; production
    # code never rewrites an existing identity).
    with sqlite3.connect(target_db) as conn:
        conn.execute(
            "UPDATE workspace_identity SET workspace_home_id = ? WHERE singleton = 1",
            (source_identity,),
        )
        conn.commit()

    manifest = restore_workspace_backup(bundle_dir, workspace_root=target_root)

    assert manifest.workspace_home_id == source_identity
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
    # A fresh store mints its own opaque identity regardless of the
    # constructor argument (R2 section 4.3); it is independent from the
    # source's own minted identity, so the mismatch this test exercises
    # occurs naturally -- capture the target's real identity to confirm
    # it is left untouched by the rejected restore.
    SqliteStateStore(target_db, workspace_home_id="a-different-workspace").initialize()
    with sqlite3.connect(target_db) as conn:
        target_identity_before = conn.execute(
            "SELECT workspace_home_id FROM workspace_identity WHERE singleton = 1"
        ).fetchone()[0]

    with pytest.raises(WorkspaceIdentityMismatchError):
        restore_workspace_backup(bundle_dir, workspace_root=target_root)

    # Activation never happened: the target's own database is untouched.
    assert _transcript_count(target_db) == 0
    with sqlite3.connect(target_db) as conn:
        row = conn.execute(
            "SELECT workspace_home_id FROM workspace_identity WHERE singleton = 1"
        ).fetchone()
    assert row[0] == target_identity_before


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


# ── R3 (section 6.3) regression tests ─────────────────────────────────────


def test_create_backup_excludes_unlisted_config_files_and_reports_them(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    _seed_workspace_with_dispatch(workspace_root, monkeypatch)
    config_dir = workspace_root / ".peerhub" / "config"
    config_dir.mkdir(parents=True)
    (config_dir / "arbiter.json").write_text("{}", encoding="utf-8")
    (config_dir / "not-on-the-allowlist.txt").write_text("secret", encoding="utf-8")

    bundle_dir = create_workspace_backup(
        workspace_root, output_dir=tmp_path / "backups", include_transcripts=False, now=_NOW
    )

    manifest = load_manifest(bundle_dir)
    assert manifest.config_files == ("arbiter.json",)
    assert manifest.excluded_config_files == ("not-on-the-allowlist.txt",)
    assert not (bundle_dir / "config" / "not-on-the-allowlist.txt").exists()
    assert (bundle_dir / "config" / "arbiter.json").exists()


def test_load_manifest_rejects_path_traversal_in_config_files(tmp_path: Path) -> None:
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    (bundle_dir / "MANIFEST.json").write_text(
        json.dumps(
            {
                "backup_schema_version": 1,
                "workspace_home_id": "cli",
                "created_at": _NOW,
                "include_transcripts": False,
                "config_files": ["../../evil.toml"],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(BackupBundleError, match="bare filename"):
        load_manifest(bundle_dir)


def test_load_manifest_rejects_absolute_path_in_config_files(tmp_path: Path) -> None:
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    absolute = str(tmp_path / "evil.toml")
    (bundle_dir / "MANIFEST.json").write_text(
        json.dumps(
            {
                "backup_schema_version": 1,
                "workspace_home_id": "cli",
                "created_at": _NOW,
                "include_transcripts": False,
                "config_files": [absolute],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(BackupBundleError, match="bare filename"):
        load_manifest(bundle_dir)


def test_load_manifest_rejects_unknown_config_filename(tmp_path: Path) -> None:
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    (bundle_dir / "MANIFEST.json").write_text(
        json.dumps(
            {
                "backup_schema_version": 1,
                "workspace_home_id": "cli",
                "created_at": _NOW,
                "include_transcripts": False,
                "config_files": ["not-a-known-config-file.json"],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(BackupBundleError, match="unknown config file name"):
        load_manifest(bundle_dir)


def test_restore_rejects_symlinked_bundle_config_file(tmp_path: Path) -> None:
    bundle_dir = tmp_path / "bundle"
    (bundle_dir / "config").mkdir(parents=True)
    _write_minimal_bundle_db(bundle_dir)
    (bundle_dir / "MANIFEST.json").write_text(
        json.dumps(
            {
                "backup_schema_version": 1,
                "workspace_home_id": "cli",
                "created_at": _NOW,
                "include_transcripts": False,
                "config_files": ["arbiter.json"],
            }
        ),
        encoding="utf-8",
    )
    real_secret = tmp_path / "real-secret.json"
    real_secret.write_text("{}", encoding="utf-8")
    try:
        (bundle_dir / "config" / "arbiter.json").symlink_to(real_secret)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation not permitted on this host")

    with pytest.raises(Exception):
        restore_workspace_backup(bundle_dir, workspace_root=tmp_path / "target")


def _write_minimal_bundle_db(bundle_dir: Path) -> None:
    """Initialize a real, schema-valid database at bundle_dir/peerhub.sqlite3
    for tests that need a structurally valid bundle without a full seeded
    workspace."""

    db_path = bundle_dir / "peerhub.sqlite3"
    SqliteStateStore(db_path, workspace_home_id="cli").initialize()


def test_restore_removes_stale_target_config_file_absent_from_bundle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_root = tmp_path / "source"
    source_root.mkdir()
    _seed_workspace_with_dispatch(source_root, monkeypatch)
    # Source has only arbiter.json.
    _write_arbiter_config(source_root)

    bundle_dir = create_workspace_backup(
        source_root, output_dir=tmp_path / "backups", include_transcripts=False, now=_NOW
    )
    source_identity = load_manifest(bundle_dir).workspace_home_id

    target_root = tmp_path / "target"
    target_root.mkdir()
    target_db = PathLayout.for_workspace(target_root).database_path
    SqliteStateStore(target_db, workspace_home_id=source_identity).initialize()
    with sqlite3.connect(target_db) as conn:
        conn.execute(
            "UPDATE workspace_identity SET workspace_home_id = ? WHERE singleton = 1",
            (source_identity,),
        )
        conn.commit()
    # The target ALSO has a proposals.json that the bundle does not carry.
    target_config = target_root / ".peerhub" / "config"
    target_config.mkdir(parents=True, exist_ok=True)
    (target_config / "proposals.json").write_text("{}", encoding="utf-8")

    restore_workspace_backup(bundle_dir, workspace_root=target_root)

    assert (target_config / "arbiter.json").exists()
    assert not (target_config / "proposals.json").exists()


def test_restore_into_fresh_target_is_independent_clone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_root = tmp_path / "source"
    source_root.mkdir()
    _seed_workspace_with_dispatch(source_root, monkeypatch)
    bundle_dir = create_workspace_backup(
        source_root, output_dir=tmp_path / "backups", include_transcripts=False, now=_NOW
    )
    source_identity = load_manifest(bundle_dir).workspace_home_id

    target_root = tmp_path / "fresh-target"
    target_root.mkdir()
    # No pre-existing database at all: an independent clone, not a
    # same-identity restore -- must not require the bundle's identity to
    # already be present anywhere.
    assert not PathLayout.for_workspace(target_root).database_path.exists()

    restore_workspace_backup(bundle_dir, workspace_root=target_root)

    target_db = PathLayout.for_workspace(target_root).database_path
    with sqlite3.connect(target_db) as conn:
        row = conn.execute(
            "SELECT workspace_home_id, activation_epoch FROM workspace_identity WHERE singleton = 1"
        ).fetchone()
    # A freshly-minted identity, not the source's -- and not the target's
    # directory basename either.
    assert row[0] != source_identity
    assert row[0] != "fresh-target"
    assert row[1] == 2  # initialize() mints epoch 1; the clone mint bumps it to 2.


def _inject_prompt_into_params_json(workspace_root: Path, prompt: str) -> None:
    """Simulate a command type whose params_json legitimately carries a raw
    prompt (e.g. the legacy dispatch.submit* commands in
    peerhub.application.commands.dispatch), since neither of the CURRENT
    peer.ask paths (execute_direct_ask, BroadcastCoordinator) actually
    persist the prompt itself into dispatch_requests.params_json -- only a
    digest (broadcast) or nothing at all (direct ask). The redaction logic
    in backup.py is still real defense-in-depth for any params_json that
    does carry one, so it is exercised directly here rather than via a
    seed helper that can't produce that shape today."""

    database_path = PathLayout.for_workspace(workspace_root).database_path
    with sqlite3.connect(database_path) as conn:
        command_id, params_json = conn.execute(
            "SELECT command_id, params_json FROM dispatch_requests LIMIT 1"
        ).fetchone()
        params = json.loads(params_json)
        params["prompt"] = prompt
        conn.execute(
            "UPDATE dispatch_requests SET params_json = ? WHERE command_id = ?",
            (json.dumps(params), command_id),
        )
        conn.commit()


def test_create_backup_redacts_prompt_in_params_json_when_transcripts_excluded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    _seed_workspace_with_dispatch(workspace_root, monkeypatch, response="secret response")
    _inject_prompt_into_params_json(workspace_root, "hello")

    bundle_dir = create_workspace_backup(
        workspace_root, output_dir=tmp_path / "backups", include_transcripts=False, now=_NOW
    )

    with sqlite3.connect(bundle_dir / "peerhub.sqlite3") as conn:
        rows = conn.execute("SELECT params_json FROM dispatch_requests").fetchall()
    assert rows
    found_redacted = False
    for (params_json,) in rows:
        payload = json.loads(params_json)
        if "prompt" in payload:
            assert payload["prompt"] == "<redacted: include_transcripts=False>"
            found_redacted = True
    assert found_redacted


def test_create_backup_keeps_prompt_in_params_json_when_transcripts_included(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    _seed_workspace_with_dispatch(workspace_root, monkeypatch)
    _inject_prompt_into_params_json(workspace_root, "hello")

    bundle_dir = create_workspace_backup(
        workspace_root, output_dir=tmp_path / "backups", include_transcripts=True, now=_NOW
    )

    with sqlite3.connect(bundle_dir / "peerhub.sqlite3") as conn:
        rows = conn.execute("SELECT params_json FROM dispatch_requests").fetchall()
    found_prompt = False
    for (params_json,) in rows:
        payload = json.loads(params_json)
        if payload.get("prompt") == "hello":
            found_prompt = True
    assert found_prompt


def test_restore_rolls_back_on_injected_activation_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_root = tmp_path / "source"
    source_root.mkdir()
    _seed_workspace_with_dispatch(source_root, monkeypatch, response="new generation")
    bundle_dir = create_workspace_backup(
        source_root, output_dir=tmp_path / "backups", include_transcripts=True, now=_NOW
    )
    source_identity = load_manifest(bundle_dir).workspace_home_id

    target_root = tmp_path / "target"
    target_root.mkdir()
    target_db = PathLayout.for_workspace(target_root).database_path
    SqliteStateStore(target_db, workspace_home_id=source_identity).initialize()
    with sqlite3.connect(target_db) as conn:
        conn.execute(
            "UPDATE workspace_identity SET workspace_home_id = ? WHERE singleton = 1",
            (source_identity,),
        )
        conn.commit()
    original_identity_epoch = _identity_and_epoch(target_db)

    import peerhub.application.backup as backup_module

    real_copy = backup_module._sqlite_backup_copy

    def _failing_copy(source: Path, dest: Path) -> None:
        # Fail only the specific copy that activates the staged DB into
        # place at the target (identified by its source: the staging
        # directory, not the prior-generation backup) -- both that copy
        # AND the rollback's own restoration copy target target_db, so
        # matching on `dest == target_db` alone would also break the
        # rollback itself and leave the journal stuck at "activating"
        # instead of reaching "rolled_back".
        if dest == target_db and source.parent.name == "restore-staging":
            raise RuntimeError("injected failure during activation")
        real_copy(source, dest)

    monkeypatch.setattr(backup_module, "_sqlite_backup_copy", _failing_copy)

    with pytest.raises(RuntimeError, match="injected failure"):
        restore_workspace_backup(bundle_dir, workspace_root=target_root)

    # Rolled back in-process: the target's original generation is intact.
    # (Content, not raw bytes: the online-backup API used by both the
    # prior-generation capture and its restoration legitimately bumps the
    # SQLite header's file-change-counter on each pass, so byte-for-byte
    # equality with a pre-backup snapshot is not a property it preserves.)
    assert _identity_and_epoch(target_db) == original_identity_epoch
    # And the journal reflects a clean, terminal rollback -- a subsequent
    # operation must not be blocked.
    journal_path = target_db.parent / "restore-journal.json"
    journal = json.loads(journal_path.read_text(encoding="utf-8"))
    assert journal["phase"] == "rolled_back"


def test_recover_workspace_restore_resolves_stuck_journal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Simulates a hard process kill: the journal is left in a non-terminal
    phase with a real prior-generation backup on disk, as if the process
    died between writing phase="activating" and reaching "completed"."""

    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    _seed_workspace_with_dispatch(workspace_root, monkeypatch, response="original")
    db_path = PathLayout.for_workspace(workspace_root).database_path
    original_response = _dispatch_response_text(db_path)
    assert original_response == "original"

    home = db_path.parent
    prior_dir = tmp_path / "manual-prior-generation"
    prior_dir.mkdir()
    import shutil as _shutil

    _shutil.copy2(db_path, prior_dir / "peerhub.sqlite3")
    journal_path = home / "restore-journal.json"
    journal_path.write_text(
        json.dumps(
            {
                "phase": "activating",
                "bundle_dir": str(tmp_path / "irrelevant-bundle"),
                "workspace_root": str(workspace_root),
                "prior_generation_dir": str(prior_dir),
            }
        ),
        encoding="utf-8",
    )
    # Simulate the interrupted activation itself having already overwritten
    # the live DB with corrupt/partial bytes.
    db_path.write_bytes(b"not a valid sqlite file, simulating a torn write")

    # Any normal access must now be refused (recovery-required).
    from peerhub.persistence.maintenance import WorkspaceMaintenanceError

    with pytest.raises(WorkspaceMaintenanceError):
        SqliteStateStore(db_path, workspace_home_id="workspace").initialize()

    result = recover_workspace_restore(workspace_root)
    assert result == "rolled_back"
    # Content, not raw bytes: see the comment in
    # test_restore_rolls_back_on_injected_activation_failure.
    assert _dispatch_response_text(db_path) == original_response

    # Now normal access succeeds again.
    SqliteStateStore(db_path, workspace_home_id="workspace").initialize()


def test_recover_workspace_restore_is_a_noop_when_nothing_is_stuck(
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    assert recover_workspace_restore(workspace_root) == "no_journal"


def test_restore_refuses_when_target_not_quiescent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_root = tmp_path / "source"
    source_root.mkdir()
    _seed_workspace_with_dispatch(source_root, monkeypatch)
    bundle_dir = create_workspace_backup(
        source_root, output_dir=tmp_path / "backups", include_transcripts=False, now=_NOW
    )
    source_identity = load_manifest(bundle_dir).workspace_home_id

    target_root = tmp_path / "target"
    target_root.mkdir()
    target_db = PathLayout.for_workspace(target_root).database_path
    SqliteStateStore(target_db, workspace_home_id=source_identity).initialize()
    with sqlite3.connect(target_db) as conn:
        conn.execute(
            "UPDATE workspace_identity SET workspace_home_id = ? WHERE singleton = 1",
            (source_identity,),
        )
        # Leave a lease in a live (non-terminal) state.
        conn.execute(
            """INSERT INTO leases (lease_id, session_id, command_id, state,
                fencing_token, authority_epoch, revision, owner_instance_id,
                owner_principal_id, heartbeat_expires_at, created_at, updated_at)
               VALUES ('L1', 'S1', 'cmd-1', 'RESERVED', 1, 1, 1, 'inst', 'prin', 0, 0, 0)"""
        )
        conn.commit()

    from peerhub.persistence.maintenance import WorkspaceMaintenanceError

    with pytest.raises(WorkspaceMaintenanceError, match="not quiescent"):
        restore_workspace_backup(bundle_dir, workspace_root=target_root)

    # Refused before touching anything.
    assert not (target_db.parent / "restore-journal.json").exists()
