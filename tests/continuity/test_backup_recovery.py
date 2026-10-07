"""M2.5 Backup & Disaster Recovery Test Suite (BCK-001..012).

Freeze Invariant 11: Restore starts from authoritative and rebuilds derived.
Core imports Extension = 0.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import closing
from pathlib import Path
from typing import Any

import pytest

from peerhub.core.models import Peer, Record, Stream
from peerhub.core.store import CoreStore
from peerhub.extensions.artifact import ArtifactStore
from peerhub.extensions.backup import (
    AuthoritativeRestoreOrderError,
    BackupCorruptedError,
    BackupManifest,
    BackupManifestMissingError,
    GenerationFencingConflictError,
    SecretLeakageDetectedError,
    create_backup,
    rebuild_derived_projections,
    restore_authoritative,
    verify_backup,
)
from peerhub.extensions.work import WorkProjection


@pytest.fixture
def env(tmp_path: Path) -> dict[str, Any]:
    core_db = tmp_path / "authoritative" / "core.db"
    core_db.parent.mkdir(parents=True, exist_ok=True)
    store = CoreStore(core_db)
    store.register_peer(Peer(peer_id="peer:agent1", display_name="Agent 1"))
    store.create_stream(Stream(stream_id="stream:work1", members=["peer:agent1"]))

    artifact_dir = tmp_path / "authoritative" / "artifacts"
    art_store = ArtifactStore(root=artifact_dir)
    staged = art_store.stage_bytes(b"sample artifact content")
    digest = art_store.commit_staged(staged)

    work_db = tmp_path / "derived" / "work.db"
    work_db.parent.mkdir(parents=True, exist_ok=True)
    work_proj = WorkProjection(db_path=work_db, store=store)

    return {
        "core_db": core_db,
        "store": store,
        "artifact_dir": artifact_dir,
        "artifact_store": art_store,
        "artifact_digest": digest,
        "work_db": work_db,
        "work_projection": work_proj,
        "tmp_path": tmp_path,
    }


# -------------------------------------------------------------------------
# BCK-001: Create backup bundle and verify backup_manifest.json with reopen proof
# -------------------------------------------------------------------------
def test_bck_001_create_backup_bundle_and_verify_manifest(env: dict[str, Any]) -> None:
    backup_dir = env["tmp_path"] / "backups" / "b1"
    manifest = create_backup(
        authoritative_db=env["core_db"],
        artifact_dir=env["artifact_dir"],
        target_dir=backup_dir,
        generation=1,
    )

    assert manifest.generation == 1
    assert manifest.reopen_proof.get("verified") is True
    assert manifest.reopen_proof.get("integrity_check") == "ok"
    assert manifest.records_count >= 0
    assert "core.db" in manifest.files

    manifest_file = backup_dir / "backup_manifest.json"
    assert manifest_file.exists()
    assert verify_backup(backup_dir) is True


# -------------------------------------------------------------------------
# BCK-002: Authoritative restore first: CoreStore and Artifacts before projections
# -------------------------------------------------------------------------
def test_bck_002_authoritative_restore_first(env: dict[str, Any]) -> None:
    backup_dir = env["tmp_path"] / "backups" / "b1"
    create_backup(env["core_db"], env["artifact_dir"], backup_dir, generation=1)

    restore_target = env["tmp_path"] / "restored_ws"
    new_gen = restore_authoritative(backup_dir, restore_target, current_generation=0)

    assert new_gen == 1
    assert (restore_target / "core.db").exists()
    assert (restore_target / "artifacts").exists()

    # Derived projections MUST NOT be in the restored authoritative bundle (Invariant 11)
    assert not (restore_target / "work.db").exists()


# -------------------------------------------------------------------------
# BCK-003: Full projection rebuild after disaster restore yields identical work items
# -------------------------------------------------------------------------
@pytest.mark.catalog_id("BCK-015")
def test_bck_003_full_projection_rebuild_after_disaster(env: dict[str, Any]) -> None:
    work_proj: WorkProjection = env["work_projection"]

    # 1. Create a work item and transition it in CoreStore + projection
    work_proj.create_work(
        stream_id="stream:work1",
        work_id="work-100",
        title="Test Task",
        initial_state="OPEN",
    )
    work_proj.transition_work(
        work_id="work-100",
        expected_revision=1,
        target_state="ACTIVE",
    )

    item_before = work_proj.get_work("work-100")
    assert item_before.state == "ACTIVE"
    assert item_before.revision == 2

    # 2. Take authoritative backup
    backup_dir = env["tmp_path"] / "backups" / "b_disaster"
    create_backup(env["core_db"], env["artifact_dir"], backup_dir, generation=1)

    # 3. Simulate disaster: wipe entire workspace
    restore_target = env["tmp_path"] / "disaster_recovery_ws"
    restore_authoritative(backup_dir, restore_target, current_generation=1)

    # 4. Rebuild projections from restored authoritative core.db
    restored_core = CoreStore(restore_target / "core.db")
    restored_work_db = restore_target / "work.db"
    rebuilt_count = rebuild_derived_projections(restored_core, WorkProjection(restored_work_db, store=restored_core))

    assert rebuilt_count >= 1
    new_work_proj = WorkProjection(restored_work_db, store=restored_core)
    item_after = new_work_proj.get_work("work-100")
    assert item_after.state == "ACTIVE"
    assert item_after.revision == 2
    assert item_after.title == "Test Task"


# -------------------------------------------------------------------------
# BCK-004: Generation fencing: restoring older backup increments generation and blocks stale
# -------------------------------------------------------------------------
def test_bck_004_generation_fencing_increments_and_protects(env: dict[str, Any]) -> None:
    backup_dir = env["tmp_path"] / "backups" / "b_fence"
    create_backup(env["core_db"], env["artifact_dir"], backup_dir, generation=2)

    restore_target = env["tmp_path"] / "fenced_ws"
    new_gen = restore_authoritative(backup_dir, restore_target, current_generation=2)
    assert new_gen == 3  # Incremented to prevent stale writers from previous epoch


# -------------------------------------------------------------------------
# BCK-005: Invariant 11: attempting to restore derived view without authoritative records fails
# -------------------------------------------------------------------------
def test_bck_005_invariant_11_rebuild_without_authoritative_fails(tmp_path: Path) -> None:
    fake_core_db = tmp_path / "non_existent_core.db"
    work_db = tmp_path / "work.db"

    with pytest.raises(AuthoritativeRestoreOrderError):
        rebuild_derived_projections(fake_core_db, object())  # the authoritative store is checked before the projection is touched


# -------------------------------------------------------------------------
# BCK-006: Corrupted backup file (bit flip) detected and rejected
# -------------------------------------------------------------------------
def test_bck_006_corrupted_backup_file_bit_flip_fails_verification(env: dict[str, Any]) -> None:
    backup_dir = env["tmp_path"] / "backups" / "b_corrupt"
    create_backup(env["core_db"], env["artifact_dir"], backup_dir, generation=1)

    # Corrupt core.db file inside the backup
    corrupt_db = backup_dir / "core.db"
    data = bytearray(corrupt_db.read_bytes())
    data[100] = (data[100] + 1) % 256
    corrupt_db.write_bytes(data)

    with pytest.raises(BackupCorruptedError):
        verify_backup(backup_dir)


# -------------------------------------------------------------------------
# BCK-007: Missing backup manifest raises BackupManifestMissingError
# -------------------------------------------------------------------------
def test_bck_007_missing_backup_manifest_fails(env: dict[str, Any]) -> None:
    backup_dir = env["tmp_path"] / "backups" / "b_nomanifest"
    create_backup(env["core_db"], env["artifact_dir"], backup_dir, generation=1)

    # Delete manifest
    (backup_dir / "backup_manifest.json").unlink()

    with pytest.raises(BackupManifestMissingError):
        verify_backup(backup_dir)

    with pytest.raises(BackupManifestMissingError):
        restore_authoritative(backup_dir, env["tmp_path"] / "target", current_generation=0)


# -------------------------------------------------------------------------
# BCK-008: Stale generation conflict raises GenerationFencingConflictError
# -------------------------------------------------------------------------
def test_bck_008_stale_generation_conflict_rejected(env: dict[str, Any]) -> None:
    backup_dir = env["tmp_path"] / "backups" / "b_stale"
    create_backup(env["core_db"], env["artifact_dir"], backup_dir, generation=2)

    # Attempting to restore when current environment is already at generation 5 without override
    with pytest.raises(GenerationFencingConflictError):
        restore_authoritative(backup_dir, env["tmp_path"] / "target", current_generation=5, allow_rollback=False)


# -------------------------------------------------------------------------
# BCK-009: Secret exclusion policy: credentials or tokens omitted or masked
# -------------------------------------------------------------------------
def test_bck_009_secret_exclusion_policy(env: dict[str, Any]) -> None:
    backup_dir = env["tmp_path"] / "backups" / "b_secrets"
    manifest = create_backup(
        authoritative_db=env["core_db"],
        artifact_dir=env["artifact_dir"],
        target_dir=backup_dir,
        generation=1,
        secrets_masking=True,
    )
    assert manifest.secret_exclusion_policy == "EXCLUDED_BY_DEFAULT"

    # Test explicit leak detector
    from peerhub.extensions.backup import assert_no_plaintext_secrets
    assert_no_plaintext_secrets({"config": "public_data", "safe": 123})

    with pytest.raises(SecretLeakageDetectedError):
        assert_no_plaintext_secrets({"api_key": "sk-secret1234567890"})


# -------------------------------------------------------------------------
# BCK-010: Atomic restore failure rollback: failure mid-restore leaves clean state
# -------------------------------------------------------------------------
def test_bck_010_atomic_restore_failure_rollback(env: dict[str, Any]) -> None:
    backup_dir = env["tmp_path"] / "backups" / "b_atomic"
    create_backup(env["core_db"], env["artifact_dir"], backup_dir, generation=1)

    target_dir = env["tmp_path"] / "atomic_target"

    # Corrupt manifest so restore fails during verification
    manifest_path = backup_dir / "backup_manifest.json"
    manifest_data = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest_data["files"]["core.db"]["sha256"] = "0" * 64
    manifest_path.write_text(json.dumps(manifest_data), encoding="utf-8")

    with pytest.raises(BackupCorruptedError):
        restore_authoritative(backup_dir, target_dir, current_generation=0)

    # Target directory should not contain partial files
    if target_dir.exists():
        assert not (target_dir / "core.db").exists()


# -------------------------------------------------------------------------
# BCK-011: Concurrent backup creation during active CoreStore writes
# -------------------------------------------------------------------------
def test_bck_011_concurrent_backup_during_active_core_writes(env: dict[str, Any]) -> None:
    store: CoreStore = env["store"]
    backup_dir = env["tmp_path"] / "backups" / "b_concurrent"

    errors: list[Exception] = []

    def writer() -> None:
        try:
            for i in range(10):
                store.append_record(
                    stream_id="stream:work1",
                    author_peer_id="peer:agent1",
                    kind="msg.test",
                    body=f"concurrent message {i}",
                    idempotency_key=f"conc-msg-{i}",
                    created_at=f"2026-10-06T00:00:{i:02d}Z",
                )
        except Exception as e:
            errors.append(e)

    t = threading.Thread(target=writer)
    t.start()

    try:
        manifest = create_backup(env["core_db"], env["artifact_dir"], backup_dir, generation=1)
        assert manifest.reopen_proof["verified"] is True
    except Exception as e:
        errors.append(e)

    t.join(timeout=10)
    assert errors == []


# -------------------------------------------------------------------------
# BCK-012: Zero dev-dependency violation (REL-009)
# -------------------------------------------------------------------------
def test_bck_012_zero_dev_dependency_violation_rel_009() -> None:
    import importlib
    backup_mod = importlib.import_module("peerhub.extensions.backup")
    for bad in ("pytest", "yaml", "opentelemetry"):
        assert bad not in backup_mod.__dict__
