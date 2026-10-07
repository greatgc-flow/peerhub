# Search / Retrieval Contract (M3.0)

## 1. Authority Separation & Core Invariants
- **Core Invariant 1: `Core & M2 unchanged; M3 removable`:**
  - Search is an optional higher-level intelligence layer extending M2.
  - Core imports M3 = 0. Disabling M3 Search leaves Core and M2 100% operational.
- **Core Invariant 2: `Search rebuildable + provenance always`:**
  - The search index (`search.db`) is strictly a **rebuildable projection** (`DERIVED_VIEW`). It is NEVER collaboration truth.
  - Wiping or dropping `search.db` causes zero permanent data loss; running `rebuild_from_sources()` completely restores the index.
  - Every search result MUST contain immutable source provenance: `source_ref`, `doc_type`, `score`, `retrieval_method`, `source_watermark`, and `metadata`.
- **Baseline Capabilities:**
  - Lexical Full-Text Search (SQLite FTS5) + exact keyword/metadata filtering.
  - Vector embeddings and re-rankers are optional adapters, not baseline prerequisites.
- **Zero Dev-Dependency Violation (REL-009):**
  - Search engine uses pure standard library `sqlite3` (built-in FTS5) and Python standard types.

## 2. Public Interfaces & Protocols
The Search module (`peerhub.m3.search`) provides:
- `SearchResult`:
  - `doc_id: str`
  - `doc_type: str` ("record" | "artifact" | "skill")
  - `source_ref: str` (e.g. `stream:position`, `artifact:digest`, `skill:name`)
  - `snippet: str`
  - `score: float`
  - `retrieval_method: str` ("LEXICAL_FTS" | "EXACT" | "METADATA")
  - `source_watermark: str`
  - `metadata: dict[str, str | int | float | bool | None] | None` (exact scalar predicates)
- `SearchIndex(db_path: Path)`:
  - `index_record(record: Record) -> None`
  - `index_artifact(digest: str, metadata: dict[str, Any], snippet: str = "") -> None`
  - `index_skill(skill_id: str, name: str, description: str, tags: list[str]) -> None`
  - `search(query: str, doc_type: str | None = None, limit: int = 10) -> list[SearchResult]`
  - `rebuild_from_sources(core_store: CoreStore, artifact_store: ArtifactStore | None = None, skill_catalog: SkillCatalog | None = None) -> int`
  - `clear() -> None`
  - `close() -> None`

## 3. Search Index Lifecycle State Machine
```text
[Search Index Lifecycle]
  UNINITIALIZED -> READY <───┐
                    │        │ (rebuild complete)
                    ▼        │
                REBUILDING ──┘
                    │
                    ▼ (corruption detected)
                 CORRUPTED -> REBUILDING
```
- Querying a corrupted or uninitialized index triggers recovery or raises `SearchIndexCorruptedError`.
- Missing source provenance raises `SearchProvenanceMissingError`.

---

## Closure update (2026-10-08)

Written after the M2/M3 closure work (`docs/implementation/M2_M3_CLOSURE_2026-10-08_KO.md`) and checked against the code and tests. Where it conflicts with the text above, **this section wins**. The state-machine and exception JSON catalogs carry the same update.

- The `search` interface supports exact metadata filtering via `metadata={key: scalar}`. Matches must be exact in both type and value.
- The `limit` constraint is applied strictly *after* metadata filtering, ensuring valid matches are not prematurely truncated.

**Superseded or missing in the original text:**
- The `SearchIndex(db_path: Path)` interface omitted the `metadata: dict[str, Any]` argument in the `search` method definition.
- The previous contract failed to specify that limits apply after filtering.
