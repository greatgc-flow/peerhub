"""Independent oracles for bundle boundaries, leaked truth, and mid-restore failure."""
import json
import sqlite3
from contextlib import closing

import pytest

from peerhub.core.models import Peer, Stream
from peerhub.core.store import CoreStore, StoreReplacedError
from peerhub.core.workspace import Workspace
from peerhub.extensions.backup import (BackupCorruptedError, SecretLeakageDetectedError,
                                      create_backup, restore_authoritative, verify_backup,
                                      rebuild_derived_projections)
from peerhub.extensions.work import WorkProjection


def source(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    store = CoreStore(root / "core.db")
    store.register_peer(Peer(peer_id="author"))
    store.create_stream(Stream(stream_id="s", members=["author"]))
    return root, store


def test_secret_in_authoritative_record_aborts_without_publishing_or_masking_truth(tmp_path):
    root, store = source(tmp_path)
    store.append_record(stream_id="s", author_peer_id="author", kind="text",
                        body={"api_key": "private-value"}, idempotency_key="secret", created_at="2026-10-06T00:00:00Z")
    with pytest.raises(SecretLeakageDetectedError):
        create_backup(root / "core.db", root / "artifacts", tmp_path / "bundle")
    assert not (tmp_path / "bundle").exists()
    assert store.read_records("s")[0].body["api_key"] == "private-value"
    assert not list(tmp_path.glob("backup_stage_*"))


def test_skills_backed_up_staging_excluded_and_target_cannot_be_reused(tmp_path):
    root, _ = source(tmp_path)
    (root / "skills" / "example").mkdir(parents=True)
    (root / "skills" / "example" / "SKILL.md").write_text("Public instructions")
    (root / "artifacts" / ".tmp").mkdir(parents=True)
    (root / "artifacts" / ".tmp" / "partial").write_text("not committed")
    bundle = tmp_path / "bundle"
    manifest = create_backup(root / "core.db", root / "artifacts", bundle)
    assert "skills/example/SKILL.md" in manifest.files
    assert not (bundle / "artifacts" / ".tmp").exists()
    before = (bundle / "backup_manifest.json").read_bytes()
    with pytest.raises(BackupCorruptedError):
        create_backup(root / "core.db", root / "artifacts", bundle)
    assert (bundle / "backup_manifest.json").read_bytes() == before


def test_secret_in_yaml_skill_source_is_not_a_successfully_excluded_bundle(tmp_path):
    root, _ = source(tmp_path)
    (root / "skills").mkdir()
    (root / "skills" / "credentials.yaml").write_text("api_key: private-value\n")
    with pytest.raises(SecretLeakageDetectedError):
        create_backup(root / "core.db", root / "artifacts", tmp_path / "bundle")
    assert not (tmp_path / "bundle").exists()


def test_copied_snapshot_is_reverified_before_old_target_is_touched(tmp_path, monkeypatch):
    import peerhub.extensions.backup as backup
    root, _ = source(tmp_path)
    bundle = tmp_path / "bundle"
    create_backup(root / "core.db", root / "artifacts", bundle)
    target = tmp_path / "target"
    target.mkdir()
    CoreStore(target / "core.db").register_peer(Peer(peer_id="old"))
    before = (target / "core.db").read_bytes()
    copy_file = backup.shutil.copy2
    def corrupt_copy(source_path, destination, *args, **kw):
        result = copy_file(source_path, destination, *args, **kw)
        if destination.name == "core.db":
            with destination.open("ab") as stream:
                stream.write(b"changed-during-copy")
        return result
    monkeypatch.setattr(backup.shutil, "copy2", corrupt_copy)
    with pytest.raises(BackupCorruptedError):
        restore_authoritative(bundle, target)
    assert (target / "core.db").read_bytes() == before


@pytest.mark.parametrize("attack", ["escape", "extra", "missing-core", "size", "proof"])
def test_manifest_must_describe_exact_safe_bundle(tmp_path, attack):
    root, _ = source(tmp_path)
    bundle = tmp_path / "bundle"
    create_backup(root / "core.db", root / "artifacts", bundle)
    path = bundle / "backup_manifest.json"
    data = json.loads(path.read_text())
    if attack == "escape":
        data["files"]["../source/core.db"] = data["files"]["core.db"]
    elif attack == "extra":
        (bundle / "unlisted").write_text("unexpected")
    elif attack == "missing-core":
        data["files"] = {"artifacts/x": data["files"]["core.db"]}
    elif attack == "size":
        data["files"]["core.db"]["bytes"] += 1
    else:
        data["reopen_proof"]["verified"] = False
    path.write_text(json.dumps(data))
    with pytest.raises(BackupCorruptedError):
        verify_backup(bundle)


def test_mid_restore_failure_rolls_back_existing_directory_and_success_drops_old_wal(tmp_path):
    root, _ = source(tmp_path)
    bundle = tmp_path / "bundle"
    create_backup(root / "core.db", root / "artifacts", bundle)
    target = tmp_path / "target"
    target.mkdir()
    old = CoreStore(target / "core.db")
    old.register_peer(Peer(peer_id="old"))
    old_generation = Workspace(target).generation()
    (target / "artifacts").mkdir()
    (target / "artifacts" / "stale").write_text("old")
    def fail(point):
        if point == "restore.before_publish":
            raise OSError("injected publish failure")
    with pytest.raises(OSError):
        restore_authoritative(bundle, target, fault_hook=fail)
    assert CoreStore(target / "core.db").get_peer("old")
    assert Workspace(target).generation() == old_generation
    assert (target / "artifacts" / "stale").exists()
    restore_authoritative(bundle, target)
    with pytest.raises(StoreReplacedError):
        old.register_peer(Peer(peer_id="stale-writer"))
    with pytest.raises(StoreReplacedError):
        old.list_streams()
    assert Workspace(target).generation() != old_generation
    assert not (target / "artifacts" / "stale").exists()
    with closing(sqlite3.connect(target / "core.db")) as conn:
        assert conn.execute("SELECT peer_id FROM peers").fetchall() == [("author",)]
    assert list(tmp_path.glob("target.pre-restore-*"))  # recoverable old state


def test_work_rebuild_reads_all_pages_not_only_first_slice(tmp_path):
    root, store = source(tmp_path)
    projection = WorkProjection(tmp_path / "work.db", store)
    for i in range(105):
        projection.create_work(stream_id="s", work_id=f"w{i}", title=f"work{i}")
    count = rebuild_derived_projections(store, tmp_path / "recovered.db")
    recovered = WorkProjection(tmp_path / "recovered.db", store)
    assert count == 105
    assert recovered.get_work("w104").title == "work104"
