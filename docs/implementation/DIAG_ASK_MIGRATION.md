# diag / ask Extension Migration — Revised Work Criteria
> **2026-10-08:** `core.legacy_import`, its frozen fixture and importer tests, and the `PEERHUB_CLI` selector have all been removed. Only the single `peerhub` CLI remains; v0 source is accessible only through the Git branch `legacy/v0-main-final`. The account below is a historical record.


This document records the initial migration. Subsequent v0 retirement and role-based path cleanup follow the [current structure cleanup](STRUCTURE_CLEANUP.md). The v0 runtime has been removed from the current product.

Basis: `ToGo/PeerHub_VerySimple_MasterRoadmap_FINAL_R9_20261005/START_HERE.md` and the Core / Session Bridge / Observation / Public CLI Cutover contracts it specifies.

- main at the start of work: `1d330a1281b99b8050698547091e488d40623d97`
- v0 comparison·recovery reference: `legacy/v0-main-final` / `57a137cd6a0cc7e89124ea2b87b72995087627a5`
- The AGY handoff proposal is reference material. The existence or merging of M2/M3 source is not treated as production promotion / VERIFIED.

## This Implementation

`ask` connects the existing `extensions.bridge.Bridge` / `ClaimStore` / `CliRuntimeTarget`. It does not create a separate generic dispatch / lease / governance engine.

1. Distinguish Peer identity from provider kind. Register builtin aliases automatically; connect custom Peers with `peer register --adapter cx|cc|ag`.
2. Conversations use `--stream` or `peer:<peer_id>:chat`. Prompts and responses are durable Records.
3. Record the claim and invocation marker before execution. Append the response, then ack the Offset.
4. Retries with `--request-id` retain the original timestamp·prompt·author·model/profile binding. Reuse completed responses; a different payload/binding is a conflict. Do not automatically rerun uncertain executions.
5. Apply CLI time and output limits. Display partial output as text independently of success, without exposing raw vendor JSON. In JSON mode, output only the result object.
6. Record task duration and certainty as an `activity` Observation. Do not infer quota or token usage solely from request execution.

Removed the hidden v0 bypass from the default `diag` call. A single read-only snapshot from `ReadonlyDiag` displays Peer / Stream / Observation / pool. Display utilization·remaining quota·elapsed window time·reset only when supported by stored observations. Preserve `MEASURED / STALE / UNKNOWN / ERROR / ABSENT / UNAVAILABLE`.

`observation refresh --peers cx cc ag` is the explicit collection command. Migrated provider probes and DTOs, the Windows direct-binary resolver, and quota parsing/transport tests to the extension. This path does not depend on v0 Runtime / dispatch / telemetry projections. Missing, NaN, or out-of-range Codex utilization is not converted to 0 or a normal quota.

The handoff proposal to retain `diag --fresh` was rejected because it violates the read-only contract. Usage examples:

```powershell
peerhub observation refresh --peers cx cc ag
peerhub diag
peerhub diag quota --json
peerhub ask cx "Reply with OK" --stream demo --request-id demo-001 --json
peerhub ask cx --query-file prompt.txt --workspace . --model MODEL --effort low
```

`--db PATH` is a global option before the command. Even if the specified path does not exist, it does not substitute another workspace DB. `diag quota --observation-db PATH` and `diag health --observation-db PATH`
can select an observation store separately from `PEERHUB_DB`.

## Removed and Retained Parts

Deleted the duplicate initial implementation `extensions/session_bridge.py` and its dedicated `tests/unit/m1/test_extensions.py`. Actual Bridge claim / fencing / certainty tests replace them. Also updated future-schema rejection tests to target the current store. Deleted files can be recovered from the Git commit at the start of work.

At the initial migration, continuity used fresh generation + bounded catch-up.
Current cc/cx/ag adapters support compatible native session reuse through
`ask --resume`, with bounded catch-up when reuse is incompatible. Current `ask`
also supports `--profile` and `--silence-timeout-seconds`; `diag --live` reads
committed snapshots, while `monitor` explicitly collects observations. These
current commands do not restore every v0 control surface. See the
[README](../../README.md#everyday-commands) for current usage.

## Validation and Release Boundaries

2026-10-06 local validation: tiny asks to actual Codex and AGY each ended as `TERMINAL`, leaving response Records / Offsets. Actual quota collection observed 2 cx windows, 1 cc window, and 4 ag windows, which the default diag displayed. The cc weekly quota was exhausted at the time, so no new evidence of live Claude ask success was produced. Claude transport/parser/certainty is validated with hermetic adapter tests.

This document describes the local implementation state of a dirty worktree. CI / live / package evidence from existing commits is not reused as evidence for these changes. No version bump / tag / push / official release proceeds until the full deterministic matrix, required provider live gates, candidate-matched package/evidence, and rollback contract cleanup are complete.

Final local checks: architecture / diag / Observation / CLI / streaming / future-schema checks: 208 PASS; separate adapter and M2/M3 checks: 270 PASS / 1 Windows symlink SKIP. After separating CLI helpers, revalidated 89 related checks as PASS. Pyright for modified runtime files: 0 errors; parser inventory check and Wave 0–9 traceability: PASS. The local wheel build for the current version and checks for inclusion of new extension files / exclusion of the deleted prototype also PASS. Stale copies of deleted files in `build/lib` found during the first incremental build were removed before rebuilding.
