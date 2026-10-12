# GOVERNANCE-DISPATCH-R2b-exception-parity-check-ag-deepthink-2026-09-24

**Author**: ag.deepthink
**Role**: Round 2b Exception Parity Checker

This document is a recursive MECE completeness check, verifying whether `ag.opus`'s lighter Round 1 `DispatchPolicy` proposal silently dropped critical exception and edge-case handling explicitly modeled in `cx.astra`'s richer Intent/Contract/Attestation model.

---

## 1. Resolution of the Vote-Retraction "Contradiction" (D03.4)

- **cx.astra (D03.4)**: "While REVIEWING, an explicit correction supersedes the old vote and removes it from the tally until a fresh vote is cast."
- **ag.opus (4.1.4)**: "Voter wants to retract a cast vote: Not supported. A correction is a new superseding event... The original vote remains in the audit trail."
- **Verdict**: **No real disagreement; semantic difference only.** Both proposals agree on the exact same architectural mechanism: votes are immutable facts (they remain in the audit trail and cannot be physically "retracted" or deleted), but a voter *can* cast a new correction event that supersedes their old vote in the active quorum tally. They are perfectly aligned on the mechanics.

---

## 2. Parity Check: Exception Cases and Edge Semantics

### A. D04.1 Final Call ACK Retraction Semantics
- **cx.astra**: Explicitly handles ACK retractions both *pre-authorization* (removes the ACK and holds the barrier) and *post-authorization* (becomes a revocation request that fences future work but cannot undo completed effects).
- **ag.opus**: Mentions `FC-10` (one NACK stays in final call), but has zero handling for a user or peer retracting a previously granted ACK.
- **Verdict**: **REAL GAP.** `ag.opus`'s simpler orchestrator silently drops the state transitions required for concurrent or post-hoc ACK retractions. 
- **Action**: `cx.astra`'s rigorous CAS-backed ACK state machine (specifically the `retraction` handling) is **load-bearing** and must survive into Round 3. We can bolt this state logic onto `ag.opus`'s Final Call configuration trigger without adopting `cx.astra`'s entire namespace model.

### B. D05.2 Execution Certainty and Crash-Recovery Semantics
- **cx.astra**: Differentiates `NOT_STARTED`, `MAY_HAVE_STARTED`, `STARTED`, and `COMPLETED`. Crucially, it dictates that cancellation after a claim records the observed result (preventing a completed effect from being turned into a cancelled fiction).
- **ag.opus**: Treats cancellation as a flat outcome state (e.g., `transport.cleanup_rule = "on_completion"` applies to success, definite failure, and cancellation). It does not model crash nuance or non-idempotent replay prevention.
- **Verdict**: **REAL GAP.** `ag.opus`'s thin orchestrator is too thin here; it loses explicit atomic execution fences.
- **Action**: `cx.astra`'s atomic claim/revocation fences and outcome states are **load-bearing** and must be adopted to prevent unsafe replays.

### C. D10.3 Privacy-vs-Crash-Replay Tension for Staged Prompts
- **cx.astra**: Explicitly flags the tension between privacy and reliability. Ephemeral prompts must NOT be written to durable staging (to survive crashes) unless explicitly opted into via a retained-input policy, accepting that a crash requires `INPUT_REQUIRED`.
- **ag.opus**: Sets `transport.cleanup_rule = "on_completion"`, but its exception table notes: "No cleanup on uncertain outcome... Staged file preserved regardless of cleanup_rule".
- **Verdict**: **REAL GAP.** `ag.opus` makes an unexamined, implicit choice to persist oversized ephemeral prompts to disk on crash, violating privacy expectations. 
- **Action**: `cx.astra`'s explicit opt-in policy for prompt retention must override `ag.opus`'s blind preservation rule. The lighter `DispatchPolicy` must add a `retention_mode` flag that defaults to ephemeral (deleted on crash/uncertainty).

### D. D06.3 Restriction Scope and Epoch Semantics
- **cx.astra**: Replaces blind quarantine counters with formal restrictions that have specific scopes (shared-account vs profile-specific), epochs, and expiry policies, preventing stale probes from resetting circuits.
- **ag.opus**: Uses a `health_consequence` policy that links an `ESCALATE` review directly to `health.open_manual_quarantine()`. It relies on the existing `HealthService`.
- **Verdict**: **REAL GAP.** The existing peerhub `HealthService` lacks epoch-gated restrictions and shared-account scoping. By merely triggering the existing quarantine function, `ag.opus` misses the foundational upgrade to restriction scopes that `cx.astra` modeled.
- **Action**: `cx.astra`'s epoch-gated restriction model is **load-bearing** and must be built into the HealthService. `ag.opus`'s policy trigger remains the right way to *invoke* it, but the underlying capability must be `cx.astra`'s.

### E. D03.3 Electorate Formation and Shrinkage
- **cx.astra**: Once opened, no timeout or health sweep shrinks an electorate.
- **ag.opus**: "No denominator change mid-round (existing design, correct)."
- **Verdict**: **COVERED.** Both proposals successfully capture this constraint.

---

## 3. Final Conclusion

The terminal's warning was correct. While `ag.opus`'s `DispatchPolicy` configuration structure is the superior *vessel* (radically simpler, fits the codebase), it silently dropped critical exception handling from `cx.astra`'s formal model.

Specifically, **ACK retractions (D04.1), execution certainty/crash fences (D05.2), privacy-preserving prompt retention (D10.3), and epoch-gated restriction scopes (D06.3)** are real, load-bearing requirements that `cx.astra` correctly identified and `ag.opus` missed. 

**Revised Round 3 Recommendation:**
We still use `ag.opus`'s thin `DispatchOrchestrator` and `DispatchPolicy` TOML config as the architectural skeleton. However, we must explicitly inject `cx.astra`'s state machine logic for Final Call ACKs, its execution certainty fences, and its privacy/epoch rules into the services that the orchestrator calls. Dropping `cx.astra`'s 20+ policy namespaces is still correct, but dropping its rigorous exception states is not.
