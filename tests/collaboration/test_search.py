"""M3.0 Search / Retrieval Test Suite (SRC-001..012).

Freeze Invariant 2: Search rebuildable + provenance always.
Core imports M3 = 0.
"""
from __future__ import annotations

import json
from pathlib import Path
import pytest

from peerhub.core.models import Peer, Record, Stream
from peerhub.core.store import CoreStore
from peerhub.extensions.artifact import ArtifactStore
from peerhub.extensions.skills import SkillCatalogEngine
from peerhub.extensions.search import (
    SearchIndex,
    SearchResult,
    SearchIndexCorruptedError,
    SearchProvenanceMissingError,
    SearchQuerySyntaxError,
    SearchIndexRebuildOrderError,
)


@pytest.fixture
def env(tmp_path: Path):
    core_db = tmp_path / "core.db"
    store = CoreStore(core_db)
    store.register_peer(Peer(peer_id="peer:agent1", display_name="Agent 1"))
    store.create_stream(Stream(stream_id="stream-1", members=["peer:agent1"]))

    artifact_dir = tmp_path / "artifacts"
    artifact_store = ArtifactStore(root=artifact_dir)

    skill_db = tmp_path / "skills.db"
    skill_catalog = SkillCatalogEngine(skill_db, store=store)

    search_db = tmp_path / "search.db"
    search_index = SearchIndex(search_db)

    return {
        "store": store,
        "artifact_store": artifact_store,
        "skill_catalog": skill_catalog,
        "search_index": search_index,
        "search_db": search_db,
        "tmp_path": tmp_path,
    }


def test_src_001_initialization(env):
    """SRC-001: Invariant 2 - SearchIndex initialization creates FTS5 tables as derived projection."""
    index: SearchIndex = env["search_index"]
    assert env["search_db"].exists()
    # Ensure tables exist and ready for queries
    results = index.search("anything")
    assert isinstance(results, list)
    assert len(results) == 0


def test_src_002_provenance_strictly_included(env):
    """SRC-002: Search results strictly include mandatory source provenance fields."""
    index: SearchIndex = env["search_index"]
    store: CoreStore = env["store"]

    rec = store.append_record(
        stream_id="stream-1",
        author_peer_id="peer:agent1",
        kind="test.record",
        body={"title": "PeerHub Architecture", "content": "Deterministic event logs"},
        idempotency_key="rec-prov-1",
        created_at="2026-10-06T00:00:00Z",
    )
    index.index_record(rec)

    results = index.search("Deterministic")
    assert len(results) == 1
    res = results[0]
    assert res.doc_id == rec.record_id
    assert res.doc_type == "record"
    assert res.source_ref == f"stream-1:{rec.position}"
    assert res.score > 0
    assert res.retrieval_method in ("LEXICAL_FTS", "EXACT")
    assert res.source_watermark is not None
    assert "stream-1" in res.source_ref


def test_src_003_exact_and_prefix_keywords(env):
    """SRC-003: Exact and prefix keyword queries match records accurately."""
    index: SearchIndex = env["search_index"]
    store: CoreStore = env["store"]

    rec = store.append_record(
        stream_id="stream-1",
        author_peer_id="peer:agent1",
        kind="msg",
        body={"summary": "database-migration protocol v2-release"},
        idempotency_key="rec-kw-1",
        created_at="2026-10-06T00:01:00Z",
    )
    index.index_record(rec)

    # Exact term
    res1 = index.search("database-migration")
    assert len(res1) == 1

    # Prefix term
    res2 = index.search("protocol*")
    assert len(res2) == 1


def test_src_004_full_rebuild_from_authoritative_core(env):
    """SRC-004: Full index rebuild from authoritative CoreStore completely reconstructs search index."""
    index: SearchIndex = env["search_index"]
    store: CoreStore = env["store"]

    for i in range(5):
        store.append_record(
            stream_id="stream-1",
            author_peer_id="peer:agent1",
            kind="msg",
            body={"event": f"audit item {i}", "tag": "rebuildable"},
            idempotency_key=f"rebuild-{i}",
            created_at=f"2026-10-06T00:{i:02d}:00Z",
        )

    # Wipe search index completely (simulating data loss of derived view)
    index.clear()
    assert len(index.search("rebuildable")) == 0

    # Rebuild from authoritative store (Invariant 2)
    count = index.rebuild_from_sources(store)
    assert count >= 5

    res = index.search("rebuildable")
    assert len(res) == 5


