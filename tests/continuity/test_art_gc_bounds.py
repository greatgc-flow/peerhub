"""Artifact store: streamed dedup verification, optional stream bound, GC grace period, directory fsync."""
import io
import os
import time

import pytest

from peerhub.extensions.artifact import ArtifactStore


@pytest.mark.catalog_id("ART-037")
def test_stage_stream_max_size_rejects_oversize_and_leaves_no_staging(tmp_path):
    store = ArtifactStore(tmp_path / "a")
    with pytest.raises(ValueError, match="max_size"):
        store.stage_stream(io.BytesIO(b"x" * 100), chunk_size=10, max_size=50)
    assert not list((tmp_path / "a" / ".tmp").glob("stage-*"))
    assert store.stage_stream(io.BytesIO(b"x" * 50), chunk_size=10, max_size=50).size == 50
    with pytest.raises(ValueError):
        store.stage_stream(io.BytesIO(b""), max_size=-1)


@pytest.mark.catalog_id("ART-038")
def test_duplicate_commit_verifies_existing_blob_without_loading_it(tmp_path, monkeypatch):
    store = ArtifactStore(tmp_path / "a")
    d = store.commit_staged(store.stage_bytes(b"payload"))
    monkeypatch.setattr(store, "read_bytes", lambda *_: pytest.fail("dedup must stream, not read_bytes"))
    assert store.commit_staged(store.stage_bytes(b"payload")) == d


@pytest.mark.catalog_id("ART-039")
def test_cleanup_orphans_grace_period_keeps_fresh_blobs(tmp_path):
    store = ArtifactStore(tmp_path / "a")
    fresh = store.commit_staged(store.stage_bytes(b"fresh"))
    old = store.commit_staged(store.stage_bytes(b"old"))
    past = time.time() - 7200
    os.utime(store.resolve_path(old), (past, past))
    assert store.cleanup_orphans(set(), min_age_seconds=3600) == [old]
    assert store.resolve_path(fresh).is_file()
    assert store.cleanup_orphans(set()) == [fresh]
    with pytest.raises(ValueError):
        store.cleanup_orphans(set(), min_age_seconds=-1)


@pytest.mark.catalog_id("ART-041")
def test_commit_fsyncs_the_shard_directory_on_posix(tmp_path, monkeypatch):
    import sys

    calls = []
    monkeypatch.setattr(ArtifactStore, "_fsync_dir", staticmethod(lambda d: calls.append(d)))
    store = ArtifactStore(tmp_path / "a")
    d = store.commit_staged(store.stage_bytes(b"z"))
    assert calls == [store.resolve_path(d).parent, store.root]  # new shard: the shard and the root that names it
    assert sys.platform  # platform skip lives inside _fsync_dir itself


@pytest.mark.catalog_id("SKL-019")
def test_skill_frontmatter_fallback_parses_inline_lists_like_pyyaml(monkeypatch):
    import importlib

    from peerhub.extensions.skills import SkillCatalogEngine

    text = "name: demo\ntags: [a, 'b', \"c\"]\nempty: []\nplain: value\n"
    with_yaml = SkillCatalogEngine._parse_yaml_frontmatter(text)
    real = importlib.import_module

    def no_yaml(name, *a, **k):
        if name == "yaml":
            raise ImportError(name)
        return real(name, *a, **k)

    monkeypatch.setattr(importlib, "import_module", no_yaml)
    assert SkillCatalogEngine._parse_yaml_frontmatter(text) == with_yaml == {"name": "demo", "tags": ["a", "b", "c"], "empty": [], "plain": "value"}


@pytest.mark.catalog_id("SKL-018")
def test_skill_tree_digest_framing_has_no_boundary_collisions(tmp_path):
    from peerhub.extensions.skills import SkillCatalogEngine

    one, two = tmp_path / "one", tmp_path / "two"
    one.mkdir()
    two.mkdir()
    (one / "a").write_bytes(b"1\x00b\x002")  # a single file whose content imitates a boundary and a second file
    (two / "a").write_bytes(b"1")
    (two / "b").write_bytes(b"2")
    d1, c1 = SkillCatalogEngine.compute_directory_digest(one)
    d2, c2 = SkillCatalogEngine.compute_directory_digest(two)
    assert (c1, c2) == (1, 2) and d1 != d2  # with `path NUL content NUL` framing these two trees collided
    (two / "b").write_bytes(b"3")
    assert SkillCatalogEngine.compute_directory_digest(two)[0] != d2  # content still changes the digest
    assert SkillCatalogEngine.compute_directory_digest(two) == SkillCatalogEngine.compute_directory_digest(two)  # deterministic


