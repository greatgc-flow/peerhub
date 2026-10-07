"""M3.1 Memory / Second Brain Test Suite (MEM-001..012).

Freeze Invariant 3: Memory is derived, never source truth.
Freeze Invariant 4: Context Pack bounded.
Core imports M3 = 0.
"""
from __future__ import annotations

import json
from pathlib import Path
import pytest

from peerhub.core.models import Peer, Record, Stream
from peerhub.core.store import CoreStore
from peerhub.extensions.memory import (
    MemoryStore,
    MemoryItem,
    ContextPack,
    MemoryNotFoundError,
    MemoryStateTransitionError,
    MemorySourceRewriteForbiddenError,
    ContextPackBudgetExceededError,
)


@pytest.fixture
def env(tmp_path: Path):
    core_db = tmp_path / "core.db"
    store = CoreStore(core_db)
    store.register_peer(Peer(peer_id="peer:agent1", display_name="Agent 1"))
    store.create_stream(Stream(stream_id="stream-mem", members=["peer:agent1"]))

    memory_db = tmp_path / "memory.db"
    mem_store = MemoryStore(memory_db)

    return {
        "store": store,
        "mem_store": mem_store,
        "memory_db": memory_db,
        "tmp_path": tmp_path,
    }


def test_mem_001_invariant_3_source_records_immutable(env):
    """MEM-001: Invariant 3 - Memory operations are derived and never rewrite or mutate source records."""
    store: CoreStore = env["store"]
    mem_store: MemoryStore = env["mem_store"]

    rec = store.append_record(
        stream_id="stream-mem",
        author_peer_id="peer:agent1",
        kind="event.fact",
        body={"fact": "Core architecture is immutable event log"},
        idempotency_key="fact-1",
        created_at="2026-10-06T00:00:00Z",
    )

    # Propose and accept memory
    item = mem_store.propose_memory(
        key="arch.core",
        content="Core architecture is immutable event log",
        source_ref=f"stream-mem:{rec.position}",
        memory_type="semantic",
    )
    mem_store.accept_memory(item.memory_id)

    # Verify source record is 100% unchanged in CoreStore
    core_records = store.read_records("stream-mem", after_position=0, limit=10)
    assert len(core_records) == 1
    assert core_records[0].body == {"fact": "Core architecture is immutable event log"}

    # Attempting to mutate source record via memory raises MemorySourceRewriteForbiddenError
    with pytest.raises(MemorySourceRewriteForbiddenError):
        mem_store.rewrite_source_record(store, "stream-mem", rec.position, {"new": "val"})


def test_mem_002_mandatory_source_ref(env):
    """MEM-002: Memory items maintain mandatory source_ref linking to originating stream position or artifact."""
    mem_store: MemoryStore = env["mem_store"]

    item = mem_store.propose_memory(
        key="pref.color",
        content="User prefers dark theme",
        source_ref="stream-mem:5",
        memory_type="episodic",
    )
    assert item.source_ref == "stream-mem:5"
    assert item.state == "CANDIDATE"


def test_mem_003_lifecycle_proposal_acceptance_rejection(env):
    """MEM-003: Memory lifecycle: proposal, acceptance, and rejection follow CANDIDATE state machine."""
    mem_store: MemoryStore = env["mem_store"]

    # Propose
    item1 = mem_store.propose_memory("key1", "val1", "stream-mem:1")
    item2 = mem_store.propose_memory("key2", "val2", "stream-mem:2")

    # Accept item1
    acc = mem_store.accept_memory(item1.memory_id)
    assert acc.state == "ACCEPTED"

    # Reject item2
    rej = mem_store.reject_memory(item2.memory_id, reason="Irrelevant")
    assert rej.state == "REJECTED"

    # Querying unknown memory raises MemoryNotFoundError
    with pytest.raises(MemoryNotFoundError):
        mem_store.accept_memory("non-existent-id")


def test_mem_004_illegal_state_transitions_raise(env):
    """MEM-004: Illegal state transitions on memory items raise MemoryStateTransitionError."""
    mem_store: MemoryStore = env["mem_store"]

    cand = mem_store.propose_memory("k", "v", "ref:1")
    # CANDIDATE -> SUPERSEDED directly is forbidden
    with pytest.raises(MemoryStateTransitionError):
        mem_store.supersede_memory(cand.memory_id, "new-id")
    unchanged = mem_store.get_memory(cand.memory_id)
    assert (unchanged.state, unchanged.revision, unchanged.superseded_by) == ("CANDIDATE", 1, None)

    rej = mem_store.reject_memory(cand.memory_id)
    # REJECTED -> ACCEPTED is forbidden
    with pytest.raises(MemoryStateTransitionError):
        mem_store.accept_memory(rej.memory_id)
    assert mem_store.get_memory(rej.memory_id).state == "REJECTED"
    assert mem_store.query_active_memories() == []


def test_mem_005_invariant_4_context_pack_strictly_bounded(env):
    """MEM-005: Invariant 4 - Context Pack is strictly bounded by max_tokens budget with deterministic trimming."""
    mem_store: MemoryStore = env["mem_store"]

    # Propose and accept 5 memory items
    for i in range(5):
        it = mem_store.propose_memory(
            key=f"item.{i}",
            content=f"Important memory item number {i} detailing system behaviors",
            source_ref=f"stream-mem:{i}",
        )
        mem_store.accept_memory(it.memory_id)

    # Conservative UTF-8 byte-token upper bound, not whitespace word count.
    pack = mem_store.build_context_pack(query="system", max_tokens=100)
    assert pack.total_tokens <= 100
    assert len(pack.items) >= 1
    assert pack.total_tokens > 0


