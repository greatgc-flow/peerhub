# Narrow-Coverage Evidence Decision R1

**Status:** `cc` judgment record resolving a real disagreement between
`ag.effort` and `cx.effort` about what the controlled-fake runner's first
real executions mean for Phase 0 exit eligibility. Documentation-only;
does not itself authorize implementation or Phase 0 exit.

## What happened

`cx.deepthink` drafted event scripts for all 19 currently `V1_SPEC_ONLY`
fixture IDs; `cc` applied them and ran every one through the real,
committed, tested controlled-fake runner
(`tools/phase0_fixture_runner/`). All 19 produced a genuine,
non-fabricated `status: "V1_CAPTURE"` fixture record with real SHA-256
digests — this is not claimed evidence, it is measured.

`cx`'s own event scripts included honest `.notes.md` files for **16** of
the 19 (`DT-02, DT-03, DT-04, DT-05, HR-04, HR-05, HR-06, RT-04, RT-05,
RT-06, GB-01, GB-03, GB-04, GB-05, CJ-02, CJ-05`), each stating that the
runner's frozen 9-event vocabulary
(`CONTROLLED-FAKE-RUNNER-CONTRACT-R2.md`) cannot express the actual domain
behavior that fixture's own spec doc describes: health circuit-breaker and
backoff state, CAS transaction atomicity, routing weight calculation,
actor authorization, configuration-staleness replanning, and similar. Each
of these 16 scripts instead reuses `INTENT_PERSISTED`'s generic
`client_id`/`command_type`/`idempotency_key`/`payload` fields and the
runner's one generic idempotency-hit/mismatch mechanism — the same
mechanism a trivial fixture like `CJ-02` already exercises. The remaining
3 (`DP-06`, `DT-01`, `DT-06`) have no such note: they are plain
process-lifecycle scripts (spawn/stream/exit/cleanup, interrupt-recovery)
that sit squarely inside the vocabulary's designed scope.

(An earlier round miscounted this set as 13; `cx.effort` caught the error
— it is 16.)

## The question

Does a real, honest `V1_CAPTURE` execution — for a script that only
exercises generic plumbing, not the fixture's own described domain
behavior — make that fixture ID `phase0_exit_eligible: true`?

## Positions

- **`ag.effort`: Option B.** Accept the 16 as `V1_CAPTURE` with a
  permanent `NARROW_COVERAGE` qualifier. Reasoning: Phase 0 is a
  host-tooling/process-lifecycle/transcript-verification boundary, not the
  domain engine itself (which doesn't exist as code yet); a documented
  qualifier honestly scopes what was actually tested and satisfies
  measured-only-claims discipline.
- **`cx.effort`: Option C.** Keep `phase0_exit_eligible: false` for all 16
  and do not add generic vocabulary either — a generic `STATE_TRANSITION`/
  `CAS_RESULT` event would still be self-reported script input unless the
  runner independently modeled and verified the domain invariant, which
  would just relocate the same false assurance under a new name. A
  qualifier cannot make exit-eligible a behavior that was never observed;
  a later, separately ratified domain-specific verifier/adapter contract
  is required before any of the 16 can become `SPEC_FAITHFUL` and
  exit-eligible.

Both explicitly agreed not to bulk-flip `phase0_exit_eligible: true` right
now, for any of the 19, without further review.

## Resolution: cx's position (Option C)

`phase0_exit_eligible` is not a documentation label — it is the switch
that, once true for all 54 behavioral IDs, permits real PeerHub source TDD
to start using these fixtures as the acceptance tests that drive that
code (`TDD-READINESS-GATE-R1.md` condition 1). If a fixture's
`V1_CAPTURE` only proves that generic idempotency plumbing ran, a future
implementation of the actual health/routing/broker/command-authorization
logic could pass that fixture while its real domain behavior is wrong,
because the fixture never exercised that behavior. A `NARROW_COVERAGE`
qualifier documents the gap honestly but does not close it, and this
project's own corpus has repeatedly rejected exactly this shape of
claim (`LEGACY_CAPTURE`/`V1_SPEC_ONLY` are explicitly "non-exit evidence";
a passing design fixture "must not be represented as live quota
evidence" in the quota-pacing round). Option B is a defensible reading of
Phase 0's scope, but it optimizes for a documentation label rather than
for what `phase0_exit_eligible` actually gates downstream.

`ag`'s "host-tooling boundary" framing is correctly folded into the
resolution below, not simply overruled: DP-06/DT-01/DT-06's process-
lifecycle behavior genuinely is that host-tooling boundary, which is why
they are treated differently from the 16 domain-policy IDs.

## Disposition

- **`DP-06`, `DT-01`, `DT-06`:** their described behavior (spawn/stream/
  exit/cleanup lifecycle; interrupt-then-recovery uncertainty) maps onto
  events the R2 vocabulary was built to model, and their `V1_CAPTURE`
  transcripts genuinely exercise it. `evidence_status` is set to
  `V1_CAPTURE` in `fixture-status-v1.json`. `phase0_exit_eligible` is
  **not** flipped by this document alone; it still requires its own
  brief, separately recorded faithful-mapping confirmation, kept as
  follow-up work rather than bundled into this contested round.
- **The other 16 (`DT-02..05`, `HR-04..06`, `RT-04..06`, `GB-01/03/04/05`,
  `CJ-02/05`):** `evidence_status` is set to `V1_CAPTURE` (the execution
  is real and the digests are real) with an explicit new
  `coverage_scope: "NARROW_COVERAGE"` field. `phase0_exit_eligible`
  remains `false`. Reaching `true` requires a new, separately ratified
  domain-specific verifier/adapter contract — a previously unscoped Phase
  0 artifact that this decision newly identifies as required. Until that
  contract exists and produces its own faithful captures, these 16 IDs
  are blocked on work that does not yet exist, not merely on ratification
  paperwork.

## Known follow-up, deliberately not done here

`fixture-status-v1.json`'s top-level `v1_capture_status: "NOT_CAPTURED"`
field has no defined vocabulary in this file (unlike `evidence_status`,
which references `canonical_status_vocabulary`) and neither peer was asked
about it directly in this round. It is now imprecise -- 19 IDs carry real
`V1_CAPTURE` evidence -- but this document does not invent a replacement
value unilaterally. Left as a named open item for the next round that
touches this file's schema.

## Consequence for the Phase 0 plan

The prior assumption that the single controlled-fake runner (R2) would be
sufficient to produce exit-eligible evidence for all 19 pending IDs was
wrong. It is sufficient only for the process-lifecycle family (DP/DT). A
new workstream — designing and ratifying a domain-specific verifier or
adapter contract for the health/routing/broker/command-authorization
families — is now a known, required, and previously unscoped precondition
for Phase 0 exit.
