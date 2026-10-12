# R4/P4b Governance Gateway Convergence — Pre-Review Quality Check

**Author**: ag.opus (voluntary pre-check, NOT a DIR-006 ratification voice)
**Date**: 2026-09-19
**Status**: Reference material for cx's upcoming independent review

**CORRECTION (cc, added after independent spot-check of this report, same day)**: this report's claim that `docs/design/peerhub-holistic-renewal-RATIFIED-cx-astra-2026-09-13.md` "is not committed under docs/design/" and cc's section 7.1/7.2 citations from it are "unverifiable from this checkout" is **false** -- the file exists at exactly that path. Directly checked: line 380 verbatim matches cc's 7.1 quote ("A transitional shared verifier is acceptable only when both old translators and ApplicationAPI delegate to the same policy and have a removal gate"), and line 398 verbatim matches cc's 7.2 quote ("Remove direct CLI service/UoW access, duplicate identity defaults, and obsolete compatibility behavior only after parity/migration evidence"). Both of cc's citations are accurate. "Potential Design Gap #4" below (recommending the doc be committed) is moot -- it already is. All other citations in this report were independently spot-checked (api.py's stub-auth comment, consensus.py's CredentialVerifier Protocol dependency-freedom, cast_vote's credential_id shape, final_call_ack's credential_verifier call site, the test citation, and the "consensus arbiter-review" call-map entry the report flags as an unaddressed migration gap) and all confirmed accurate.
**Reviewed documents**:
1. `docs/design/peerhub-dctx-proposal-1-2026-09-13.md` (cx.deepthink's original D-CTX design, Increments 0-3)
2. `docs/design/peerhub-r4-p4b-independent-review-cc-2026-09-17.md` (cc's independent critique)
3. `docs/design/peerhub-r4-p4b-converged-design-2026-09-17.md` (2-voice converged draft, PENDING cx)
**Cited source files re-read directly**: every file reference below was verified by reading the actual line in this checkout.

---

## Part 1: Citation Verification

### Converged design citations

| Citation | Claim | Verified? | Notes |
|---|---|---|---|
| `api.py` ~line 531-532 stub auth | Comment says "assume caller context checks out for this skeleton" | **YES** | Actual location: line 532 comment, line 533 check. The comment reads `"# 4. Auth (assume caller context checks out for this skeleton, normally we'd check \`caller.client_id == cmd.submission.client_id\`)"` — it then does exactly that near-tautological `caller.client_id != cmd.submission.client_id` check. Citation is precise. |
| `dispatch_context.py` `verify_credential_for_actor`/`resolve_actor_for_credential` | Already shipped and tested | **YES** | `verify_credential_for_actor` at line 90-134, `resolve_actor_for_credential` at line 137-174. Both join through `dispatch_context_credentials` → `dispatch_requests` on `command_id` and check `selected_peer_instance_id`. Exactly as described. |
| `consensus.py` `CredentialVerifier` Protocol | Dependency-free of `persistence/` | **YES** | `consensus.py` imports from `peerhub.core.*` and `peerhub.governance.*` only (lines 3-28). No `persistence` import. The Protocol docstring at line 32-38 explicitly states "this module never depends on peerhub.persistence directly." |
| `cast_vote`'s `credential_id: str | None = None` shape | Opt-in verification | **YES** | Line 535: `credential_id: str | None = None`. Lines 549-556: if credential present → verify or reject; if absent and `verified_required` → reject; if absent and not `verified_required` → proceed as asserted. Exactly the shape described. |
| `test_cli_proposal_vote_defaults_to_cc_when_voter_and_credential_both_omitted` | Test exists and is shipped | **YES** | Collected via `pytest --collect-only`: exists in `tests/integration/application/test_proposals.py`. |
| Production call-map schema lacks `authorizer_behavior` | Field is genuinely new | **YES** | `peerhub-production-call-map-R1.json` schema has `gateway`, `authorizer` (free-text), but no `authorizer_behavior`. The converged design's proposal to add this field is a real addition. |

### cc review citations

| Citation | Claim | Verified? | Notes |
|---|---|---|---|
| D-CTX Increment 2 quote | "Asserted calls may remain available for normal-risk compatibility..." | **YES** | `peerhub-dctx-proposal-1-2026-09-13.md` line 319: verbatim match. |
| D-CTX Increment 3 quote | "Decide separately, with migration telemetry, whether asserted mutations remain supported..." | **YES** | Line 327: verbatim match. |
| D-CTX "only immediate behavior break" quote | "a claim contradicting valid context fails instead of downgrading" | **YES** | Line 329: verbatim match. |
| Ratified doc section 7.1 reference | "a transitional shared verifier is acceptable only when both old translators and ApplicationAPI delegate to the same policy" | **NOT INDEPENDENTLY VERIFIED** | cc cites a "ratified doc" section 7.1 — this appears to be from `peerhub-holistic-renewal-RATIFIED-cx-astra-2026-09-13.md` (referenced in the call-map JSON's `authority` field). I did not find this specific file committed under `docs/design/`. This citation cannot be verified against the current checkout. |
| Ratified doc section 7.2 reference | "Remove... only after parity/migration evidence" | **SAME** | Same file, same issue. |

### D-CTX proposal citations (spot-checked, not exhaustive)

| Citation | Claim | Verified? | Notes |
|---|---|---|---|
| `consensus.py:516-520` eligibility check | Actor checked against eligible sequence, vote keyed at `:529` | **STRUCTURALLY YES, LINE NUMBERS STALE** | In the current codebase, eligibility is at line 546 (`actor_id not in eligible`) and vote keying at line 566 (`votes[actor_id] = {...}`). The logic is exactly as described, but lines shifted because the D-CTX credential integration added `CredentialVerifier` and related code above `cast_vote`. This is expected drift, not a factual error. |
| `api.py:3036-3037` ApplicationAPI auth | Skeleton check | **LINE NUMBER WRONG, CONTENT CORRECT** | The file is only 633 lines. The auth stub is at line 532-533. The D-CTX proposal cited an older version (api.py was likely much larger pre-refactoring). The content description is accurate. |

---

## Part 2: Answers to the 4 Open Questions

### Question 1: Does the asserted/verified duality make sense?

**Yes, the duality is correct as designed. There is no reason to require verification universally at R4.**

The converged design's enforcement shape — "uncredentialed calls stay valid unless a domain opts into `verified_required`" — is a direct generalization of the pattern already shipped and tested at `consensus.py` lines 549-556. The rationale is well-grounded:

1. **A human at a terminal has no credential to present.** There is no `dispatch_requests` admission row for a human CLI invocation. Requiring verification universally would lock out the human operator entirely, or require inventing a "local developer credential" concept the D-CTX design explicitly chose not to create.

2. **The deferral is the D-CTX design's own choice, not an omission.** Increment 3 (line 327 of the original D-CTX proposal) literally says "Decide separately, with migration telemetry." R4/P4b IS Increment 3. It should execute Increment 3's own language, not pre-empt a decision Increment 3 says to defer.

3. **The gateway's value at R4 is architectural convergence, not universal enforcement.** Routing all CLI paths through `ApplicationAPI.submit()` makes uniform enforcement *possible* and *checkable* (via the new `authorizer_behavior` field). Whether to flip the switch for each domain is a separate, evidenced decision.

**One nuance the converged design should make more explicit:** The `ASSERTED_ONLY` → `VERIFIED_WHEN_PRESENTED` → `VERIFIED_REQUIRED` progression is per-command, not per-deployment. This means the same peerhub installation can have some commands at `ASSERTED_ONLY` and others at `VERIFIED_REQUIRED` simultaneously — this is correct and intended, but should be stated outright so a future implementer doesn't interpret the enum as a global configuration knob.

### Question 2: Is gating default-removal as a separate, later step correct?

**Yes, emphatically. Default-removal must not happen alongside routing migration.**

The evidence is concrete:

- `test_cli_proposal_vote_defaults_to_cc_when_voter_and_credential_both_omitted` (confirmed present in `tests/integration/application/test_proposals.py`) is a real, shipped, deliberately-authored backward-compatibility test. It was added on 2026-09-16, within this same design arc — not a legacy artifact someone forgot about, but an intentional compatibility guarantee for v0.4.0.

- The distinction between "routing changes" and "behavior changes" is load-bearing. Routing migration (DIRECT_CLI_BYPASS → APPLICATION_API_GATEWAY) is a zero-behavior-change refactor: the same asserted calls that worked before continue working, just through a different Python entrance. Removing defaults would be a *behavior* change — suddenly, `peerhub consensus proposal-vote --choice agree` without `--voter cc` would fail (or would it? that depends on whether the gateway resolves a default or requires an explicit actor). This is a decision that must be backed by telemetry showing the default path is unused, per the original design.

**No design gap here.** The converged design is correctly more conservative than ag.deepthink's original proposal on this point, and cc's correction is well-reasoned.

### Question 3: Is the `authorizer_behavior` enum the right shape?

**Yes, the four-value enum is well-designed. I have one refinement.**

The proposed values:
- `NOT_APPLICABLE` — read/observation commands
- `ASSERTED_ONLY` — routed through gateway, no domain-specific `verified_required`
- `VERIFIED_WHEN_PRESENTED` — credential checked if given, not required
- `VERIFIED_REQUIRED` — mandatory verification

This maps directly to the three enforcement branches already shipped in `consensus.py`:

| `authorizer_behavior` | Maps to `cast_vote` branch | Lines |
|---|---|---|
| `ASSERTED_ONLY` | `credential_id is None and not verified_required` → proceeds | L549-556, else branch implicit |
| `VERIFIED_WHEN_PRESENTED` | `credential_id is not None` → verify, reject on failure | L550-554 |
| `VERIFIED_REQUIRED` | `credential_id is None and verified_required` → reject | L555-556 |

This is clean. The enum values are self-documenting, mutually exclusive, and mechanically checkable.

**Refinement**: I recommend the field be a *per-command static declaration* in the call map, not something that can vary per-invocation at the call-map level. The *runtime* behavior of a specific invocation may vary (e.g., `consensus vote` on a `verified_required=True` round behaves as `VERIFIED_REQUIRED` even though the command is declared `VERIFIED_WHEN_PRESENTED`), but the call-map should record the command's *static capability* — what the command is *prepared to enforce*, not what any particular invocation happened to enforce. This means:

- `consensus vote` should be `VERIFIED_WHEN_PRESENTED` in the call map (it CAN verify, and DOES when a credential is given or the round demands it).
- `consensus propose` should be `ASSERTED_ONLY` unless/until a proposal-creation-level verification policy is added.
- `error report` should be `ASSERTED_ONLY` after initial migration.

This gives a stable, version-controlled record of per-command capabilities, independent of runtime state.

### Question 4: Citation-level corrections from my own re-read

1. **D-CTX proposal's line numbers are stale for `consensus.py`.** The proposal cites `:516-520` (eligibility) and `:529` (vote keying). In the current code, these are at lines 546 and 566 respectively, shifted by the `CredentialVerifier` Protocol addition. Not a substantive error but worth noting for anyone trying to follow the citations against the current checkout.

2. **D-CTX proposal's `api.py` line citation `3036-3037` is dramatically wrong for the current file.** The file is 633 lines total. The auth stub is at line 532-533. This likely reflects an older, much larger version of `api.py` (possibly before a refactoring split). The content description is accurate; only the line number is wrong.

3. **cc's review cites a "ratified doc" (sections 7.1, 7.2) that I cannot find committed in the current checkout.** The call-map JSON's `authority` field references `docs/design/peerhub-holistic-renewal-RATIFIED-cx-astra-2026-09-13.md`. This file does not appear in `docs/design/` in the current checkout. Either it was not committed, or it was committed under a different name, or it's in a different branch. **cx should verify these citations against the actual file**, wherever it lives. cc's use of these citations is plausible but unverifiable from this checkout alone.

4. **No `final_call_ack` credential verification is mentioned in the converged design.** The `consensus.py` file applies the same `credential_verifier` pattern to `final_call_ack` (lines 170-176) as it does to `cast_vote` (lines 549-556). The converged design focuses on `cast_vote` but doesn't explicitly mention whether `final_call_ack`'s existing verification is also subsumed by the gateway migration. Since the same `CredentialVerifier` Protocol handles both, the gateway migration should handle both — but this should be stated explicitly to avoid ambiguity.

---

## Part 3: Migration Order Soundness

### "Operational-error first, consensus last"

**Sound.** The reasoning chain is:

1. `operational-error` (`error report`, `error review resolve`) is the minimal-dependency domain — no existing verification, no identity defaults, no complex inter-domain interactions. It's the ideal "prove the template works" first mover.

2. The 10 intermediate domains (`feedback`, `artifact`, `file-lock`, `task`, `room`, `lesson`, `role`, `leadership`, `duty`, `recovery`) all currently have `DIRECT_CLI_BYPASS` with no domain-specific verification. Migrating them is a purely architectural change — routing through `ApplicationAPI.submit()` — with no behavior change if the gateway starts at `ASSERTED_ONLY`. Each one is independently testable.

3. `consensus` is migrated LAST because:
   - It's the ONLY domain with existing, shipped, tested domain-level credential verification (`CredentialVerifier` Protocol at `consensus.py:31-40`, invoked at lines 170-176 and 549-556).
   - Migration means REMOVING that domain-level code and replacing it with the gateway-level authorizer. If the gateway is wrong, this **regresses a real security control** rather than merely adding one.
   - Doing it last means the gateway has been proven on 10 other domains first — the risk of a gateway-level bug is much lower by that point.

This is a textbook "test on low-risk before touching high-risk" approach. **No design gap.**

### "Consensus is the only domain that also needs code removal, not just routing"

**Confirmed correct by code inspection.** I grep'd the entire `peerhub/` package — `credential_verifier` and `CredentialVerifier` appear only in:
- `governance/consensus.py` (definition, usage)
- `runtime.py` and `cli/__init__.py` (construction sites, where `verify_credential_for_actor` is wired in)

No other governance domain has internal verification logic. Every other domain migration is purely a routing change (CLI entrance → `Client.submit()` → `ApplicationAPI.submit()` → existing handler). Only consensus migration also requires removing the `CredentialVerifier` dependency from `ConsensusService.__init__` and the verification blocks from `cast_vote` and `final_call_ack`.

---

## Part 4: Potential Design Gaps

### Gap 1: `final_call_ack` not explicitly addressed

As noted in Q4.4 above, the converged design's migration plan discusses `consensus vote` and `proposal-vote` but doesn't explicitly mention `final_call_ack`, which also has credential verification at `consensus.py:170-176`. When the gateway subsumes consensus verification, both `cast_vote` AND `final_call_ack` need their internal verification removed. This is almost certainly intended but should be listed explicitly.

### Gap 2: `consensus arbiter-review` not classified in migration plan

The call map includes `consensus arbiter-review` as a `MUTATING` command with `DIRECT_CLI_BYPASS`. The converged design's migration plan says "Last: `consensus`/`proposal-vote`" — does `arbiter-review` fall under the consensus domain's migration step? It should, since it's a consensus-domain mutator, but the migration plan's explicit listing (`proposal-vote`, `vote`) might be read as exhaustive. Recommend listing all consensus-domain commands explicitly.

### Gap 3: No explicit statement about `authorizer_behavior` for `NOT_APPLICABLE` validation

The `NOT_APPLICABLE` value is defined for read/observation commands. But some commands are `CONDITIONAL_MUTATION` or `CONDITIONAL_MUTATION_EXTERNAL` (e.g., `status` can refresh telemetry, which is a write). Should `authorizer_behavior` apply to the read path, the write path, or both? For conditional commands, the call map should probably record the *maximum* enforcement level (the behavior when the mutation path is taken).

### Gap 4: The "ratified doc" (sections 7.1, 7.2) is not verifiable from this checkout

This is a process gap, not a design gap. If the ratified doc is the design authority cited by both the call map and cc's review, it should be committed alongside the other design docs so future reviewers can verify citations. Currently, the call map references it at `schema_version: 1` line 4, and cc cites specific section numbers, but the file is absent.

---

## Summary

The converged design is **sound, well-reasoned, and well-cited**. Its enforcement shape is a direct, correct generalization of an already-shipped pattern. The migration order is defended by real evidence (consensus is genuinely the only domain with existing verification to protect). The `authorizer_behavior` enum is clean and mechanically checkable.

**Corrections needed:**
- Explicitly list `final_call_ack` and `arbiter-review` in the consensus migration step.
- Add a note that `authorizer_behavior` is a per-command *static capability* declaration, not a per-invocation runtime state.
- The ratified holistic-renewal doc should be committed or cross-referenced so future reviewers can verify sections 7.1 and 7.2.

**No blocking design gaps were found.** The 4 items in Part 4 are clarifications, not architectural problems. The design is ready for cx's review.
