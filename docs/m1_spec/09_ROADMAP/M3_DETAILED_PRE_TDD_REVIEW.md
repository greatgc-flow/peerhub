# M3 Detailed Pre-TDD Review — 2026-10-05

We will keep the 7 slices. Core boundaries are `Search != Memory`, `Routing != Orchestration`, and `A2A != Execution Runtime`.

## Recommended Implementation Wave
`Search → Memory → Routing → Basic Approval → Orchestration → A2A → Execution Runtime Port`

## Search
REBUILDABLE projection. All results have source provenance + retrieval method/score semantics + `index_generation/source_watermark`. Initial baseline is metadata/exact + lexical FTS; embedding/reranker are adapter optional.

## Memory / Second Brain
Memory is `DOMAIN_DATA + DURABLE_DERIVED`; source evidence is not rewritten. `CANDIDATE → ACCEPTED → SUPERSEDED/REVOKED`, including a reject path. Procedural memory is Skill. Context Pack is a bounded EPHEMERAL projection specifying budget/selection order. Knowledge Graph/ontology/temporal graphs are M4-K.

## A2A
`A2A Task != PeerHub Work`, `A2A Artifact != PeerHub Artifact`, `A2A Message != Record`, `A2A context != Stream`. Remote protocol state is preserved as opaque ExternalExecutionRef and only necessary evidence is mapped to Record. Agent Card is a declared evidence, not a measured truth. Starts with one binding; optional push/streaming is capability-negotiated.

## Routing
`Routing selects; it does not execute.` Inputs are Work requirement + Capability Catalog + Observation(quota/health) + Eval + Policy. UNKNOWN is not healthy/unlimited/supported. Baseline is deterministic rule/filter/tie-break; AI/ML routers are optional advisor/M4-H. Route Decision preserves exact input refs + policy revision + candidate reasons + digest.

## Orchestration
`bounded executor of an explicit accepted plan`. Planner and Executor are separated and Plan can be pinned as an Artifact. baseline: sequence/fan-out/join/conditional stop/bounded retry. `max_depth/max_steps/max_fanout/max_attempts/deadline/budget` are required. Reuses M1 Execution Certainty; MAY_HAVE_STARTED blind retry is prohibited. Authority is Work/Record/Artifact; orchestrator private DB is not treated as truth.

## Basic Approval
Only a single-use gate bound to the exact side effect digest is the M3 baseline. `REQUESTED → APPROVED/REJECTED/EXPIRED → CONSUMED`. Full voting/quorum/arbiter/role/leader/delegation is M4-C/D.

## Execution Runtime Port
Separated from A2A. Independent agent collaboration is A2A; PeerHub calling execution capability is Runtime Port. Minimum `probe/submit/get/cancel/collect`. Unrestricted remote shell is prohibited. Fake/loopback proof is sufficient for M3 Exit; HA/replication is M4-I.

## Freeze Invariants
1. Core unchanged and M3 removable.
2. Search rebuildable + provenance always.
3. Memory is derived, never source truth.
4. Context Pack bounded.
5. A2A identities never become PeerHub identities implicitly.
6. Agent Card declarations != measured truth.
7. Routing select-only; exact evidence replayable.
8. UNKNOWN stays unknown per policy.
9. Orchestration always bounded.
10. MAY_HAVE_STARTED no blind replay.
11. Approval exact-effect/single-use.
12. Remote runtime capability-bound, no general remote shell.
