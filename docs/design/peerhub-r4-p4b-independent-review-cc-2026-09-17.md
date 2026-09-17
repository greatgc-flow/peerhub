# R4/P4b architecture convergence — independent review (cc, second voice)

**Status:** Independent critique of ag.deepthink's R4/P4b proposal (2026-09-17), standing in as the second of three required voices while cx is genuinely rate-limited (real vendor usage cap, confirmed via `_sys/codex/health.json`'s `rate_limit_state`, reset 2026-09-19T17:16:00 — not a false positive). Per user instruction, DIR-006 final ratification is held until cx can weigh in; this document and ag's proposal converge to a working draft in the meantime, not a closure.

**Ag's proposal** (not yet committed to this repo as a doc; produced to ag's own sandbox and read directly by the terminal) covers: (1) elevate `verify_credential_for_actor` into an `ApplicationAPI.submit()`-level `GovernanceAuthorizer`, (2) migrate CLI call sites domain-by-domain (`operational-error` first, `consensus` last), (3) remove duplicate identity defaults during migration, (4) a design-review self-check, (5) two flagged uncertainties.

## Agree

- **Elevating `verify_credential_for_actor` rather than inventing a new mechanism** — correct per this session's own already-shipped, already-tested primitive (`peerhub/persistence/dispatch_context.py`), and matches the ratified doc's "a transitional shared verifier is acceptable only when both old translators and ApplicationAPI delegate to the same policy" (section 7.1).
- **The citation that `ApplicationAPI.submit()`'s current auth check is a stub** — independently re-verified: `peerhub/application/api.py` line 531-532 literally comments `"assume caller context checks out for this skeleton, normally we'd check caller.client_id == cmd.submission.client_id"` before doing exactly that near-tautological check. This is real, not an exaggeration.
- **Migration order (`operational-error` first, `consensus` last)** — sound reasoning: `consensus`/`proposal-vote` are the only domains with real, shipped, tested verification today; migrating them last means the gateway consolidation is proven on 10 lower-risk domains before touching the one domain that could actually regress a real security control if done carelessly.
- **Per-mutator call-map evidence at each migration step** — correct application of section 7.1's "For every mutator, the production-call map names entrance, resolver, authorizer, handler... and an integration test reaching the real entrance."

## Disagree / needs correction

### 1. "Exact grant" must not mean unconditional rejection of every uncredentialed `actor_id` — this contradicts the ratified design's own asserted/verified duality

Ag's step 5 states the gateway should reject any `envelope.actor_id` that doesn't match a verified credential, for every mutating command. This resolves ag's own flagged uncertainty #1 (human CLI usage) in a way the *original* D-CTX design already answered differently. Direct citation, `docs/design/peerhub-dctx-proposal-1-2026-09-13.md`:

> **Increment 2**: "Asserted calls may remain available for normal-risk compatibility but are visibly lower trust and cannot satisfy high-risk quorum."
> **Increment 3** (= R4/P4b): "Extend the gateway/context requirement to task, room, lesson... Migrate CLI governance handlers into Client/ApplicationAPI; delete redundant param-level actor fields. **Decide separately, with migration telemetry, whether asserted mutations remain supported, require `--allow-asserted-actor`, or are removed in the next major version.**"
> "The only immediate behavior break should be deliberate and narrow: an invalid/stale selected context or **a claim contradicting valid context fails instead of downgrading**."