def test_src_005_artifact_indexing_and_search(env):
    """SRC-005: Artifact metadata and content snippets are searchable via FTS5."""
    index: SearchIndex = env["search_index"]
    art_store: ArtifactStore = env["artifact_store"]

    staged = art_store.stage_bytes(b"Benchmark evaluation raw dataset metrics")
    digest = art_store.commit_staged(staged)

    index.index_artifact(
        digest=digest,
        metadata={"name": "eval_dataset.csv", "type": "dataset", "owner": "eval-team"},
        snippet="Benchmark evaluation raw dataset metrics",
    )

    results = index.search("Benchmark")
    assert len(results) == 1
    assert results[0].doc_type == "artifact"
    assert results[0].source_ref == f"artifact:{digest}"
    assert "eval_dataset.csv" in str(results[0].metadata)


def test_src_006_skill_catalog_indexing_and_search(env):
    """SRC-006: Skill catalog items and tags are searchable via FTS5."""
    index: SearchIndex = env["search_index"]

    index.index_skill(
        skill_id="skill-python-linter",
        name="Python Linter Skill",
        description="Static analysis tool for formatting and type validation",
        tags=["python", "linter", "quality"],
    )

    results = index.search("formatting")
    assert len(results) == 1
    assert results[0].doc_type == "skill"
    assert results[0].source_ref == "skill:skill-python-linter"


def test_src_007_doc_type_filter(env):
    """SRC-007: Filtering by doc_type ('record', 'artifact', 'skill') restricts results cleanly."""
    index: SearchIndex = env["search_index"]
    store: CoreStore = env["store"]

    rec = store.append_record(
        stream_id="stream-1",
        author_peer_id="peer:agent1",
        kind="msg",
        body={"common": "shared common keyword term"},
        idempotency_key="filter-rec-1",
        created_at="2026-10-06T00:00:00Z",
    )
    index.index_record(rec)

    index.index_skill(
        skill_id="skill-common",
        name="Common Skill",
        description="shared common keyword term in skill",
        tags=["shared"],
    )

    # Search with filter doc_type='skill'
    skills = index.search("common", doc_type="skill")
    assert all(r.doc_type == "skill" for r in skills)
    assert len(skills) == 1

    # Search with filter doc_type='record'
    records = index.search("common", doc_type="record")
    assert all(r.doc_type == "record" for r in records)
    assert len(records) == 1


def test_src_008_rebuild_order_error(env):
    """SRC-008: Rebuild without valid authoritative store raises SearchIndexRebuildOrderError."""
    index: SearchIndex = env["search_index"]
    with pytest.raises(SearchIndexRebuildOrderError):
        index.rebuild_from_sources(None)  # type: ignore


def test_src_009_malformed_query_syntax_resilience(env):
    """SRC-009: Malformed query syntax (special characters, unclosed quotes) handled safely without crash."""
    index: SearchIndex = env["search_index"]
    # Malformed inputs
    res1 = index.search('\"unclosed quote')
    assert isinstance(res1, list)

    res2 = index.search("AND OR NOT ***")
    assert isinstance(res2, list)

    res3 = index.search("()[]{}!^~")
    assert isinstance(res3, list)


def test_src_010_corrupted_index_detection_and_recovery(env):
    """SRC-010: Corrupted search database triggers SearchIndexCorruptedError and clean recovery on clear()."""
    db_path: Path = env["search_db"]
    index: SearchIndex = env["search_index"]
    index.close()

    # Corrupt sqlite file header completely
    db_path.write_bytes(b"CORRUPTED_NOT_A_SQLITE_DATABASE" * 10)

    # Instantiating on corrupted db raises SearchIndexCorruptedError
    with pytest.raises(SearchIndexCorruptedError):
        SearchIndex(db_path)

    # Recovery via SearchIndex.clear_path(db_path)
    SearchIndex.clear_path(db_path)
    recovered_index = SearchIndex(db_path)
    assert recovered_index.verify_integrity() is True


def test_src_011_clean_lifecycle_independence(env):
    """SRC-011: Clean lifecycle: closing and disabling search index leaves CoreStore and M2 untouched."""
    index: SearchIndex = env["search_index"]
    store: CoreStore = env["store"]

    index.close()

    # CoreStore continues functioning 100%
    rec = store.append_record(
        stream_id="stream-1",
        author_peer_id="peer:agent1",
        kind="ping",
        body={"status": "intact"},
        idempotency_key="ping-after-close",
        created_at="2026-10-06T00:00:00Z",
    )
    assert rec.position >= 1


def test_src_012_zero_dev_dependency_violation():
    """SRC-012: Zero dev-dependency violation: search index runs on standard library (REL-009)."""
    import importlib
    search_mod = importlib.import_module("peerhub.extensions.search")
    for bad in ("pytest", "yaml", "opentelemetry", "mcp", "whoosh", "tantivy"):
        assert bad not in search_mod.__dict__
