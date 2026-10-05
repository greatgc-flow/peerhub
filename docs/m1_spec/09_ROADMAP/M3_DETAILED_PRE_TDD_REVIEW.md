# M3 Detailed Pre-TDD Review — 2026-10-05

7개 slice를 유지합니다. 핵심 경계는 `Search != Memory`, `Routing != Orchestration`, `A2A != Execution Runtime`입니다.

## 권장 구현 Wave
`Search → Memory → Routing → Basic Approval → Orchestration → A2A → Execution Runtime Port`

## Search
REBUILDABLE projection. 모든 result는 source provenance + retrieval method/score semantics + `index_generation/source_watermark`를 가집니다. 초기 baseline은 metadata/exact + lexical FTS; embedding/reranker는 adapter optional.

## Memory / Second Brain
Memory는 `DOMAIN_DATA + DURABLE_DERIVED`; source evidence를 rewrite하지 않습니다. `CANDIDATE → ACCEPTED → SUPERSEDED/REVOKED`, reject 경로를 둡니다. Procedural memory는 Skill. Context Pack은 bounded EPHEMERAL projection이며 budget/selection order를 명시합니다. Knowledge Graph/ontology/temporal graph는 M4-K.

## A2A
`A2A Task != PeerHub Work`, `A2A Artifact != PeerHub Artifact`, `A2A Message != Record`, `A2A context != Stream`. Remote protocol state는 opaque ExternalExecutionRef로 보존하고 필요한 evidence만 Record에 매핑합니다. Agent Card는 declared evidence이며 measured truth가 아닙니다. 한 binding부터 시작하고 optional push/streaming은 capability-negotiated.

## Routing
`Routing selects; it does not execute.` 입력은 Work requirement + Capability Catalog + Observation(quota/health) + Eval + Policy. UNKNOWN은 healthy/unlimited/supported가 아닙니다. Baseline은 deterministic rule/filter/tie-break이며 AI/ML router는 optional advisor/M4-H. Route Decision은 exact input refs + policy revision + candidate reasons + digest를 보존합니다.

## Orchestration
`bounded executor of an explicit accepted plan`. Planner와 Executor를 분리하고 Plan은 Artifact로 고정 가능합니다. baseline: sequence/fan-out/join/conditional stop/bounded retry. `max_depth/max_steps/max_fanout/max_attempts/deadline/budget` 필수. M1 Execution Certainty를 재사용하며 MAY_HAVE_STARTED blind retry 금지. authority는 Work/Record/Artifact; orchestrator private DB를 truth로 두지 않습니다.

## Basic Approval
Exact side effect digest에 binding된 single-use gate만 M3 baseline. `REQUESTED → APPROVED/REJECTED/EXPIRED → CONSUMED`. Full voting/quorum/arbiter/role/leader/delegation은 M4-C/D.

## Execution Runtime Port
A2A와 분리합니다. 독립 agent 협업은 A2A, PeerHub가 execution capability를 호출하는 것은 Runtime Port. 최소 `probe/submit/get/cancel/collect`. unrestricted remote shell 금지. M3 Exit에는 fake/loopback proof까지로 충분하며 HA/replication은 M4-I.

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
