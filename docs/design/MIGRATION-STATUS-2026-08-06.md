# Peerhub Migration Status & Closure Plan (2026-08-06)

> **Document Status**: Current measured status and ratified migration closure plan.
> **Scope**: Captures the independent verification (ag.deepthink) of cx's migration audit, the honest gap list, and the adopted 4-artifact/6-stage closure framework.
> **Verdict**: Adopted as-is. Independent verification confirms all critical claims in the audit are accurate.

## 1. Executive Summary

A dialectical review of the migration state was conducted on 2026-08-06. The review concludes that while `peerhub` has a substantial and fully tested kernel (353/353 passing tests), authority remains 100% with the legacy `hub.py`. The original Phase 0 compatibility baseline has drifted underneath stable action names, and several Slice 5 / Phase 2 vertical dispatch contracts remain unimplemented fakes. 

The proposed 4-artifact and 6-stage closure framework is adopted as-is to replace the current CSV-only approach.

## 2. Measured Current-State Snapshot

- **Peerhub Head**: `3292ca7012076d7e7a15c1ecf2646349c051978a` on `main`.
- **Legacy Hub Head**: `54bedd7` (`fix(hub): widen post-progress zombie window from 300s to 1800s`).
- **Test Suite**: 353 collected, 353 passed.
- **Legacy `hub.py` Hash**: `bd13cf559a67b7fd90fffb3088dd76ca5921f5c1b1f2332604bcaf1d664c43e6` (Drifted from Phase 0 frozen baseline).
- **Current Integration**: Zero cutover or shadow integration exists. Neither `hub.py` nor CLI wrappers import or invoke peerhub yet.

## 3. Honest Gap List & Critique Findings

During the dialectical review, the following load-bearing claims from the audit were independently verified and found to be strictly accurate:

1. **The Hash Drift**: The SHA-256 hash of `_sys/core/hub.py` is verified to be `BD13CF55...43E6`, confirming that the underlying legacy source has evolved beyond the Phase 0 frozen baseline, despite no new actions being added.
2. **The 90-Action Inventory Match**: The legacy action surface vector is completely stable. There are exactly 90 actions listed in `hub-actions-v1.csv` and exactly 90 actions exposed by the CLI parser. The drift lies entirely in semantics, not the CLI boundary.
3. **Phase 2 Fake Contracts**: 
   - *Adapter Boundary Bypass*: `tests/integration/dispatch/test_vertical_dispatch.py` directly injects caller-created `InvocationPlan` and `ProtocolAssessment` into `dispatch_and_execute()`, bypassing the `PeerAdapter` boundaries entirely.
   - *Partial FakeAdapter*: `peerhub/builtins/fake_adapter.py` still explicitly raises `NotImplementedError` for `plan_invocation`, `new_decoder`, and `interpret_output`.
   - *Process Runner Limits*: `pipe.py` lacks timeout wiring (`process_timeout_ms`, `silence_timeout_ms`) and relies on an undeclared `psutil` dependency for process-birth proof, silently degrading identity fences without it.

The audit's findings are load-bearing, correctly measured, and the "Phase 2 Complete" claim previously stated in kickoff docs is historically premature.

## 4. The 4-Artifact Framework

To halt invisible semantic drift, the migration control system moves from a single CSV to four linked artifacts:

1. **Generated Legacy Surface Manifest**: A machine-generated (`legacy-hub-surface-current.json`) receipt of Git commit, file hashes, action vectors, parser arguments, and statically knowable state reads/writes.
2. **Typed Migration Ledger v2**: An authoritative `migration-ledger-v2.json` tracking detailed mapping (legacy arguments, effects, peerhub targets) and explicit implementation/authority status vocabulary (e.g., `KERNEL_READY`, `SHADOWING`, `PEERHUB_AUTHORITY`).
3. **Shared-Seam Ledger**: Cross-cutting helper tracking (e.g., lease/open/sweep, session reuse) so that changing a shared seam automatically flags all dependent actions for review.
4. **Automated Drift Report**: P0 enforcement on relevant Engram commits to flag schema/effect/helper changes, marking affected actions as `NEEDS_RECHARACTERIZATION`.

## 5. 6-Stage Closure Sequence

The migration must proceed in this strict order:

* **Stage 0 (Rebase Baseline)**: Bind current commits, generate the surface manifest, and reconcile the status ledger.
* **Stage 1 (Finish Fake Slice)**: Wire runner timeouts, build E2E runner tests, fix adapter boundaries/fakes, and enforce config digests. Make Phase 2 honestly complete.
* **Stage 2 (Peerhub Command Boundary)**: Add `application/api.py`, `Client`, and command dispatch. Expose internals without legacy authority changes.
* **Stage 3 (Adapter Conformance)**: Implement a real adapter end-to-end, based on measured evidence (not guesses).
* **Stage 4 (Shadow by Ownership Slice)**: Shadow migrate in coherent clusters (e.g., Dispatch/lease, Coordination, Consensus). Requires same-revision shadow comparison and rollback proof.
* **Stage 5 (Cutover and Retirement)**: Move ownership clusters one by one through `ENGRAM_AUTHORITY -> SHADOW_VALIDATE -> CUTOVER_DRAINING -> PEERHUB_AUTHORITY -> RETIRED`, ensuring no dual writers at any time.
