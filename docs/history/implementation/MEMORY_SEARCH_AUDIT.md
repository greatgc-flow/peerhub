# Memory / Search recovery audit — 2026-10-06

Implementation worktree evidence, not frozen milestone promotion.

## Ask parity

- Explicit `profile` (including legacy short IDs such as `standard`) resolves declarative model policy outside Core: packaged < global `PEERHUB_CONFIG_HOME/models.toml` < workspace `.peerhub/config/models.toml`, followed by explicit model/effort overrides.
- Named profiles fail closed; wildcard configuration never invents a named profile. Availability remains unknown unless independently observed.
- Explicit profile identity and resolved model/effort bind durable request idempotency. Calls without profiles retain their previous binding.
- Optional silence timeout observes any stdout/stderr bytes, coexists with the wall deadline, kills/reaps on inactivity, and preserves STARTED uncertainty without response/Offset acknowledgement or blind replay.
- AG forwards its locally advertised `--effort`; contradictory effort-qualified model settings are rejected. Native resume/control remains unsupported rather than fabricated.
- Invalid configuration validates before DB bootstrap. Read-only adapter discovery preserves future schema and corruption error classification.

## Search

- Core Records rebuild from one committed snapshot with bounded page iteration; no 10,000-record truncation.
- Optional sources are consumed through public ArtifactStore (`list_all_digests`, verified `read_bytes`) and SkillCatalogEngine (`list_skills`, `verify_skill_integrity`) ports.
- Artifact identity/watermark uses the complete digest. Skill watermark includes the source tree digest and revision.
- Rebuild runs in one projection transaction: failed source verification rolls back the previous index, rather than publishing partial rebuild success.
- Stable identity breaks rank ties. Corrupt/missing FTS tables raise an explicit error rather than returning false zero results.

## Memory

- `MemoryStore(path)` remains an explicitly projection-only API. Its local mutations are not claimed to survive source rebuild.
- `MemoryStore(path, core_store=store, stream_id=..., author_peer_id=...)` writes versioned authoritative lifecycle Records before updating the derived projection. Existing Stream/Peer membership is enforced by Core; Memory never rewrites originating facts.
- Lifecycle event body: `{"schema_version":"1.0","item": <complete MemoryItem snapshot>,"reason": "..."}`. Kind is `m3.memory.proposed|accepted|rejected|superseded|revoked`.
- The proposal owns immutable identity/content/source/type/confidence/created_at. Each legal transition increments revision, preserves immutable fields, and carries its original timestamp. Source revision fencing prevents stale mutations; supersession requires a distinct accepted replacement in the same event stream.
- Replay rejects incomplete/unknown event schemas, duplicate proposals, invalid transitions, missing replacement targets and attempted reactivation. It does not invent IDs/timestamps/defaults or turn proposals into accepted items. Invalid replay preserves the prior projection unchanged.
- Memory lifecycle must be ordered within its owning Stream; cross-stream transition ordering is not guessed.
- Context selection follows pinned > relevance > recency, then deterministic identity. Budgets use a conservative UTF-8 byte-token upper bound for content plus newline separators, not whitespace word count or an invented vendor token measurement. Optional byte limits apply to that same rendered content; metadata is not presented as prompt content.

## Remaining limitations (not silently claimed complete)

- Source event bodies previously lacking lifecycle identity/timestamps require an explicit migration policy; they cannot be truthfully auto-accepted during rebuild.
- Arbitrary metadata supplied directly to `index_artifact` exists only in the projection unless the caller also maintains an authoritative declaration. CAS enumeration alone restores digest/size/content, not invented names/owners.
- Exact structured metadata predicates and additional retrieval adapters are not implemented by the current `search(query, doc_type, limit)` port. FTS searches metadata text; it must not be advertised as arbitrary exact metadata filtering.
- The named profile policy does not yet provide a mutable observed Runtime Target Catalog/binding-management surface.
- These targeted checks do not replace exact-head full-suite, CI/live evidence or milestone Exit gates.

## Evidence

- Ask adapters + ask E2E: 99 passed before the additional short-profile/schema regression tests; `.peerhub/ask-parity-junit.xml`.
- Search/Memory base + recovery: 33 passed before additional transaction/projection-failure checks; `.peerhub/memory-search-junit.xml`.
- Production Pyright passed for modified modules. Final expanded focused run is recorded separately by the coordinating agent.
- Expanded combined run: 136 passed, one Windows test-fixture permission failure; `.peerhub/ask-memory-search-junit.xml`. The intentional corruption fixture was corrected to make its own temporary immutable CAS blob writable.
- Search/Memory final recheck after that fixture correction: 35 passed; `.peerhub/memory-search-final-junit.xml`. These are separate honest runs, not an invented aggregate green report.
- Final expanded adapters/ask/Search/Memory focused run on the corrected tree: **137 passed**, one green JUnit report `.peerhub/ask-memory-search-final-junit.xml` (102.50 seconds). Pyright for all modified production modules: zero errors/warnings.
