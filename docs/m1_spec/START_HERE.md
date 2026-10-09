# PeerHub Very Simple Master Roadmap — FINAL R4

## One Sentence

> **PeerHub maintains a small durable communication Core of Peer/Stream/Record/Offset, extends long-term Work in M2 and intelligent/remote collaboration in M3 as Extensions, and selectively activates subsequent features only when actual necessity is proven.**

## Required Roadmap

```text
M1 Durable Communication
→ M2 Durable Work Continuity
→ M3 Federated Intelligent Collaboration
→ Required Roadmap Complete
```

M4 and beyond are `Optional Capability Tracks`.

## M1 Core

Core:

```text
Peer / Stream / Record / Offset
```

First-party Modules:

```text
Session Bridge
Observation
Readonly Diag
```

**quota / rate-limit observation is already included in M1 Observation + Diag.**

## Reading Order

1. `FINAL_ROADMAP_DECISION.md`
2. `09_ROADMAP/PEERHUB_MASTER_ROADMAP.md`
3. `09_ROADMAP/CAPABILITY_MILESTONE_MATRIX.md`
4. `01_M1/CORE_CONTRACT.md`
5. `01_M1/OBSERVATION_AND_DIAG.md`
6. `09_ROADMAP/M2_DURABLE_WORK_CONTINUITY.md`
7. `09_ROADMAP/M3_FEDERATED_INTELLIGENT_COLLABORATION.md`
8. `09_ROADMAP/OPTIONAL_CAPABILITY_TRACKS.md`
10. `06_GUIDES/TEST_SET/README.md`
11. `08_LIFECYCLE/README.md`
12. `07_AUDIT/FINAL_RECURSIVE_AUDIT.md`

## Baseline

- Repo evidence: `greatgc-flow/peerhub` main `4a6994e7f73933a30c5d4e8ee538cd7d736f06ce`
- current M1 side-by-side CLI: **11 leaf commands**; default `peerhub` cutover is separate
- M1 test baseline: **85 requirements / 207 tests**
- global `AGENTS.md`: intentionally absent

## Development Start

The M1 **design contract is TDD-ready/frozen**, and the current main `4a6994e7...` waves 0-9 implementation is `VERIFIED` up to the exact-head CI/live/package/invariant/evidence gate. However, the default `peerhub` public CLI cutover is a separate maturity/gate.
M2/M3 designs can be materialized in parallel, but production promotion only occurs after the previous milestone Exit PASS.

## Unified Package Standard
This package includes a product conformance map for the general-purpose `VS-UNIFIED-PACKAGE-STANDARD 1.2.0` in `10_STANDARDS_CONFORMANCE/`. It follows common semantics but does not force the product's unique topology to be identical.


## Conformance and Maturity
Standard compliance (`COMPLY/ADAPT/...`) indicates target structural alignment, not current implementation completion. Based on design depth, M1 is TDD_READY, and the actual implementation maturity is VERIFIED. The formal maturity of M2·M3 is PLANNED, and the roadmap direction is frozen. The actual state SSOT is `10_STANDARDS_CONFORMANCE/product-maturity.json`.

- Current implementation snapshot: `07_AUDIT/CURRENT_M1_IMPLEMENTATION_SNAPSHOT_20261005.json`
- Public cutover gate: `09_ROADMAP/M1_PUBLIC_CLI_CUTOVER_GATE.md`
