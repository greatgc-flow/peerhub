"""Independent lifecycle replay, pagination, source ports and deterministic bounds."""
from dataclasses import asdict, replace
import json
import sqlite3
import stat

import pytest

from peerhub.core.models import Peer, Stream
from peerhub.core.store import CoreStore
from peerhub.extensions.artifact import ArtifactStore
from peerhub.extensions.memory import MemoryStore, MemoryStateTransitionError, ContextPackBudgetExceededError
from peerhub.extensions.search import SearchIndex, SearchIndexCorruptedError
from peerhub.extensions.skills import SkillCatalogEngine


@pytest.fixture
def core(tmp_path):
    store = CoreStore(tmp_path / "core.db")
    store.register_peer(Peer(peer_id="a"))
    store.create_stream(Stream(stream_id="events", members=["a"]))
    return store


def bulk_records(core, count):
    """Fast valid immutable log fixture; digest excludes server IDs and idempotency keys."""
    first = core.append_record(stream_id="events", author_peer_id="a", kind="msg", body="pagination",
                               idempotency_key="fixture-1", created_at="2026-10-06T00:00:00Z")
    with sqlite3.connect(core.db_path) as conn:
        columns = [row[1] for row in conn.execute("PRAGMA table_info(records)")]
        template = list(conn.execute("SELECT * FROM records WHERE record_id=?", (first.record_id,)).fetchone())
        rows = []
        for position in range(2, count + 1):
            row = template.copy()
            row[columns.index("record_id")] = f"fixture-{position}"
            row[columns.index("idempotency_key")] = f"fixture-{position}"
            row[columns.index("position")] = position
            rows.append(row)
        conn.executemany("INSERT INTO records VALUES (" + ",".join("?" for _ in columns) + ")", rows)


def durable(tmp_path, core):
    return MemoryStore(tmp_path / "memory.db", core_store=core, stream_id="events", author_peer_id="a")


def test_memory_exact_replay_never_reactivates_revoked_superseded_or_candidates(tmp_path, core):
    memory = durable(tmp_path, core)
    old = memory.propose_memory("key", "old", "fact:1")
    memory.accept_memory(old.memory_id)
    new = memory.propose_memory("key", "new", "fact:2")
    memory.accept_memory(new.memory_id)
    memory.supersede_memory(old.memory_id, new.memory_id)
    memory.revoke_memory(new.memory_id)
    candidate = memory.propose_memory("candidate", "unreviewed", "fact:3")
    rejected = memory.propose_memory("rejected", "bad", "fact:4")
    memory.reject_memory(rejected.memory_id)
    original = [memory.get_memory(it.memory_id) for it in (old, new, candidate, rejected)]
    memory.clear()
    assert memory.rebuild_from_records(core) == 9
    assert [memory.get_memory(it.memory_id) for it in (old, new, candidate, rejected)] == original
    assert memory.query_active_memories() == []
    assert memory.build_context_pack("key", 100).items == []


@pytest.mark.parametrize("bad_kind", ["m3.memory.accepted", "m3.memory.unknown"])
def test_malformed_or_reactivation_event_preserves_old_projection(tmp_path, core, bad_kind):
    memory = durable(tmp_path, core)
    item = memory.propose_memory("key", "content", "fact:1")
    accepted = memory.accept_memory(item.memory_id)
    memory.revoke_memory(item.memory_id)
    # Read-only projection witness: bad source must not overwrite this known-good state.
    observer = MemoryStore(memory.db_path)
    old = observer.get_memory(item.memory_id)
    wrong = replace(accepted, revision=4)
    core.append_record(stream_id="events", author_peer_id="a", kind=bad_kind,
        body={"schema_version": "1.0", "item": asdict(wrong)}, idempotency_key="bad", created_at=wrong.updated_at)
    with pytest.raises(MemoryStateTransitionError):
        observer.rebuild_from_records(core)
    assert observer.get_memory(item.memory_id) == old


def test_bare_legacy_proposal_schema_is_not_autoaccepted(tmp_path, core):
    observer = MemoryStore(tmp_path / "memory.db")
    witness = observer.propose_memory("local", "local", "fact:1")
    core.append_record(stream_id="events", author_peer_id="a", kind="m3.memory.proposed",
        body={"key": "bare", "content": "not authority"}, idempotency_key="bad", created_at="2026-10-06T00:00:00Z")
    with pytest.raises(MemoryStateTransitionError):
        observer.rebuild_from_records(core)
    assert observer.get_memory(witness.memory_id) == witness


