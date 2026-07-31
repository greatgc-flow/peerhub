# Post-Phase-0 Roadmap R1

Status: draft planning document, not a ratification record. Written by cc
after Phase 0's full closure (2026-07-31, commit `af8cab8`), summarizing
the frozen state and laying out options for what comes next. Does not
itself authorize any of the paths below -- each remains gated by its own
future decision.

## Where things stand

Phase 0 (controlled-fake, no-real-provider evidence work) is complete and
frozen: 54/54 fixtures `SPEC_FAITHFUL`, 266/266 tests, all 10 defects
found across three review passes fixed and peer-ACKed, evidence hash-bound
end-to-end (`source-evidence-manifest-v1.json` v2, 597 files). See
`FINAL-CROSS-REVIEW-REMEDIATION-R1.md` and
`RATIFICATION-PROVENANCE-INDEX-R1.md` for the full ledger.

Nothing here is broken. Everything below is optional forward work, not a
punch list of bugs.

## Remaining backlog (named, not started)

1. **R3 backlog #2-15** (`DP06-DT01-DT06-CLASSIFICATION-SPEC-R1.md`) --
   13 edge-case items: out-of-order stream events, terminal-event
   precedence, `TREE_STATE`/`CANCEL_ACK` schemas, idempotency binding,
   unterminated-script classification, parse/version/schema-negotiation
   failure classification, exit-code domain, clock validation, enum
   closure, `MAY_HAVE_STARTED` vs `UNKNOWN`, multiple `CLEANUP_ERROR`
   handling, cleanup-degrading-success, identity-reuse.
2. **SL-01-06 spec's own 5 OPEN items** -- full SL-06 recovery-policy
   matrix (trigger->decision mapping), `lease_kind` taxonomy,
   process-bound vs principal-bound leases, SL-03's reject-vs-replan
   default, session-binding self-corruption (needs its own quarantine
   path).
3. **HR-03's classification-level scope mapping** for 4 of 7
   classifications (`EXECUTABLE_UNAVAILABLE`/`ENVIRONMENT_UNAVAILABLE`/
   `AUTH_UNAVAILABLE`/`NETWORK_UNAVAILABLE`) -- currently resolved only
   per-fixture-row via fact injection, not as a general rule.
4. **Named-but-unratified items in the AC track**: the static 90-action
   inventory ratification, action-fixture linkage, public error codes,
   safe-abort classification, rollback vocabulary, real-OS integration
   (`authority-proof-status-v1.json`).
5. **"No real package implementation authorized"** -- `RUNTIME-HEALTH-
   SEMANTICS-R1.md` and siblings still say this. Lifting it is a real
   decision, not something that should happen by silent drift.
6. **Phase 0 exit / cutover execution** -- blocked on essentially
   everything above plus a real test suite (TDD conditions 3-4).

## Three paths forward

### Path A -- Stay frozen (current default, no action needed)
Do nothing further. All backlog items are genuinely deferrable: none of
them threaten the 54 already-ratified fixtures, and none compound in
risk by sitting untouched. Appropriate if there's no near-term need to
extend Phase 0 evidence coverage or begin real implementation.

### Path B -- Begin real TDD implementation
Lift item 5's freeze via an explicit ratified decision (not silent
continuation), then start writing real source packages against the 54
`SPEC_FAITHFUL` fixtures as spec, following the project's stated
TDD-first workflow rule. This is the natural "next phase" per the
project's own gate structure (`TDD-READINESS-GATE-R1.md`), but is a
large scope commitment and should get its own planning pass, not be
folded into this roadmap.

### Path C -- Close out the smaller backlog first
Work through items 1-4 above (all bounded, all already-scoped design
questions, similar in size to what this session's HR-03/SL-01 work
looked like) before considering Path B. Lower risk per unit of work than
B, but doesn't move toward the project's actual goal (a real
implementation) by itself.

## Recommendation

No strong recommendation is made here -- this is a genuine user decision
(altitude: direction, not implementation detail), not something to
default without asking. Path A costs nothing and is always safe to stay
in. Paths B and C are not mutually exclusive with A (either can start
whenever) but represent real scope commitments that should be entered
deliberately.

## Process note for whichever path is chosen

This session's arc (2026-07-29 through 2026-07-31) repeatedly found that
"the original round already checked, nothing more to find" undersold what
a fresh document sweep or an independent second peer audit could still
catch (DP-06, CJ-01..06, RT-03, HR-03's policy gap, and the final
stale-capture defect were each found this way). Whichever path is chosen
next should keep using two independent peer passes for any genuinely
ambiguous design decision, plus cc's own direct verification of the most
consequential claims before committing -- this discipline is what caught
every real defect this arc, including in its own closing round.
