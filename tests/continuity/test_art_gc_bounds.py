"""Artifact store: streamed dedup verification, optional stream bound, GC grace period, directory fsync."""
import io
import os
import time

import pytest

from peerhub.extensions.artifact import ArtifactStore


def test_stage_stream_max_size_rejects_oversize_and_leaves_no_staging(tmp_path):
    store = ArtifactStore(tmp_path / "a")
    with pytest.raises(ValueError, match="max_size"):
        store.stage_stream(io.BytesIO(b"x" * 100), chunk_size=10, max_size=50)
    assert not list((tmp_path / "a" / ".tmp").glob("stage-*"))
    assert store.stage_stream(io.BytesIO(b"x" * 50), chunk_size=10, max_size=50).size == 50
    with pytest.raises(ValueError):
        store.stage_stream(io.BytesIO(b""), max_size=-1)


def test_duplicate_commit_verifies_existing_blob_without_loading_it(tmp_path, monkeypatch):
    store = ArtifactStore(tmp_path / "a")
    d = store.commit_staged(store.stage_bytes(b"payload"))
    monkeypatch.setattr(store, "read_bytes", lambda *_: pytest.fail("dedup must stream, not read_bytes"))
    assert store.commit_staged(store.stage_bytes(b"payload")) == d


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


def test_commit_fsyncs_the_shard_directory_on_posix(tmp_path, monkeypatch):
    import sys

    calls = []
    monkeypatch.setattr(ArtifactStore, "_fsync_dir", staticmethod(lambda d: calls.append(d)))
    store = ArtifactStore(tmp_path / "a")
    d = store.commit_staged(store.stage_bytes(b"z"))
    assert calls == [store.resolve_path(d).parent]
    assert sys.platform  # platform skip lives inside _fsync_dir itself


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
