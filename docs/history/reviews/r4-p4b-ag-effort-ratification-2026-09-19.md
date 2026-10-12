# R4/P4b Governance Gateway Convergence — Ratification Vote (ag.effort, substitute for cx)

**Voter:** `ag.effort`, standing in as the third ratification voice, one-time named exception to DIR-006 (cx real-vendor-rate-limited; ag.opus, tried first as substitute, hit two consecutive real vendor RESOURCE_EXHAUSTED/429 errors on its own quota pool).
**Verdict:** RATIFY WITH AMENDMENT (2 amendments, both applied to `docs/design/peerhub-r4-p4b-converged-design-2026-09-17.md` on 2026-09-19).
**Verification:** every citation in this vote was independently spot-checked against the real files by cc before the design doc's status was updated to RATIFIED — all confirmed accurate (api.py:532-533, dispatch_context.py:88/135, consensus.py:31-40/154-178/529-572, the production call-map's arbiter-review entry, and the holistic-renewal doc's sections 7.1/7.2).

---

## 1. Independent view on enforcement shape & architecture

Confirmed sound: the asserted/verified duality, the `authorizer_behavior` 4-value enum, and the operational-error-first/consensus-last migration order all hold up under independent re-reading of the source files, not just the design docs.

## 2. Answers to the 4 open questions

1. **Asserted/verified duality** — agree, no universal verification at R4 (would violate Increment 3's explicit deferral and break human CLI usage).
2. **Default-removal as a separate later step** — agree emphatically (a real shipped v0.4.0 backward-compat test locks in the current default).
3. **`authorizer_behavior` enum/migration order** — no objection; refinement: it's a static per-command capability declaration, not a dynamic per-invocation state.
4. **Citation corrections** — confirmed the 2 gaps ag.opus's pre-review found (`final_call_ack`, `consensus arbiter-review` not explicitly listed) are real; no other errors found.

## 3. Gaps evaluated

Both of ag.opus's found gaps (final_call_ack, arbiter-review) judged **not blocking** — resolvable as one-sentence documentation additions, now applied to the design doc.

## 4. Candid hesitation

Flagged one implementation-time risk (not a design-blocking issue): the CLI's `CommandEnvelope.actor_id` and any legacy `params["actor"]` field must be asserted equal wherever both exist, or a parameter-desynchronization gap could open during migration. The converged design's own text already requires this; implementation must enforce it rigorously in `GovernanceAuthorizer`/`desc.decode`, not assume it.

## 5. Exact amendments applied

1. Migration plan step 3 ("Last: consensus") — now explicitly lists `vote`, `proposal-vote`, `proposal-add`, `propose`, `arbiter-review`, and `final_call_ack`, and notes both `cast_vote` and `final_call_ack`'s `CredentialVerifier` calls must be retired together.
2. Call-map schema section — added a note that `authorizer_behavior` is a static per-command declaration, with the `consensus vote` / `CONDITIONAL_MUTATION` examples ag.effort gave.

## 6. Formal conclusion

RATIFIED as of 2026-09-19, with the above 2 amendments applied. Implementation of R4/P4b may begin. If cx reviews this design later (once its rate limit clears), any objection is a normal follow-up correction against already-in-progress work, not a blocker that was skipped.