@pytest.mark.catalog_id("ART-040")
def test_export_verified_streams_a_blob_and_never_publishes_a_tampered_one(tmp_path):
    import os
    import stat

    from peerhub.extensions.artifact import ArtifactNotFoundError, ArtifactTamperedError

    store = ArtifactStore(tmp_path / "a")
    data = b"0123456789" * 50_000  # 500 kB, several chunks
    digest = store.commit_staged(store.stage_bytes(data))
    out = tmp_path / "out" / "copy.bin"
    assert store.export_verified(digest, out, chunk_size=4096) == len(data) and out.read_bytes() == data
    blob = store.resolve_path(digest)
    os.chmod(blob, stat.S_IREAD | stat.S_IWRITE)
    blob.write_bytes(data[:-1] + b"X")  # tamper with the committed blob
    bad = tmp_path / "out" / "bad.bin"
    with pytest.raises(ArtifactTamperedError):
        store.export_verified(digest, bad)
    assert not bad.exists() and not list((tmp_path / "out").glob(".export-*"))  # no destination, no leftover temp
    with pytest.raises(ArtifactNotFoundError):
        store.export_verified("0" * 64, tmp_path / "out" / "none.bin")


@pytest.mark.catalog_id("ART-039")
def test_gc_grace_is_measured_from_commit_time_not_staging_time(tmp_path):
    store = ArtifactStore(tmp_path / "a")
    staged = store.stage_bytes(b"slow writer")
    past = time.time() - 7200
    os.utime(staged.path, (past, past))  # staged long ago, committed just now
    digest = store.commit_staged(staged)
    assert store.cleanup_orphans(set(), min_age_seconds=3600) == []  # fresh commit: protected although the staging file was old
    assert store.resolve_path(digest).is_file()


@pytest.mark.catalog_id("ART-039")
def test_re_referencing_an_old_unreferenced_blob_renews_its_lease(tmp_path):
    store = ArtifactStore(tmp_path / "a")
    digest = store.commit_staged(store.stage_bytes(b"shared"))
    past = time.time() - 7200
    os.utime(store.resolve_path(digest), (past, past))  # an old, currently unreferenced blob
    assert store.commit_staged(store.stage_bytes(b"shared")) == digest  # a writer deduplicates against it right now
    assert store.cleanup_orphans(set(), min_age_seconds=3600) == []  # GC must not remove it before the reference lands


@pytest.mark.catalog_id("ART-041")
def test_a_new_shard_syncs_its_parent_and_an_existing_shard_does_not(tmp_path, monkeypatch):
    import hashlib
    from pathlib import Path

    calls: list[Path] = []
    monkeypatch.setattr(ArtifactStore, "_fsync_dir", staticmethod(lambda d: calls.append(Path(d))))
    store = ArtifactStore(tmp_path / "a")
    first = store.commit_staged(store.stage_bytes(b"one"))
    shard = store.resolve_path(first).parent
    assert calls == [shard, store.root]  # a new shard: the shard AND the root entry that names it
    calls.clear()
    # find a second blob whose digest lands in the SAME shard (2 hex chars = 256 shards)
    data = next(str(n).encode() for n in range(100_000) if hashlib.sha256(str(n).encode()).hexdigest()[:2] == shard.name and str(n).encode() != b"one")
    store.commit_staged(store.stage_bytes(data))
    assert calls == [shard]  # the shard already existed: only its own entry needs syncing


@pytest.mark.catalog_id("SKL-018")
def test_a_skill_file_that_changes_while_being_hashed_is_rejected_not_framed_wrongly(tmp_path, monkeypatch):
    from peerhub.extensions import skills as skills_mod
    from peerhub.extensions.boundary import SecurityBoundaryError
    from peerhub.extensions.skills import SkillCatalogEngine

    d = tmp_path / "skill"
    d.mkdir()
    f = d / "a.txt"
    f.write_bytes(b"x" * 100_000)  # several chunks
    real_fstat = skills_mod.os.fstat

    def growing(fd):
        result = real_fstat(fd)
        with open(f, "ab") as extra:  # another process appends right after the size was read
            extra.write(b"late")
        return result

    monkeypatch.setattr(skills_mod.os, "fstat", growing)
    with pytest.raises(SecurityBoundaryError, match="changed while"):
        SkillCatalogEngine.compute_directory_digest(d)
    monkeypatch.undo()
    f.write_bytes(b"x" * 100_000)
    assert SkillCatalogEngine.compute_directory_digest(d) == SkillCatalogEngine.compute_directory_digest(d)  # stable files are fine
