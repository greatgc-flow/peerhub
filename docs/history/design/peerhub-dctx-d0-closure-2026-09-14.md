# D0 closure — DIR-006 unanimous consensus on D-CTX disposition

**Status:** CLOSED. DIR-006 unanimous agreement reached 2026-09-14 (cc, ag.effort, cx.standard all AGREE).
**Scope:** Closes D0 ("Independent D-CTX review", holistic renewal section 10.2) with a concrete, bounded disposition — not a claim that D1 (the credential/authority system) is implemented or ready to implement.

## The disposition (as put to ag and cx for confirmation)

1. **Not yet ready to implement.** The workspace-trust-anchor question (Q2/D2) and same-user compromised-worker resistance (Q1/D3) remain genuinely open design questions, not implementation gaps.
2. **Ship label, if/when implemented:** "cooperative-local confusion protection and forensic auditability" — explicitly not peer authentication or compromised-worker resistance.
3. **Sequencing once resumed:** fix D7 (the `environment_delta` → `Popen(env=...)` replacement bug) first, since it is a real pre-existing bug independent of D-CTX. Then build the epoch/fencing layer, reusing `SqliteStateStore.mint_new_epoch()`/`mint_new_identity_and_epoch()` and `restore_authority.require_quiescent()`/`invalidate_restored_authority()` (added by today's R2/R3 work), which already materially satisfy D12's epoch-binding/no-authority-resurrection requirement. Only then, with a concrete trust-anchor answer to Q2, does D1's credential/carrier/issuer/ledger system itself begin.
4. **D1 stays held** until a workspace-trust-anchor answer to Q2 exists and has itself been reviewed.

## Voice record

- **cx.deepthink** (proposal-1 author) → **cx.astra** (holistic-renewal ratifier, self-corrected via D1–D14) → **cx.standard** (this confirmation): AGREE. "Points 1–4 accurately close D0. Final caveat: the 'cooperative-local confusion protection and forensic auditability' label must remain explicit in implementation and documentation; D1 stays held pending a reviewed, concrete D2 trust-anchor answer."
- **ag.opus** (independent review, REVISE verdict, 8 empirical canaries) → **ag.effort** (this confirmation): AGREE. "This disposition cleanly satisfies DIR-006 direction-altitude alignment and provides an unambiguous closure for D0."
- **cc** (independent review, re-verified 3 of ag.opus's code citations against current codebase, proposed this disposition): AGREE (proposer).

All three root peers explicitly confirmed. Per DIR-006 ("unanimous consensus required at direction/plan altitude"), this closes D0's "required DIR-006 agreement" exit evidence from section 10.2's work-package table.

## What this does and does not authorize

**Authorizes:** implementing the D7 environment-composition fix immediately (bounded, independent of D-CTX, unanimously agreed real bug). See `docs/design/peerhub-dctx-d0-closure-2026-09-14.md` disposition point 3.

**Does not authorize:** any D1 (credential/carrier/issuer/ledger) implementation. That remains held until a workspace-trust-anchor proposal answers Q2/D2 and that proposal itself passes independent review — a new, separate design round, not a continuation of today's work.

## Sources

- `peerhub-dctx-proposal-1-2026-09-13.md` (cx.deepthink, Round-1 proposal)
- `peerhub-holistic-renewal-RATIFIED-cx-astra-2026-09-13.md` section 8 (D1–D14 corrections, cx.astra)
- `peerhub-dctx-independent-review-ag-opus-2026-09-13.md` (ag.opus, first non-cx voice)
- `peerhub-dctx-independent-review-cc-2026-09-14.md` (cc, second non-cx voice, this disposition's origin)
