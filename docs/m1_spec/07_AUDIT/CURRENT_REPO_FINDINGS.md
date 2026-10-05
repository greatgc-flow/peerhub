# Current Repository Findings — 2026-10-05 Rebaseline

Current `greatgc-flow/peerhub` main: `4a6994e7f73933a30c5d4e8ee538cd7d736f06ce` (`fix(release): evidence tool accepts evidence-backed matrix skips`).

## Current-head assurance
- CI run `37210765163`: SUCCESS.
- release-gate run `37210767689`: SUCCESS.
- G0/G1/G2, G3 live validation, G4 package, G7 invariant, release-evidence: all SUCCESS.
- publish job in that run was SKIPPED because it was not a new publication event.

Therefore M1 implementation can be `VERIFIED` for this exact current-head scope. Distribution remains a separate axis.

## Distribution vs verification
GitHub Release `v0.11.0` is already PUBLISHED at tag commit `fa460c49...`, while current main contains later release-flow fixes. Do not retroactively label the earlier published artifact `PROMOTED` using evidence from a different commit. A stable promotion must bind gate evidence to the exact artifact/candidate being promoted.

## CLI cutover
`peerhub` remains the legacy default hierarchy and `peerhub-m1` is side-by-side. The frozen 109-command table remains migration/disposition evidence, not target architecture. Public M1 cutover remains separate from M1 implementation verification.

## Diag / quota
M1 ReadonlyDiag can read resource pools/observations, but the side-by-side M1 CLI still exposes only stream-health projection. Existing legacy `peerhub diag` provides quota telemetry. Quota/rate/freshness parity and removal/wiring of the no-op `--obs-db` surface remain explicit cutover gates.
