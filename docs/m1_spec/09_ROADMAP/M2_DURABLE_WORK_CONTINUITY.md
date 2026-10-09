# M2 — Durable Work Continuity

## Goal
Continue long-term Work, Artifact, Skill/Catalog, Backup/Recovery, and Eval durably without modifying the M1 Core.

## Vertical slices

| Slice | Result | Core Invariant | Minimum Proof Before Release |
|---|---|---|---|
| M2.0 Generic Extension Host | Extension common boundary | Core imports Extension = 0 | enable/disable/migration/failure isolation |
| M2.1 Artifact | Result/evidence reference | digest/provenance immutable | tamper/path/dedup/restart |
| M2.2 Work Projection | Long-term work View | Record authoritative; projection rebuildable | replay/rebuild/crash/concurrency |
| M2.3 Skill + General Capability Catalog | HOW/WHAT SSOT | generated vendor views only | schema/drift/version/supersession |
| M2.4 MCP | tool/data/context boundary | MCP bypass write prohibited | auth/input/idempotency/error isolation |
| M2.5 Backup/Recovery | disaster continuity | authoritative recovered first | restore/replay/reconcile/generation fencing |
| M2.6 Eval/Telemetry | Quality feedback | eval != truth | deterministic dataset/regression/evidence |

## Entry
M1 Exit Gate PASS.

## Exit
Requirement/Test/Gate for each slice must be a terminal closure, and M1 functions must operate normally even when all Extensions are turned off. Also, prove logical equivalence by deleting/rebuilding derived projections using only authoritative state.

Detailed implementation boundaries are based on `M2_DETAILED_PRE_TDD_REVIEW.md`.

## Boundary between M1 and M2.0
M1's Session Bridge/Observation/Diag are first-party module boundaries statically assembled into the product. **M2.0 Generic Extension Host** does not retroactively redefine their existence. Starting from M2.0, we introduce a general extension hosting contract with manifest/version/capability/config schema/enable-disable/migration/failure isolation.

## Catalog Nomenclature
M1's `Runtime Target Catalog` owns only the minimum model/profile/runtime facts to identify RuntimeTargets. M2's `General Skill/Capability Catalog` owns Skills, general capabilities/current facts, and vendor projections. These two catalogs are not merged into a single authoritative repository.

## Required Pre-TDD Contract Before Implementation
M2 is currently **roadmap-frozen**, which does not mean implementation-ready. At a minimum, freeze the following before writing code for each slice:

- authority: authoritative / projection / cache / external evidence
- public port/API and schema/version
- state machine and terminal/error states
- null/empty/unknown semantics
- retry/idempotency/duplicate/crash boundary
- concurrency/fencing and security/trust boundary
- migration/rebuild/disable/rollback paths
- exception catalog + high-risk interaction
- Requirement → RED Test → Release Gate → DoD

Without these items, the slice will not be promoted from `PLANNED` to implementation.
