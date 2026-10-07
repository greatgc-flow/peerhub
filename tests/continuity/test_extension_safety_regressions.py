"""Concrete storage/trust boundary regressions found by the contract audit."""
from __future__ import annotations

import hashlib
import os
import sqlite3
import stat
from pathlib import Path

import pytest

from peerhub.core.models import Peer, Stream
from peerhub.core.store import CoreStore
from peerhub.extensions.artifact import ArtifactStore, ArtifactTamperedError, SecurityBoundaryError, StagedArtifact
from peerhub.extensions.eval import EvalDataset, EvaluatorValidationError, capture_trace, run_eval
from peerhub.extensions.host import ExtensionHost, SchemaPrefixViolationError, ExtensionHookError
from peerhub.extensions.manifest import ExtensionManifest, SchemaValidationError
from peerhub.extensions.skills import SkillCatalogEngine, SkillTamperedError, VolatileFactRejectedError
from peerhub.extensions.work import WorkProjection


def test_artifact_short_write_is_retried(tmp_path: Path, monkeypatch):
    original = os.write
    def short_write(fd, data):
        return original(fd, data[:max(1, len(data) // 2)])
    monkeypatch.setattr(os, "write", short_write)
    store = ArtifactStore(tmp_path / "artifacts")
    content = b"abcdefgh" * 100
    digest = store.commit_staged(store.stage_bytes(content))
    assert store.read_bytes(digest) == content


def test_artifact_forged_external_source_and_changed_stage_rejected(tmp_path: Path):
    store = ArtifactStore(tmp_path / "artifacts")
    outside = tmp_path / "outside"
    outside.write_bytes(b"sensitive")
    forged = StagedArtifact(outside, hashlib.sha256(b"sensitive").hexdigest(), 9)
    with pytest.raises(SecurityBoundaryError):
        store.commit_staged(forged)
    assert outside.read_bytes() == b"sensitive"
    staged = store.stage_bytes(b"original")
    staged.path.write_bytes(b"changed")
    with pytest.raises(ArtifactTamperedError):
        store.commit_staged(staged)


def test_artifact_failed_rename_is_not_success(tmp_path: Path, monkeypatch):
    store = ArtifactStore(tmp_path / "artifacts")
    staged = store.stage_bytes(b"content")
    def denied(*args):
        raise PermissionError("simulated locking")
    monkeypatch.setattr(os, "replace", denied)
    with pytest.raises(PermissionError):
        store.commit_staged(staged)
    assert staged.path.exists()
    assert not store.resolve_path(staged.digest).exists()


def test_artifact_corrupt_existing_blob_is_not_deduplicated(tmp_path: Path):
    store = ArtifactStore(tmp_path / "artifacts")
    digest = store.commit_staged(store.stage_bytes(b"original"))
    path = store.resolve_path(digest)
    path.chmod(stat.S_IREAD | stat.S_IWRITE)
    path.write_bytes(b"corrupted")
    staged = store.stage_bytes(b"original")
    with pytest.raises(ArtifactTamperedError):
        store.commit_staged(staged)
    assert staged.path.exists()


def test_stage_file_actually_stages_bytes(tmp_path: Path):
    source = tmp_path / "input"
    source.write_bytes(b"data")
    store = ArtifactStore(tmp_path / "artifacts")
    assert store.read_bytes(store.commit_staged(store.stage_file(source))) == b"data"


@pytest.mark.parametrize("sql", ["DROP TABLE records;", "DELETE FROM records;",
                                 "ATTACH DATABASE ':memory:' AS other;", "COMMIT;",
                                 "CREATE TEMP TABLE unprefixed(value);",
                                 "CREATE VIEW unprefixed AS SELECT * FROM records;",
                                 "CREATE TABLE ext_ok(x); ALTER TABLE ext_ok RENAME TO peers;"])
def test_host_migration_cannot_bypass_boundary(tmp_path: Path, sql: str):
    host = ExtensionHost(tmp_path / "registry.db")
    with sqlite3.connect(host.db_path) as conn:
        conn.execute("CREATE TABLE records(value)")
        conn.execute("INSERT INTO records VALUES ('safe')")
    with pytest.raises(SchemaPrefixViolationError):
        host.apply_extension_schema("ext_example", sql)
    with sqlite3.connect(host.db_path) as conn:
        assert conn.execute("SELECT value FROM records").fetchall() == [("safe",)]
        assert conn.execute("SELECT name FROM sqlite_master WHERE name='ext_ok'").fetchone() is None


def test_host_schema_and_version_update_are_one_transaction(tmp_path: Path):
    host = ExtensionHost(tmp_path / "registry.db")
    host.register_manifest(ExtensionManifest(id="ext_example", version="1", entrypoint="main.py"), tmp_path)
    with sqlite3.connect(host.db_path) as conn:
        conn.executescript("CREATE TRIGGER reject_version BEFORE UPDATE OF schema_version ON m2_extension_registry "
                           "BEGIN SELECT RAISE(ABORT, 'fault'); END;")
    with pytest.raises(sqlite3.IntegrityError):
        host.apply_extension_schema("ext_example", "CREATE TABLE ext_example_data(value);", new_schema_version=2)
    with sqlite3.connect(host.db_path) as conn:
        assert conn.execute("SELECT name FROM sqlite_master WHERE name='ext_example_data'").fetchone() is None
        assert conn.execute("SELECT schema_version FROM m2_extension_registry").fetchone()[0] == 1


def test_host_entrypoint_escape_and_failed_load(tmp_path: Path):
    host = ExtensionHost(tmp_path / "registry.db")
    with pytest.raises(SchemaValidationError):
        host.register_manifest(ExtensionManifest(id="ext_escape", version="1", entrypoint="../outside.py"), tmp_path)
    (tmp_path / "broken.py").write_text("raise RuntimeError('failed')\n", encoding="utf-8")
    host.register_manifest(ExtensionManifest(id="ext_broken", version="1", entrypoint="broken.py"), tmp_path)
    with pytest.raises(ExtensionHookError):
        host.enable("ext_broken")
    assert host.get_state("ext_broken") == "FAILED"
    assert "ext_broken" not in host.loaded_modules


def test_host_disable_releases_callback_bindings(tmp_path: Path):
    host = ExtensionHost(tmp_path / "registry.db")
    (tmp_path / "main.py").write_text("READY = True\n", encoding="utf-8")
    host.register_manifest(ExtensionManifest(id="ext_example", version="1", entrypoint="main.py"), tmp_path)
    host.enable("ext_example")
    host.register_hook("ext_example", "event", lambda payload: None)
    host.disable("ext_example")
    assert not host.hooks


def test_skill_activation_checks_source_and_nested_volatile_facts(tmp_path: Path):
    store = CoreStore(tmp_path / "core.db")
    store.register_peer(Peer(peer_id="alice"))
    store.create_stream(Stream(stream_id="main", members=["alice"]))
    catalog = SkillCatalogEngine(tmp_path / "skills.db", store)
    source = tmp_path / "source"
    source.mkdir()
    manifest = source / "SKILL.md"
    manifest.write_text("---\nname: review\n---\nOriginal", encoding="utf-8")
    catalog.index_skill("main", source)
    catalog.transition_skill("review", 1, "VALIDATED")
    manifest.write_text("---\nname: review\n---\nModified", encoding="utf-8")
    with pytest.raises(SkillTamperedError):
        catalog.transition_skill("review", 2, "ACTIVE")
    assert catalog.get_skill("review").state == "VALIDATED"
    with pytest.raises(VolatileFactRejectedError):
        catalog.declare_capability("main", "model", {"runtime": {"quota": 1}})


@pytest.mark.parametrize("score", [float("nan"), float("inf"), True, "0.5", None])
def test_eval_invalid_score_types_rejected(score):
    class InvalidEvaluator:
        evaluator_type = "Invalid"
        evaluator_version = "1"
        def evaluate(self, trace, dataset):
            return {"verdict": "PASSED", "scores": {"accuracy": score}}
    with pytest.raises(EvaluatorValidationError):
        run_eval(InvalidEvaluator(), capture_trace("trace", []), EvalDataset("empty", "0" * 64, 0, "now"))


def test_work_rebuild_rejects_conflicting_and_cross_stream_records(tmp_path: Path):
    store = CoreStore(tmp_path / "core.db")
    store.register_peer(Peer(peer_id="alice"))
    for stream in ("main", "other"):
        store.create_stream(Stream(stream_id=stream, members=["alice"]))
    work = WorkProjection(tmp_path / "work.db", store)
    work.create_work("main", "task", "Original")
    work.transition_work("task", 1, "ACTIVE")
    body = {"work_id": "task", "from_state": "ACTIVE", "to_state": "DONE",
            "expected_revision": 1, "new_revision": 2}
    for index, poisoned in enumerate((body, {**body, "expected_revision": 2, "new_revision": 3, "to_state": {}},
                                      {**body, "expected_revision": 2, "new_revision": 99})):
        store.append_record(stream_id="main", author_peer_id="alice", kind="m2.work.transitioned",
                            body=poisoned, idempotency_key=f"poison-{index}", created_at="2026-10-06T00:00:00Z")
    foreign = store.append_record(stream_id="other", author_peer_id="alice", kind="m2.work.transitioned",
        body={**body, "expected_revision": 2, "new_revision": 3}, idempotency_key="foreign",
        created_at="2026-10-06T00:00:00Z")
    work.rebuild_projection([*store.read_records("main"), foreign])
    restored = work.get_work("task")
    assert (restored.state, restored.revision, restored.title) == ("ACTIVE", 2, "Original")


def test_skill_rebuild_rejects_conflicting_transition_and_volatile_catalog(tmp_path: Path):
    store = CoreStore(tmp_path / "core.db")
    store.register_peer(Peer(peer_id="alice"))
    store.create_stream(Stream(stream_id="main", members=["alice"]))
    catalog = SkillCatalogEngine(tmp_path / "skills.db", store)
    source = tmp_path / "source"
    source.mkdir()
    (source / "SKILL.md").write_text("---\nname: review\n---\nOriginal", encoding="utf-8")
    catalog.index_skill("main", source)
    catalog.transition_skill("review", 1, "VALIDATED")
    for index, to_state in enumerate(("ACTIVE", {})):
        store.append_record(stream_id="main", author_peer_id="alice", kind="m2.skill.transitioned",
            body={"skill_id": "review", "from_state": "VALIDATED", "to_state": to_state,
                  "expected_revision": 1, "new_revision": 2},
            idempotency_key=f"poison-{index}", created_at="2026-10-06T00:00:00Z")
    store.append_record(stream_id="main", author_peer_id="alice", kind="m2.catalog.declared",
        body={"capability_id": "bad", "spec": {"nested": {"quota": 99}}, "revision": 1},
        idempotency_key="bad-catalog", created_at="2026-10-06T00:00:00Z")
    catalog.rebuild_index(store.read_records("main"))
    assert catalog.get_skill("review").state == "VALIDATED"
    with pytest.raises(LookupError):
        catalog.get_capability("bad")
