# R4/P4b architecture convergence — 2-voice converged design (PENDING cx)

**Status: DRAFT, converged between ag.deepthink and cc; NOT DIR-006 ratified.** cx (both `effort` and `deepthink` tiers) hit a real, confirmed vendor usage limit during this round (`_sys/codex/health.json`, `rate_limit_state.reset_at = 2026-09-19T17:16:00`) and has not yet reviewed this design. Per explicit user instruction, this document records where the two available voices converged so the third review isn't done from scratch, but **implementation must not begin until cx has reviewed this and DIR-006 unanimous agreement (cc/ag/cx) is recorded**, matching this same project's precedent for D0.

Inputs: ag.deepthink's initial proposal (2026-09-17, produced to ag's own sandbox, not separately committed), `docs/design/peerhub-r4-p4b-independent-review-cc-2026-09-17.md` (cc's critique), and ag's response to that critique (2026-09-17, agreeing with all 3 corrections). This document is the synthesis of all three.

## Converged mechanism

**"The common verifier and exact grants"** = elevate the already-shipped, already-tested `verify_credential_for_actor()`/`resolve_actor_for_credential()` (`peerhub/persistence/dispatch_context.py`) from a consensus-specific dependency into a `GovernanceAuthorizer` injected once into `ApplicationAPI`, replacing the current stub check at `ApplicationAPI.submit()` (`peerhub/application/api.py` ~line 531-532, which today only does a near-tautological `caller.client_id == cmd.submission.client_id` — the code's own comment calls this "a skeleton").

**Enforcement shape, generalized from the already-shipped consensus pattern (`ConsensusService.cast_vote`'s `credential_id: str | None`), not invented new:**
- If the envelope carries a `credential_id`: it must verify against `envelope.actor_id` via the injected authorizer, or the gateway rejects with an authorization error. (Extends today's per-domain check to every domain, uniformly, at one place.)
- If no `credential_id` is present: the call remains a valid **asserted** actor claim, UNLESS the specific command/round has opted into `verified_required` (a per-domain, per-risk-tier policy decision, not a gateway-wide default) — matching the already-shipped `consensus propose --verified-required` shape exactly.
- **Exact grant** means: when verification does apply, the verified identity must equal the claimed `actor_id` exactly — no partial match, no downgrade. It does NOT mean every mutating command universally requires a credential starting with R4. That decision is explicitly deferred by the original design (`peerhub-dctx-proposal-1-2026-09-13.md` Increment 3: "Decide separately, with migration telemetry, whether asserted mutations remain supported, require `--allow-asserted-actor`, or are removed in the next major version").

**Why this resolves the "human CLI usage" question without a new concept:** a human at a terminal has no `dispatch_requests` admission row and therefore no credential to present — their calls are asserted, which stays valid for normal-risk work by design. No local-developer-credential mechanism is needed.

## Migration plan

1. **Template domain: `operational-error`** (minimal dependencies). Rewrite its CLI entrance to build a `CommandEnvelope` and call `Client.submit()` instead of instantiating `OperationalErrorService` directly. Update `docs/design/peerhub-production-call-map-R1.json`'s entry: `gateway` becomes `APPLICATION_API_GATEWAY` (was `DIRECT_CLI_BYPASS`); `authorizer_behavior` (new field, see below) reflects its actual enforcement, which for a first migration is expected to start at `ASSERTED_ONLY` (routing changes; enforcement behavior does not, until separately decided per-domain). A real integration test reaching `peerhub.cli:main` proves the migrated path still works.
2. **Subsequent domains, in order:** `feedback`, `artifact`, `file-lock`, `task`, `room`, `lesson`, `role`, `leadership`, `duty`, `recovery` — each following the exact same template: route through `ApplicationAPI`/`Client`, update its call-map entry, prove via a real integration test. No domain regresses relative to its current behavior at migration time (asserted calls that worked before keep working).
3. **Last: `consensus`/`proposal-vote`.** These already have real, shipped, tested domain-level verification (Increment 1). Once the gateway-level authorizer exists and is proven on the 10 domains above, `ConsensusService`'s own internal `CredentialVerifier` call can be retired in favor of the gateway doing it uniformly — this is the only domain where "migration" also means removing now-redundant domain-level code, done last specifically because it's the one place a mistake could regress an already-shipped security control.

**Explicitly out of scope for the migration step itself** (separately gated, per domain, evidenced by migration telemetry — not simultaneous with routing changes):
- Removing duplicate identity defaults (e.g. `proposal-vote --voter` defaulting to `"cc"` — tested and shipped in v0.4.0, `tests/integration/application/test_proposals.py::test_cli_proposal_vote_defaults_to_cc_when_voter_and_credential_both_omitted`).
- Deciding whether asserted mutations remain supported long-term, require an explicit opt-out flag, or are removed in a future major version (Increment 3's own deferred decision).

## Production call-map schema addition

Add a new typed top-level field per command, alongside the existing free-text `"authorizer"` (which continues to describe *how*, e.g. "GovernanceAuthorizer via verify_credential_for_actor"):

```json
"authorizer_behavior": "ASSERTED_ONLY" | "VERIFIED_WHEN_PRESENTED" | "VERIFIED_REQUIRED" | "NOT_APPLICABLE"
```

- `NOT_APPLICABLE`: read/observation commands.
- `ASSERTED_ONLY`: mutating command routed through the gateway, but no domain-specific `verified_required` policy exists yet (the expected state for most commands immediately after migration).
- `VERIFIED_WHEN_PRESENTED`: a credential is checked if given, but not required (matches consensus `vote` today without `--verified-required`).
- `VERIFIED_REQUIRED`: the domain/round has opted into mandatory verification (matches `consensus propose --verified-required` today).

This makes R4's exit evidence ("identical protection on all entrances") mechanically checkable per-command instead of inferred from routing alone — `gateway` and `authorizer_behavior` are independent facts, since a migrated command can legitimately still be `ASSERTED_ONLY`.

## Deferred to implementation time, not blocking design closure

- Exact CLI output-formatting approach through `Client.submit()`'s generic `CommandOutcome` vs. today's direct, domain-shaped return values (ag's uncertainty #2) — a per-domain decision made when that domain is actually migrated.

## What still needs cx's review before DIR-006

1. Does cx agree with the asserted/verified duality in "Converged mechanism," or does cx see a reason to require verification universally now?
2. Does cx agree default-removal and the asserted-support decision are separately gated, later steps?
3. Does cx have a concrete objection or refinement to the `authorizer_behavior` enum or the migration order?
4. Any citation-level correction cx finds by independently re-reading the same source files (`api.py`, `dispatch_context.py`, `consensus.py`, the ratified doc, the D-CTX proposal doc) that ag and cc did not catch.