def test_mem_006_invalid_budget_raises(env):
    """MEM-006: Zero or impossible budget raises ContextPackBudgetExceededError."""
    mem_store: MemoryStore = env["mem_store"]

    it = mem_store.propose_memory("k", "v", "ref:1")
    mem_store.accept_memory(it.memory_id)

    with pytest.raises(ContextPackBudgetExceededError):
        mem_store.build_context_pack(query="k", max_tokens=0)

    with pytest.raises(ContextPackBudgetExceededError):
        mem_store.build_context_pack(query="k", max_tokens=-10)


def test_mem_007_supersession_links_and_excludes_old(env):
    """MEM-007: Memory supersession links old memory to new and excludes old from active packs."""
    mem_store: MemoryStore = env["mem_store"]

    m1 = mem_store.propose_memory("user.location", "Tokyo", "ref:1")
    mem_store.accept_memory(m1.memory_id)

    m2 = mem_store.propose_memory("user.location", "Seoul", "ref:2")
    mem_store.accept_memory(m2.memory_id)

    mem_store.supersede_memory(old_id=m1.memory_id, new_id=m2.memory_id)

    # m1 state is SUPERSEDED
    old_item = mem_store.get_memory(m1.memory_id)
    assert old_item is not None
    assert old_item.state == "SUPERSEDED"
    assert old_item.superseded_by == m2.memory_id

    # Active query and context pack only contain m2
    active = mem_store.query_active_memories(key_prefix="user.location")
    assert len(active) == 1
    assert active[0].content == "Seoul"

    pack = mem_store.build_context_pack("location", max_tokens=100)
    assert all(item.memory_id != m1.memory_id for item in pack.items)
    assert any(item.memory_id == m2.memory_id for item in pack.items)


def test_mem_008_revocation_removes_from_active_queries(env):
    """MEM-008: Memory revocation transitions ACCEPTED memory to REVOKED and removes from active queries."""
    mem_store: MemoryStore = env["mem_store"]

    m = mem_store.propose_memory("temp.fact", "Temporary fact", "ref:1")
    mem_store.accept_memory(m.memory_id)

    mem_store.revoke_memory(m.memory_id, reason="Fact no longer accurate")

    revoked = mem_store.get_memory(m.memory_id)
    assert revoked is not None
    assert revoked.state == "REVOKED"

    active = mem_store.query_active_memories(key_prefix="temp.fact")
    assert len(active) == 0


def test_mem_009_rebuild_from_records(env):
    """MEM-009: Replaying memory proposal events from stream records fully reconstructs memory store."""
    store: CoreStore = env["store"]
    mem_store: MemoryStore = env["mem_store"]

    # Authoritative lifecycle events contain complete explicit snapshots.
    durable = MemoryStore(env["tmp_path"] / "durable-memory.db", core_store=store,
                          stream_id="stream-mem", author_peer_id="peer:agent1")
    original = []
    for i in range(3):
        item = durable.propose_memory(f"config.{i}", f"Config setting {i}", f"source:{i}", "semantic")
        original.append(durable.accept_memory(item.memory_id))

    # Wipe memory store
    mem_store.clear()
    assert len(mem_store.query_active_memories()) == 0

    # Rebuild from CoreStore records
    count = mem_store.rebuild_from_records(store)
    assert count == 6

    # Only explicitly accepted items are active, with original identity/timestamps.
    items = mem_store.query_active_memories()
    assert len(items) == 3
    assert sorted(items, key=lambda it: it.memory_id) == sorted(original, key=lambda it: it.memory_id)


def test_mem_010_query_active_memories_filtering(env):
    """MEM-010: Query active memories filters by key_prefix and memory_type correctly."""
    mem_store: MemoryStore = env["mem_store"]

    m1 = mem_store.propose_memory("user.pref.theme", "dark", "ref:1", memory_type="semantic")
    mem_store.accept_memory(m1.memory_id)

    m2 = mem_store.propose_memory("user.pref.lang", "ko", "ref:2", memory_type="semantic")
    mem_store.accept_memory(m2.memory_id)

    m3 = mem_store.propose_memory("session.event", "logged in", "ref:3", memory_type="episodic")
    mem_store.accept_memory(m3.memory_id)

    # Filter by key_prefix
    prefs = mem_store.query_active_memories(key_prefix="user.pref")
    assert len(prefs) == 2

    # Filter by memory_type
    episodes = mem_store.query_active_memories(memory_type="episodic")
    assert len(episodes) == 1
    assert episodes[0].key == "session.event"


def test_mem_011_clean_lifecycle_independence(env):
    """MEM-011: Disabling memory module leaves CoreStore and M2 operational with zero disruption."""
    store: CoreStore = env["store"]
    mem_store: MemoryStore = env["mem_store"]

    mem_store.close()

    # CoreStore continues functioning cleanly
    rec = store.append_record(
        stream_id="stream-mem",
        author_peer_id="peer:agent1",
        kind="ping",
        body={"status": "fine"},
        idempotency_key="ping-after-mem-close",
        created_at="2026-10-06T00:10:00Z",
    )
    assert rec.position >= 1


def test_mem_012_zero_dev_dependency_violation():
    """MEM-012: Zero dev-dependency violation: memory module runs on standard library (REL-009)."""
    import importlib
    mem_mod = importlib.import_module("peerhub.extensions.memory")
    for bad in ("pytest", "yaml", "opentelemetry", "mcp", "chromadb", "langchain"):
        assert bad not in mem_mod.__dict__