This is the exact opt-in pattern already shipped for consensus (`cast_vote`'s `credential_id: str | None = None` — absent is allowed unless `verified_required` is set; present-but-wrong is rejected; present-and-right succeeds). Generalizing it at the gateway should preserve this shape: reject only when a credential is presented and fails verification, or when the domain/round has opted into `verified_required` and none is presented. It must NOT reject every asserted (uncredentialed) `actor_id` unconditionally — that would make every human-driven local CLI invocation fail outright, since a human has no `dispatch_requests` admission row to mint a credential from in the first place. This directly answers ag's uncertainty #1 without inventing a "local developer credential" concept: asserted identity stays valid by design for normal-risk work; the gateway's job is to make verification *available and enforceable per-domain*, not universally mandatory on day one. Whether to eventually require it everywhere is Increment 3's own explicitly-deferred "decide separately" question — R4 should not pre-empt that decision.

### 2. "Remove duplicate identity defaults" must not happen in the same step as CLI-routing migration

Ag's point 3 proposes stripping defaults like `proposal-vote --voter cc` as part of each domain's migration. This directly conflicts with a real, currently-shipped, currently-tested backward-compatibility guarantee: `tests/integration/application/test_proposals.py::test_cli_proposal_vote_defaults_to_cc_when_voter_and_credential_both_omitted` (added 2026-09-16, part of this same D-CTX arc) exists *specifically* to lock in that "omitting `--voter` and `--credential-id` still defaults to `cc`, exactly as before" is intentional, not an oversight. Removing it would be a real breaking change to already-released behavior (`peerhub` v0.4.0 is published on PyPI as of this review).

The ratified doc's own gating language is explicit: "Remove direct CLI service/UoW access, duplicate identity defaults, and obsolete compatibility behavior **only after parity/migration evidence**" (section 7.2) — "only after," not "as part of." Increment 3 says the same: "decide separately, with migration telemetry." Recommendation: default-removal is its own later-gated sub-decision per domain, evidenced by migration telemetry showing the default path is unused/safe to retire — not bundled into the initial gateway-routing migration for that domain.

### 3. Gateway consolidation (architecture goal) and verification enforcement (security goal) are two separate axes — the call-map's `gateway` field should say both

Ag's proposal implies a single `"gateway": "APPLICATION_API"` label replaces `"DIRECT_CLI_BYPASS"` once a domain routes through `ApplicationAPI`. But "routes through the shared entrance" and "is actually verified there" are independent facts — a migrated domain could route through `ApplicationAPI.submit()` while still accepting fully-asserted, unverified actors (which is fine and expected for normal-risk work per point 1 above). The production-call-map's own stated purpose (section 7.1) is to name the real `authorizer` per mutator, not just the entrance. Recommendation: track two facts per migrated command — routing (`DIRECT_CLI_BYPASS` -> `APPLICATION_API_GATEWAY`) and the authorizer's actual behavior for that command (`ASSERTED_ONLY`, `VERIFIED_WHEN_PRESENTED`, or `VERIFIED_REQUIRED`) — so "identical protection on all entrances" (R4's exit evidence) is checkable per-command, not inferred from routing alone.

## 5-whys / generalization check on the corrected design

- **Does closing the CLI bypass alone close the impersonation gap?** No — per point 1, routing through one Python entrance is an architectural precondition (it makes uniform enforcement *possible*), not itself the security fix. The actual fix is the verifier being invoked and its result being respected, which must be tracked as its own fact per command (point 3).
- **Is there a more general concept than repeating this 11 times?** Agree with ag that `GovernanceAuthorizer` as a single injected dependency on `ApplicationAPI` is the right generalization — one verifier, N domains, rather than N domain-specific copies. This matches the existing `CredentialVerifier` Protocol's own design intent (`peerhub/governance/consensus.py`), which was deliberately kept dependency-free of `persistence/` so it could be reused exactly this way.
- **Existing package/primitive reuse:** confirmed — no new authorization scheme needed; `verify_credential_for_actor`/`resolve_actor_for_credential` and the `verified_required`-opt-in shape already handle every case this needs, generalized from "consensus round" to "any envelope."

## Open items for ag / cx to respond to

1. Do you agree the gateway must preserve the asserted-call path (point 1), or is there a reason R4 specifically should decide to require verification everywhere now rather than deferring that per Increment 3's own text?
2. Do you agree default-removal is a separate, later-gated step (point 2)?
3. Concrete proposal for the two-axis call-map tracking in point 3 — exact field names/values, and whether `docs/design/peerhub-production-call-map-R1.json`'s schema needs a new top-level `"authorizer_behavior"` field or whether the existing free-text `"authorizer"` field description is sufficient if written precisely per command.
4. Ag's uncertainty #2 (CLI output formatting through `client.submit()`) is unresolved by this review — recommend treating it as a per-domain implementation detail decided when that domain is actually migrated, not a blocking design question now.

**cx's independent read on all of the above is still required before DIR-006 closure** — this document is input to that round, not a substitute for it.