def test_recovery_covers_records_after_ten_thousand(tmp_path, core, monkeypatch):
    bulk_records(core, 10002)
    memory = durable(tmp_path, core)
    item = memory.propose_memory("later", "not skipped", "fact:later")
    memory.accept_memory(item.memory_id)
    memory.revoke_memory(item.memory_id)
    memory.clear()
    assert memory.rebuild_from_records(core) == 3
    assert memory.get_memory(item.memory_id).state == "REVOKED"
    index = SearchIndex(tmp_path / "search.db")
    seen = []
    monkeypatch.setattr(index, "index_record", lambda rec: seen.append(rec.position))
    assert index.rebuild_from_sources(core) == 10005
    assert seen == list(range(1, 10006))


@pytest.mark.catalog_id("SRC-014")
def test_search_rebuild_includes_verified_artifacts_and_skill_sources(tmp_path, core):
    artifacts = ArtifactStore(tmp_path / "artifacts")
    digest = artifacts.commit_staged(artifacts.stage_bytes(b"artifactneedle immutable bytes"))
    skills = SkillCatalogEngine(tmp_path / "skills.db", core)
    source = tmp_path / "source"
    source.mkdir()
    (source / "SKILL.md").write_text('---\nname: canonical-skill\ndescription: skillneedle procedure\n---\nSteps\n', encoding="utf-8")
    skill = skills.index_skill("events", source, "a")
    index = SearchIndex(tmp_path / "search.db")
    assert index.rebuild_from_sources(core, artifacts, skills) == 3  # declaration Record + artifact + skill
    art = index.search("artifactneedle", "artifact")[0]
    assert art.source_ref == f"artifact:{digest}" and art.source_watermark == f"sha256-{digest}"
    hit = index.search("skillneedle", "skill")[0]
    assert hit.source_watermark == f"sha256-{skill.tree_digest}:rev-{skill.revision}"
    before = asdict(hit)
    index.rebuild_from_sources(core, artifacts, skills)
    assert asdict(index.search("skillneedle", "skill")[0]) == before


def test_context_order_pinning_and_nonwhitespace_unicode_bounds(tmp_path):
    memory = MemoryStore(tmp_path / "memory.db")
    first = memory.propose_memory("a", "same", "fact:1")
    second = memory.propose_memory("b", "same", "fact:2")
    for item in (first, second):
        memory.accept_memory(item.memory_id)
    with sqlite3.connect(memory.db_path) as conn:
        conn.execute("UPDATE memory_items SET created_at='2026-10-06T00:00:00Z'")
    pack = memory.build_context_pack("same", 20)
    assert [it.memory_id for it in pack.items] == sorted((first.memory_id, second.memory_id))
    assert memory.build_context_pack("same", 20).pack_id == pack.pack_id
    pinned = memory.build_context_pack("same", 20, max_items=1, pinned_ids=[second.memory_id])
    assert pinned.items[0].memory_id == second.memory_id
    huge = memory.propose_memory("huge", "한" * 10000, "fact:huge")
    memory.accept_memory(huge.memory_id)
    bounded = memory.build_context_pack("한", 20, byte_limit=5)
    assert huge.memory_id not in {it.memory_id for it in bounded.items}
    assert sum(len(it.content.encode("utf-8")) for it in bounded.items) <= 5
    assert memory.build_context_pack("same", 20, max_items=0).items == []
    with pytest.raises(ContextPackBudgetExceededError):
        memory.build_context_pack("same", 20, max_items=-1)


def test_empty_source_reference_and_dangling_supersession_are_rejected(tmp_path):
    memory = MemoryStore(tmp_path / "memory.db")
    with pytest.raises(MemoryStateTransitionError):
        memory.propose_memory("key", "content", "")
    item = memory.propose_memory("key", "content", "fact:1")
    memory.accept_memory(item.memory_id)
    with pytest.raises(MemoryStateTransitionError):
        memory.supersede_memory(item.memory_id, "missing")


def test_search_corruption_is_not_silently_reported_as_zero_hits(tmp_path):
    index = SearchIndex(tmp_path / "search.db")
    with sqlite3.connect(index.db_path) as conn:
        conn.execute("DROP TABLE fts_documents")
    with pytest.raises(SearchIndexCorruptedError):
        index.search("anything")
    with pytest.raises(ValueError):
        index.search("anything", limit=-1)


def test_search_failed_rebuild_rolls_back_previous_index(tmp_path, core):
    from peerhub.extensions.artifact import ArtifactTamperedError
    index = SearchIndex(tmp_path / "search.db")
    index.index_skill("witness", "witnessneedle", "previous valid index", [])
    previous = [asdict(hit) for hit in index.search("witnessneedle")]
    artifacts = ArtifactStore(tmp_path / "artifacts")
    digest = artifacts.commit_staged(artifacts.stage_bytes(b"original"))
    artifact_path = artifacts.resolve_path(digest)
    artifact_path.chmod(stat.S_IREAD | stat.S_IWRITE)  # corrupt only this owned test fixture
    artifact_path.write_bytes(b"tampered")
    with pytest.raises(ArtifactTamperedError):
        index.rebuild_from_sources(core, artifacts)
    assert [asdict(hit) for hit in index.search("witnessneedle")] == previous


