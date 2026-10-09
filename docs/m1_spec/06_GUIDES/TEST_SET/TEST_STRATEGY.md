# PeerHub M1 Test Strategy — Recursive MECE / TDD Ready

## Goal
Tests lock down **invariants + exception spaces + interaction risks**, not implementation details.

1. Small Core: Peer / Stream / Record / Offset.
2. durable truth: SQLite/canonical position/revision are trusted even after restart.
3. uncertainty honesty: runtime side effects are not masqueraded as exactly-once.
4. concurrency/crash/storage faults are treated as P0, on par with happy paths.
5. Prove the Core boundary and read-only nature of Diag/Observation.
6. Separate deterministic CI from real-provider empirical gates.
7. Auto-verify recursive MECE through **test-of-tests**.

## Current baseline
- Requirements: **85**
- Tests: **207**
- Exceptions classified: **85**, OPEN 0
- High-risk interactions: **34**, uncovered REQUIRED 0
- State machines: **6**, OPEN transition 0
- Required-dimension uncovered: **0**

## Test tiers
- architecture: 5
- concurrency: 22
- e2e: 20
- fault: 20
- integration: 42
- live: 8
- meta: 4
- migration: 3
- package: 13
- property: 14
- schema: 16
- security: 1
- soak: 2
- unit: 37

## Case types
- boundary: 26
- fault: 36
- meta: 4
- negative: 55
- positive: 86

## Recursive Rules
Partition iteratively in this order: `Functional area → case type → applicable risk dimension → lifecycle/failure phase → cross-feature interaction`. Applicability is declared by the requirement's `required_dimensions` and verified by the validator against the actual test union.

## Execution Policy
- PR/push: deterministic + META, excluding live/soak.
- Fast: architecture/schema/unit/property/meta.
- Core merge: Includes SQLite/concurrency/multiprocess/storage fault/migration.
- M1: Includes Bridge/Control/Observation/Diag/fake-runtime E2E/security.
- Release: package matrix + required real-provider canary.
- Scheduled: soak/capacity; evidence-only prior to explicit SLO.

## Flaky Prevention
- `sleep()` is prohibited for race/TTL. Use Barrier/Latch/ManualClock.
- Independent SQLite connections per writer; multi-process uses actual OS processes.
- Faults use VFS/adapter/fake-runtime named injection points.
- Save property failing seed/examples.
- Live providers are separate empirical evidence, not replacements for deterministic suites.

## Exit Criteria
The `Requirement → Dimension → Test → Evidence → Gate` and `State / Exception / Interaction` graphs must all be closed, and the package validator must PASS.
