# PeerHub Master Roadmap Final Decision — R4

## Final Structure

```text
M1 Durable Communication
  Core: Peer / Stream / Record / Offset
  Extensions: Session Bridge / Observation / Readonly Diag
        ↓
M2 Durable Work Continuity
  Extension Runtime / Artifact / Work Projection / Skill-Catalog
  MCP / Backup-Recovery / Eval-Telemetry
        ↓
M3 Federated Intelligent Collaboration
  Search / Memory-Second Brain / A2A / Routing / Orchestration
  Basic Approval-Governance / Remote Runtime Port-Adapter Contract
        ↓
Required Roadmap Complete
        ↓
M4-A ... M4-L Optional Capability Tracks
```

## Immutable Decisions

Across M1~M3 and all Optional Tracks, there are only four Cores below.

```text
Peer
Stream
Record
Offset
```

We do not elevate Task, Memory, Routing, Orchestration, Role, or Governance to Core concepts to implement M2/M3 features.

## Diag / Quota Location

Quota information lookup, which was a core operational requirement of legacy PeerHub, belongs to **M1**.

```text
Observation: collect quota/rate-limit/resource-pool evidence
Readonly Diag: lookup/display evidence
```

M3 Routing uses this evidence for selection policies, and only M4-B Resource Coordination handles actual resource allocation.

## M2 Decision

The core of M2 is that **Record is the authoritative truth and Extension View is a rebuildable projection**.
Artifacts have immutable digest/provenance, and Work/Task is not equated with Stream.
Skill/Catalog/MCP/Backup/Eval are placed at the Extension boundary.

## M3 Decision

Search/Memory/A2A/Routing/Orchestration/Approval and Remote Runtime Port/Adapter Contract are introduced, but all are maintained as replaceable layers outside the Core.
AI judgments and search indexes are not truth and preserve source/evidence.
`UNKNOWN quota != unlimited` and `UNKNOWN health != healthy` are maintained.

## Decisions Beyond M4

M4 and beyond are not mandatory milestones. `M4-A ... M4-L` are mutually independent **Optional Capability Tracks**.

Activation requires the following:

1. Actual operational evidence;
2. 5 Whys;
3. Confirmation of unresolvability with existing M1~M3 features;
4. Extension isolation capability without Core changes;
5. Requirement + RED + rollback/disable path;
6. Explicit opt-in.

## Implementation Sequence

Design may proceed ahead, but production promotion follows the sequence:

```text
M1 Exit PASS
→ M2 Exit PASS
→ M3 Exit PASS
→ evidence-driven optional tracks
```

## Final Completion Definition

After completing M3, the goal is not to build all Optional features listed on the roadmap.

> **The state where features without actual usage evidence are not built is the normal completion state of PeerHub.**

The machine-readable SSOT is `09_ROADMAP/roadmap.json`.


## Conformance and Maturity
Standard compliance (`COMPLY/ADAPT/...`) indicates target structural alignment, not current implementation completion. Based on design depth, M1 is TDD_READY, and the current main implementation maturity is VERIFIED. The formal maturity of M2·M3 is PLANNED, and the roadmap direction is frozen. The actual state SSOT is `10_STANDARDS_CONFORMANCE/product-maturity.json`.


## 2026-10-04 current implementation note

PeerHub main `4a6994e7...` now contains the M1 implementation. The legacy 109-command table is migration evidence; `peerhub-m1` is currently side-by-side and default `peerhub` cutover remains gated.
## 2026-10-05 current-head rebaseline
Based on PeerHub main `4a6994e7...`, CI and release-gate workflows are all SUCCESS, and G3 live validation and release evidence have also PASSED. Therefore, the M1 implementation is elevated to `VERIFIED` in the exact-current-head scope. The distribution state of `peerhub` legacy default ↔ `peerhub-m1` cutover and v0.11.0 GitHub Release is maintained on a separate axis. Detailed Pre-TDD reviews for M2/M3 have been added, and the Core remains the 4 elements: Peer/Stream/Record/Offset.
