# Memory / Second Brain Contract (M3.1)

## 1. Authority Separation & Core Invariants
- **Core Invariant 3: `Memory is derived, never source truth`:**
  - Memory facts (episodic and semantic) are derived and structured from underlying event streams and artifacts.
  - Raw records and artifacts are NEVER rewritten, modified, or overwritten by memory mutations.
  - Every memory item MUST maintain an immutable `source_ref` referencing the originating record or artifact.
- **Core Invariant 4: `Context Pack bounded`:**
  - Context pack projections are ephemeral and strictly bound by deterministic `max_tokens` or `byte_limit`.
  - Packing follows explicit deterministic ordering (`PINNED` > `RELEVANCE` > `RECENCY`).
  - Items exceeding budget are omitted; over-budget context packs are never generated.
- **Memory Item Lifecycle:**
  - `CANDIDATE -> ACCEPTED -> SUPERSEDED | REVOKED`
  - `CANDIDATE -> REJECTED`
  - Superseded or revoked items are immediately excluded from active context pack assemblies.
- **Zero Dev-Dependency Violation (REL-009):**
  - Uses standard library `sqlite3` and Python standard modules only.

## 2. Public Interfaces & Protocols
The Memory module (`peerhub.m3.memory`) provides:
- `MemoryItem`:
  - `memory_id: str`
  - `key: str`
  - `content: str`
  - `source_ref: str`
  - `memory_type: str` ("episodic" | "semantic")
  - `state: str` ("CANDIDATE" | "ACCEPTED" | "REJECTED" | "SUPERSEDED" | "REVOKED")
  - `confidence: float`
  - `revision: int`
  - `superseded_by: str | None`
  - `created_at: str`
  - `updated_at: str`
- `ContextPack`:
  - `pack_id: str`
  - `items: list[MemoryItem]`
  - `total_tokens: int`
  - `budget: int`
  - `selection_strategy: str`
- `MemoryStore(db_path: Path)`:
  - `propose_memory(key: str, content: str, source_ref: str, memory_type: str = "episodic", confidence: float = 1.0) -> MemoryItem`
  - `accept_memory(memory_id: str) -> MemoryItem`
  - `reject_memory(memory_id: str, reason: str = "") -> MemoryItem`
  - `supersede_memory(old_id: str, new_id: str) -> None`
  - `revoke_memory(memory_id: str, reason: str = "") -> None`
  - `get_memory(memory_id: str) -> MemoryItem | None`
  - `query_active_memories(key_prefix: str | None = None, memory_type: str | None = None) -> list[MemoryItem]`
  - `build_context_pack(query: str, max_tokens: int, max_items: int = 10) -> ContextPack`
  - `rebuild_from_records(core_store: CoreStore) -> int`
  - `clear() -> None`
  - `close() -> None`

## 3. Memory Lifecycle State Machine
```text
[Memory Lifecycle]
         ┌───────────────┐
         │   CANDIDATE   │
         └──┬─────────┬──┘
  (accept)  │         │  (reject)
            ▼         ▼
       ┌──────────┐ ┌──────────┐
       │ ACCEPTED │ │ REJECTED │
       └──┬─────┬─┘ └──────────┘
(supersede│     │(revoke)
          ▼     ▼
┌────────────┐ ┌─────────┐
│ SUPERSEDED │ │ REVOKED │
└────────────┘ └─────────┘
```
- Active context packs only incorporate items in `ACCEPTED` state.
- Attempting invalid state transitions raises `MemoryStateTransitionError`.
