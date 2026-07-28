# Authority Proof Fixture Companion R1

Status: proposed, provider-free specification only. This companion is
additive to the 54 behavioral fixture IDs in `fixtures/CONTRACT.md`; it does
not change their count, identifiers, evidence status, or Phase 0 exit gate.

## Namespace and boundary

`AC-01` through `AC-09` are authority-cutover proof fixtures. Each begins as
`V1_SPEC_ONLY`, is excluded from the controlled-fake-runner bootstrap exit
set, and cannot be presented as cutover evidence until it has a digest-bound
capture and a separate cutover ratification. The companion supplies the
explicit-ID/status binding required by the authority-cutover contract; it does
not authorize a broker, live authority mutation, provider call, or cutover.

| ID | Proof-matrix subject | Deterministic provider-free specification |
|---|---|---|
| AC-01 | Filesystem | Model NTFS capability success plus unsupported/redirected/alias/lock/WAL rejection before any mutation. |
| AC-02 | Identity | Model resolved volume/file-ID mismatch, copied-home collision, and audited relocation-import identity binding. |
| AC-03 | Shadow | Compare immutable same-revision inputs; drift resets equivalence and permits no legacy, PeerHub, or provider effect. |
| AC-04 | Fence | Model stale final-write epoch checks, concurrent contenders, marker CAS contention, and migration-lock-renewal loss. |
| AC-05 | JSON crash | Model write-before-receipt crash, changed or omitted write scope, retained-handle custody, absent-path replacement, and custody failure. |
| AC-06 | External effect | Model explicit terminal receipt, absent receipt, ambiguous observation, and prohibition of blind replay. |
| AC-07 | Recovery | Model failure before/after marker, receipt corruption, backup-digest mismatch, fenced rollback, and PeerHub-era-write refusal. |
| AC-08 | Drain | Model normal completion, cancellation/cutoff, process-birth mismatch, static classification resolution, and digest mismatch. |
| AC-09 | Quota | Model missing/stale account evidence, independent workspaces, and manual dispatch distinct from routing advice. |

Every future AC capture must bind input/baseline digests, stable error code,
observable no-effect assertion, canonical transcript digest, and before/after
state digests. The evidence-status overlay is
`fixtures/authority-proof-status-v1.json`.