def test_memory_committed_event_recovers_after_projection_write_failure(tmp_path, core, monkeypatch):
    memory = durable(tmp_path, core)
    def fail(conn, item):
        raise OSError("injected projection failure after authoritative append")
    with monkeypatch.context() as patch:
        patch.setattr(memory, "_put", fail)
        with pytest.raises(OSError):
            memory.propose_memory("key", "durable", "fact:1")
    event = core.read_records("events")[0]
    recovered = memory.get_memory(event.body["item"]["memory_id"])
    assert asdict(recovered) == event.body["item"]
    assert recovered.state == "CANDIDATE"  # recovery does not invent acceptance


@pytest.mark.catalog_id("MEM-013")
def test_quarantine_mode_skips_invalid_history_reports_it_and_keeps_valid_memories(tmp_path, core):
    memory = durable(tmp_path, core)
    good = memory.propose_memory("good", "kept", "fact:good")
    memory.accept_memory(good.memory_id)
    item = memory.propose_memory("key", "content", "fact:1")
    accepted = memory.accept_memory(item.memory_id)
    memory.revoke_memory(item.memory_id)
    core.append_record(stream_id="events", author_peer_id="a", kind="m3.memory.accepted",
        body={"schema_version": "1.0", "item": asdict(replace(accepted, revision=4))}, idempotency_key="bad", created_at=accepted.updated_at)
    core.append_record(stream_id="events", author_peer_id="a", kind="m3.memory.proposed",
        body={"key": "bare", "content": "no schema"}, idempotency_key="bad2", created_at="2026-10-06T00:00:00Z")
    fresh = MemoryStore(tmp_path / "rebuilt.db")
    with pytest.raises(MemoryStateTransitionError):  # strict stays the default
        fresh.rebuild_from_records(core)
    applied = fresh.rebuild_from_records(core, quarantine=True)
    assert applied >= 3 and len(fresh.quarantined) == 2  # nothing silently dropped: both bad events are listed with a reason
    assert all(q["record_id"] and q["reason"] for q in fresh.quarantined)
    assert fresh.get_memory(good.memory_id).state == "ACCEPTED"  # later/other valid history still rebuilt
    assert fresh.get_memory(item.memory_id).state == "REVOKED"  # the skipped reactivation did not resurrect it
    assert MemoryStore(tmp_path / "rebuilt.db").get_memory(good.memory_id) == fresh.get_memory(good.memory_id)  # durable


@pytest.mark.catalog_id("MEM-013")
def test_quarantine_skip_is_atomic_per_event(tmp_path, core):
    memory = durable(tmp_path, core)
    item = memory.propose_memory("key", "content", "fact:1")
    before = memory.get_memory(item.memory_id)
    core.append_record(stream_id="events", author_peer_id="a", kind="m3.memory.accepted",
        body={"schema_version": "1.0", "item": asdict(replace(before, revision=9))}, idempotency_key="bad", created_at=before.updated_at)
    fresh = MemoryStore(tmp_path / "r2.db")
    fresh.rebuild_from_records(core, quarantine=True)
    assert fresh.get_memory(item.memory_id).state == before.state and fresh.get_memory(item.memory_id).revision == before.revision


@pytest.mark.catalog_id("MEM-013")
def test_a_bound_store_keeps_its_quarantine_policy_across_automatic_refreshes(tmp_path, core):
    seed = durable(tmp_path, core)
    good = seed.propose_memory("good", "kept", "fact:good")
    core.append_record(stream_id="events", author_peer_id="a", kind="m3.memory.proposed",
        body={"key": "bare", "content": "no schema"}, idempotency_key="bad", created_at="2026-10-06T00:00:00Z")
    strict = MemoryStore(tmp_path / "strict.db", core_store=core, stream_id="events", author_peer_id="a")
    with pytest.raises(MemoryStateTransitionError):
        strict.get_memory(good.memory_id)  # strict is still the default: the bad history aborts the refresh
    tolerant = MemoryStore(tmp_path / "tolerant.db", core_store=core, stream_id="events", author_peer_id="a", quarantine=True)
    assert tolerant.get_memory(good.memory_id).content == "kept"  # a READ no longer raises on the bad event
    assert len(tolerant.quarantined) == 1 and tolerant.quarantined[0]["reason"]  # and the diagnosis is still reported
    again = tolerant.propose_memory("later", "after", "fact:later")  # writes keep working too
    assert tolerant.get_memory(again.memory_id).state == "CANDIDATE" and len(tolerant.quarantined) == 1
