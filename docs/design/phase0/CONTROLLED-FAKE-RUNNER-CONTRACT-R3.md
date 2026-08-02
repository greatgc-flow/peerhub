# Controlled-Fake Runner Contract R3

Status: ratified 2026-07-30 (see `RATIFICATION-PROVENANCE-INDEX-R1.md`'s `session-2026-07-30-dp06-r3-track` row),
resolving R3 backlog item #1 from `DP06-DT01-DT06-CLASSIFICATION-SPEC-R1.md`
only. Produced by an independent-verification round between ag.deepthink and
cx.deepthink (2026-07-30), reconciled by cc. Does not resolve backlog items
#2-15, does not authorize Phase 0 exit, cutover, or any production
implementation.

## Why this document exists

`DP06-DT01-DT06-CLASSIFICATION-SPEC-R1.md` (2026-07-29) concluded, after a
genuine 3-round adversarial critique, that R2's own prose alone could not
establish where the dispatch boundary sits relative to `INTENT_PERSISTED`,
journal append, and reduction -- two readings ("pre-dispatch crash, no
process ever ran" vs. "post-dispatch, pre-observation crash") both
technically satisfied R2's text, leaving DP-06 at `PENDING_FAITHFUL_MAPPING_
REVIEW`. That finding is not overturned here. What this round adds is
evidence the prior critique did not cross-reference: other already-ratified
documents in the same corpus state the answer directly.

## Evidence (independently verified by cc against the actual files, not
taken on either peer's claim alone)

- `docs/design/phase0/fixtures/CONTRACT.md` (the same MUST-tier scope
  document every other fixture this session was built against) states, for
  DP-06 itself: "Crash after dispatch intent becomes MAY_HAVE_STARTED/UNKNOWN
  and is not automatically replayed."
- `docs/design/ARCHITECTURE.md` section 14: "post-`DISPATCH_INTENT` crash is
  `MAY_HAVE_STARTED`."
- `docs/design/phase0/V1-CONTROLLED-FAKE-CONFORMANCE-SPEC-R1.md` (R2's own
  named predecessor, explicitly retained as "historical design input" by
  R2 section 7 -- only its ambiguous event *names* were superseded, not its
  substantive mappings): "DP-06 | `INTENT_PERSISTED` then injected runner
  crash | recovery is `MAY_HAVE_STARTED`; no automatic replay; journal digest
  retained."
- `docs/design/phase0/PROTOCOL-V1-FREEZE.md` (the ratified v1 protocol, round
  `r-aec7`, unanimous, that this whole Phase 0 evidence effort exists to
  produce conformance evidence for): defines `PRE_SPAWN`/`POST_SPAWN` as
  distinct phases; states "`START_UNCERTAIN` and post-spawn timeouts are
  `MAY_HAVE_STARTED`"; states state/receipt/outbox commits happen in one
  transaction that "does not wait on process, provider, filesystem, or
  network I/O" (commit and dispatch are architecturally separate); states
  "Ambiguous external effects remain `MAY_HAVE_STARTED`/`UNKNOWN` until
  reconciled; they are never blindly replayed."

Both peers independently confirmed these citations are real (cc verified
each one directly against the files) and that, read together, they
establish reading 2 (`START_UNCERTAIN` / `MAY_HAVE_STARTED` / `UNKNOWN`) as
the intended, system-wide answer -- not an arbitrary fixture-authored
policy. Where the peers diverged: ag held that this evidence alone already
fully resolves DP-06 as a matter of architectural precedent; cx held that
architectural precedent cannot silently amend a separately hash-bound
runner contract, and that an explicit R3 rule is still required because R2
never cites `PROTOCOL-V1-FREEZE.md` or the retained conformance spec as a
dependency. cc adopts cx's more conservative procedural position (write the
explicit rule) while agreeing with ag's substantive conclusion (the answer
itself is not in genuine doubt) -- the two positions are complementary, not
contradictory.

## Ratified amendment

This section amends `CONTROLLED-FAKE-RUNNER-CONTRACT-R2.md` by adding a new
section 3.1 (Dispatch-intent recovery boundary), narrowly scoped to resolve
only R3 backlog item #1:

> ### 3.1 Dispatch-intent recovery boundary
>
> Durable journal append of an `INTENT_PERSISTED` record establishes that
> the attempt has crossed the dispatch-intent replay-safety boundary named
> in `fixtures/CONTRACT.md`'s DP-06 line. Event reduction is a projection of
> that durable fact and does not itself move the boundary; the boundary is
> the append, not the reduction.
>
> If a simulated interruption occurs after that append but before reduction
> (a supported test input per section 3), and the journal contains no later
> `SPAWNED`, `EXIT`, or other terminal evidence, the reducer MUST classify
> the recovered attempt as:
>
> - `terminal_classification = START_UNCERTAIN`
> - `effect_certainty = MAY_HAVE_STARTED`
> - `execution_outcome = UNKNOWN`
>
> The reducer MUST NOT classify that attempt as `NOT_STARTED`, and recovery
> MUST NOT automatically replay the possibly-already-dispatched attempt (per
> section 3's existing rule). `MAY_HAVE_STARTED` does not assert that a
> process actually started; it states that the durable recovery evidence
> cannot prove that it did not.
>
> An interruption proven (by the injected test script) to occur strictly
> before the durable append of `INTENT_PERSISTED` remains pre-boundary and
> is classified `NOT_STARTED` -- this section does not change that case.

## Scope and non-effects

This amendment resolves R3 backlog item #1 only. It does not resolve, and
takes no position on, backlog items #2-15 (out-of-order stream events,
terminal-event precedence beyond the existing cleanup MUST, `TREE_STATE`
semantics, `CANCEL_ACK` schema, the idempotency binding schema, unterminated
scripts, parse/version/schema-negotiation failure classification, exit-code
domain, clock validation, full enum closure, `MAY_HAVE_STARTED` vs
`UNKNOWN` distinction, multiple `CLEANUP_ERROR` handling, cleanup-degrading-
success, and identity-reuse handling). Each remains open backlog for a
future ratification round, deliberately kept out of scope here to avoid
overclaiming this narrow amendment settles more than it does.

This amendment does not touch DT-01 or DT-06 (already resolved and
implemented in a prior round) and does not authorize Phase 0 exit, cutover,
or production implementation.
