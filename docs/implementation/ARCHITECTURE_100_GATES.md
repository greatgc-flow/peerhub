# Architecture: 91 now, evidence required for 100

Date: 2026-11-11. Tracking: #335, #323; Draft PR #349.

## Accepted versus candidate evidence

The last recorded qualitative assessment remains **90.7/100 (rounded 91)**,
using the existing ten equally weighted dimensions. It is a judgment-based
architecture assessment, not a defect-free guarantee or certification.
Source: issue #335 comment 6785372343, assessed at `05c343624914`.
The subsequent `df2e52f26dfa30eba3c8bf108278537cf0e5041e` ClaimStore change has
CI evidence but was explicitly not assigned a higher architecture score.

This commit adds review material, not a production-code optimization. The
[Bridge initialization candidate](candidates/bridge-init-20261111/README.md)
was implemented and tested in a local reconstructed runtime. Its patch has
**NOT been applied to the repository runtime**, and its full CI, Windows,
Pyright and real-provider validation are **NOT_RUN**. Do not attribute the
previous green CI run to that candidate. A green documentation-only commit
would not certify the unapplied patch either.

## Same scoring dimensions; explicit completion evidence

Each dimension contributes one tenth of its /100 score. Gap contributions
below sum to **9.3 overall points**. These are the existing score deficits,
not a promise that one extra test earns a preset number of points.

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

1. **Bridge initialization candidate:** apply in a complete checkout, verify
   the recorded before/after hashes, run its 91 regression cases and existing
   migration/fault/registry suites, Pyright, command inventory and hosted CI.
   The patch already includes the new test module and Windows marker entry.
   Inspect raw SQL and statement boundaries rather than accepting a timing-only
   result. Rollback is a source revert, not a user-data rewrite.
2. **#323 isolation:** preserve AGY writable-overlap rejection. Determine the
   supported isolation mechanism and verify it in actual execution. Neither a
   worktree alone nor an offline runtime proves that an external process is
   confined. Do not introduce a waiver merely to improve the score; unsupported
   cases must remain explicitly rejected. A product scope change requires an
   explicit recorded decision, not relabeling an untested item as N/A.
3. **Real-provider and platform evidence:** declare binaries, versions, OS,
   configuration and source revision. Live tests spend provider quota and need
   explicit opt-in under CONTRIBUTING. None was invoked in this session; the
   local PATH had no claude/codex/agy. Formal release G3 follows the repository's
   clean merged-main procedure and is separate from pre-merge candidate checks.
   No main merge or release is authorized by this plan.
4. **Independent final review:** re-evaluate all ten dimensions with reproducible
   evidence, record reviewer/provenance, and retain unresolved or contradictory
   findings. Self-review is not an independent reviewer. Do not increase scores
   solely because a test count grew, a check was skipped, or a document was added.

## 100-point decision rule

Within the declared scope, every completion item must have passing evidence
bound to the reviewed source revision, with critical findings resolved and
unsupported capabilities honestly bounded. FAIL, UNKNOWN, NOT_RUN, stale
results and unrelated revisions do not become PASS. A complete checklist is
not proof of universal correctness. The qualitative ten-axis assessment is
revisited explicitly; this plan does not assign the future score in advance.

No workflow, protection policy, runtime permissions, package version, main
branch or release is changed by these review artifacts.
