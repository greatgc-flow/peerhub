# PeerHub Final M1 Decision

## Core

```text
Peer
Stream
Record
Offset
```

## Core Responsibilities

```text
identify
create/join Stream
append immutable Record
read ordered Records
track Offset
basic provenance
idempotent append
```

## M1 Extension

```text
Session Bridge  -> local AI CLI / future harness runtime connection
Observation     -> quota/rate/session/version/capability observation
Diag            -> strict read-only observer reading Observation/Core
```

## Out of Scope

```text
NO AI role injection
NO coordinator/supervisor
NO planner
NO automatic routing/failover
NO consensus/governance
NO task engine
NO agent memory/RAG
NO global directive/lesson injection
NO generic lease/lock platform
NO daemon requirement
NO MCP/A2A internal architecture
```

## Long-term Extension Preparation

```text
Collaboration memory -> Stream/Record
Runtime/session memory -> Harness/Session
Short/long-term memory -> future Memory extension
Procedure memory -> Agent Skills
Changing facts -> JSON Schema validated Catalog
Remote agents -> A2A Adapter
Tools/data -> MCP
Telemetry export -> OTel Adapter
```

## Very Simple Core

We do not grow the Core to support the latest technologies.
**A small Core where Session Bridge/Extension can absorb new technologies** is the final choice.

## Current Repo Delta Check

As of 2026-10-04, the M1 implementation has been merged into main `4a6994e7...`. The past 109-command table and v0 importer/selector were removed on 2026-10-08 (v0 is a Git preserved branch), and the new `peerhub-m1` side-by-side CLI and the default `peerhub` public cutover are managed by separate maturities/gates. **The Core contract remains exactly Peer/Stream/Record/Offset.** `workspace reset`/`backup global` are maintained as Backup/Recovery future extensions.

## TDD-ready Test Baseline

The M1 contract is tracked by **85 requirements / 205 test cases** in `06_GUIDES/TEST_SET/`. We have completed the design down to Core invariants, SQLite race/crash, Bridge fencing/certainty, Observation honesty, strict read-only Diag, fake-runtime E2E, real-provider canary, and clean install/publish gate.

## TDD / Recursive MECE Baseline

- Requirements: **85**
- Tests: **205**
- Requirement required-dimension coverage: validator enforced
- State/Exception/Interaction inventories: terminal closure required
- Provider live and soak are isolated from deterministic CI

## Post-development Closed Loop

After development/testing completion, the **Done → Released → Operated → Closed** model of `08_LIFECYCLE/` is applied. Correctness invariant violations are immediate release hold/rollback candidates, and operation signals return as development inputs via evidence→triage→reproduce→requirement/regression→release→recurrence watch. Performance/capacity thresholds are not hardcoded in code or documentation, but managed by per-environment config after measuring the baseline.

## Vertical/Horizontal Final Cross-check R3

- invariant SSOT 8/8 requirement reverse tracing
- test→release gate 205/205 machine mapping
- release gate DAG explicit/fail-closed
- signal route→lifecycle/runbook resolvability
- unresolved Problem follow-up closure rules after rollback

## Complete Roadmap Assignment — R4

The required roadmap after M1 is finalized as `M2 Durable Work Continuity -> M3 Federated Intelligent Collaboration`.
M4 and beyond are not mandatory milestones but Optional Capability Tracks.
For the complete SSOT and detailed Gates, refer to `09_ROADMAP/roadmap.json` and `09_ROADMAP/PEERHUB_MASTER_ROADMAP.md`.
The legacy `diag`'s quota lookup responsibility is maintained by M1 Observation + Readonly Diag.


## 2026-10-04 implementation rebaseline

M1 code is now present upstream. However default `peerhub` is still the legacy CLI and `peerhub-m1 diag health` does not yet expose quota/rate-limit Observation views. M1 public cutover therefore remains separate and MUST preserve quota/rate-limit display parity before promotion.
