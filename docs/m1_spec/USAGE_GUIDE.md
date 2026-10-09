# PeerHub Usage/Evolution Guide

This document explains target UX and milestone scope. For runnable current
commands, use the [README Core recipe](../../README.md#core-recipe) and
[everyday commands](../../README.md#everyday-commands). Task control below is
a target workflow; the public CLI exposes only the commands listed by
`peerhub --help`.

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
python docs/m1_spec/tools/seal_package.py
```

Run from the repository root after any edit under `docs/m1_spec`. This rebuilds
`MANIFEST.json` and `SHA256SUMS.txt` and validates the package; the validation
report is recorded in `PACKAGE_VALIDATION.txt`.

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


## Current CLI and historical cutover

The side-by-side `peerhub-m1` entrypoint and the legacy 109-command runtime
have been retired. `peerhub`, `python -m peerhub`, and `python -m peerhub.cli`
use the same public CLI. The v0 source is retained only on the Git branch
`legacy/v0-main-final`; see the [structure cleanup](../implementation/STRUCTURE_CLEANUP.md).

By default `ask` starts a fresh session with bounded Stream catch-up.
`ask --resume` requests compatible native cc/cx/ag session reuse, with fresh
session fallback when incompatible. Repeat the flag on each continuing ask.
