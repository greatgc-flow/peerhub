# Architecture: evidence required for 100

Date: 2026-11-11. Tracking: #335, #323; Draft PR #349.

## Accepted versus candidate evidence

The last recorded qualitative assessment is **90.7/100 (rounded 91)**,
using the existing ten equally weighted dimensions. It is a judgment-based
architecture assessment, not a defect-free guarantee or certification.
Source: issue #335 comment 6785372343, assessed at `05c343624914`.
Subsequent scores require an explicit evidence-backed re-evaluation in #335;
adding tests or documents does not automatically increase the score.

The [Bridge initialization proposal](candidates/bridge-init-20261111/README.md)
is now applied to runtime code on this review branch, with the 91-case
regression module and Windows inventory entry. See the
[integration record](BRIDGE_INIT_INTEGRATION_20261111.md) for exact source hashes
and verification scope. The original candidate folder is historical evidence;
its prior NOT_APPLIED/NOT_RUN statements describe that earlier proposal.
Current hosted CI outcomes belong to the integration commit in PR #349/#335.
No previous-head PASS is relabeled as new-candidate evidence.

## Same scoring dimensions; explicit completion evidence

Each dimension contributes one tenth of its /100 score. Gap contributions
below sum to **9.3 overall points** at the last assessment. These are the
existing deficits, not a promise that one extra test earns preset points.

| Dimension | Last score /100 | Overall gap | Evidence still required before considering 100 |
|---|---:|---:|---|
| Core purity | 97 | 0.3 | Candidate-matched dependency/ownership audit; no extension/provider behavior in Core; public read boundaries reviewed |
| Logical Kernel responsibility | 89 | 1.1 | Ownership review of Bridge/Claims/Registry and fault boundaries; no duplicated owner or evidence-free cosmetic split |
| Transactions and integrity | 95 | 0.5 | Applied-candidate migration, repair, rollback, immutable-history and concurrency gates on Linux/Windows |
| Session reuse | 91 | 0.9 | Actual cc/cx/ag retained-session and explicit-selection evidence under declared supported policies, bound to candidate revision |
| Adapter independence | 94 | 0.6 | Cold-import and runtime-port checks plus actual provider contract compatibility; unavailable combinations remain unknown |
| Concurrency and scheduling | 76 | 2.4 | #323: enforceable workspace/isolation contract, same/nested-workspace conflict tests and actual provider evidence; scoped claims are not workspace isolation |
| Observability and read-only | 95 | 0.5 | Selected-scope read-only snapshot and no-collection/no-repair side-effect oracles; unknown/stale facts stay explicit |
| Recovery and errors | 90 | 1.0 | Current-candidate restart, stale-owner, uncertain-execution and failed-repair evidence; no blind retries or fabricated success |
| VerySimple maintainability | 82 | 1.8 | Measured initialization improvement, migration preservation, single-owner documentation and an independent review of remaining complexity |
| TDD and release confidence | 98 | 0.2 | Candidate-matched full supported matrix and required checks; actual provider evidence separated from offline tests and from formal release G3 |
| **Average / overall gap** | **90.7** | **9.3** | **No automatic score increase from adding this document** |

## Prioritized closure work

1. **Bridge initialization integration:** the recorded patch is now applied.
   Check the integration commit's hosted CI, including the 91 new regression
   cases, existing migration/fault/registry suites, Pyright and Windows
   inventory. Inspect persisted SQL and transaction boundaries, not only
   timing. Reverting source is sufficient; never rewrite user data.
2. **#323 isolation:** preserve AGY writable-overlap rejection. Determine the
   supported isolation mechanism and verify it in actual execution. Neither a
   worktree alone nor an offline runtime proves external process confinement.
   Do not introduce a waiver merely to improve the score. Product scope changes
   require a recorded decision, not relabeling an untested item as N/A.
3. **Real-provider and platform evidence:** declare binaries, versions, OS,
   configuration and source revision. Live tests spend provider quota and need
   explicit opt-in under CONTRIBUTING. No live calls were made in this task;
   the local PATH has no claude/codex/agy. Formal release G3 follows the clean
   merged-main procedure, separately from pre-merge checks. No main merge or
   release is authorized by this plan.
4. **Independent final review:** re-evaluate all ten dimensions with reproducible
   evidence, record reviewer/provenance and contradictory findings. Self-review
   is not an independent reviewer. Skips and unrelated revisions are not PASS.

## 100-point decision rule

Within the declared scope, every completion item must have passing evidence
bound to the reviewed source revision, with critical findings resolved and
unsupported capabilities honestly bounded. FAIL, UNKNOWN, NOT_RUN, stale
results and unrelated revisions do not become PASS. A complete checklist is
not proof of universal correctness. Revisit the qualitative ten-axis score
explicitly rather than assigning the future score in advance.

No workflow, protection policy, runtime permissions, package version, main
branch or release is changed by this integration.
