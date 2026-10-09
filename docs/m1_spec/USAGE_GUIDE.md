# PeerHub Usage/Evolution Guide

This document explains the target UX and the available scope by milestone. It is not a document that guarantees the exact legacy v0.x CLI syntax.

## M1 — Basic Collaboration

```text
workspace
→ Peer discovery/registration
→ Stream creation
→ immutable Record append
→ Session Bridge delivery/response
→ Offset catch-up
```

Long-term task control:
- Progress status: check Record/Observation/log first;
- pause: save durable control Record first, then best-effort interrupt;
- resume: resume if provider session is compatible, otherwise fresh session + bounded catch-up;
- redirect: same Stream if the final goal is the same;
- major goal change: old Stream close + new Stream reference.

### quota / diag

```text
M1 Observation -> quota/rate/resource-pool evidence
M1 Diag        -> strict read-only display
```

Unobserved values are not defaulted to 0/healthy/unlimited.

## M2 — Durable Work Continuity

Uses Artifact, Work View, Skill/Catalog, MCP, Backup/Recovery, Eval.
Record/Artifact provenance is authoritative, and Views like Work/Search must be rebuildable.

## M3 — Federated Intelligent Collaboration

Combines Search/Memory/A2A/Routing/Orchestration/Approval/Remote Runtime Port·Adapter. Actual multi-node/HA is added only when M4-I is activated.
Routing can use the quota/health evidence from M1 Observation, but the Core is not changed.

## M4+ — Optional

Opt-in to required Tracks only. UI/Notification/Enterprise/HA/Role are not conditions for M3 completion.

## Package Validation

```powershell
python .\tools\validate_package.py
```

The screen output and `PACKAGE_VALIDATION.txt` will remain identical.

## Development/Operation Closed Loop

```text
Requirement
→ RED
→ Minimum Implementation
→ deterministic/live/package GREEN
→ Release
→ Observe
→ Learn
→ next Requirement
↺
```

For Gates, only exact candidate-bound fresh PASS is valid. queued/running/cancelled/stale/unavailable do not satisfy the blocking gate.


## 2026-10-04 current implementation note

PeerHub main `4a6994e7...` now contains the M1 implementation. The legacy 109-command table is migration evidence; `peerhub-m1` is currently side-by-side and default `peerhub` cutover remains gated.
