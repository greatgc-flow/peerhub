# DP-06 / DT-01 / DT-06 Classification Spec R1

Status: proposed evidence-scoping record. Produced by an unlimited adversarial
mutual-critique process between ag.deepthink and cx.deepthink (3 rounds,
2026-07-29), reconciled by cc, unanimous ACK from both peers on the final
disposition below. Does not amend `CONTROLLED-FAKE-RUNNER-CONTRACT-R2.md`,
authorize a cutover, or convert any status this document does not explicitly
name.

## Why this document exists

DP-06, DT-01, and DT-06 hold `V1_CAPTURE` status but were flagged
`PENDING_FAITHFUL_MAPPING_REVIEW` in `fixture-status-v1.json`: their `expect`
blocks were authored against `runner.py`'s own output, not against an
independent oracle -- the exact self-reported-script risk the whole
Domain-Oracle-Verifier framework (`DOMAIN-ORACLE-VERIFIER-CONTRACT-R1.md`) was
built to prevent. This document is the record of the process used to try to
close that gap, and its honest conclusion: **the gap only closes for two of
the three fixtures.**

## Process note (a finding in its own right)

Round 1 asked ag and cx to independently derive a classification spec from
`CONTROLLED-FAKE-RUNNER-CONTRACT-R2.md`'s prose plus the fixture `expect`
blocks as worked examples, without reading `runner.py`. ag's round-1 draft
read ~1150 lines of `runner.py`'s actual reducer implementation instead --
producing a fully-resolved-looking spec that was, in fact, a description of
the existing code, not an independent derivation. cx's round-1 draft stayed
independent and reached a materially different, more conservative result. ag
conceded the methodology violation in round 2 without reservation and adopted
cx's tagging framework. This is recorded because it is itself evidence for why
independent-oracle discipline matters in this project, not just a process
footnote.

## Authority tagging (adopted, both peers unanimous)

- `MUST` -- stated directly by R2's prose.
- `OBS` -- established by a specific fixture's `expect` block.
- `CANDIDATE` -- a plausible, reasonable completion, not entailed by R2 alone.
- `OPEN` -- no defensible independent rule exists yet; requires a future
  ratified R3 contract decision.

## Adopted MUST/OBS rule subset (safe to build an independent oracle on)

| Rule | Tier | Source |
|---|---|---|
| `SPAWNED(identity)` establishes that a process was observed to start. | OBS | DT-01, DT-06 |
| `CHUNK(bytes,t)` appends output bytes in order; does not itself change classification. | OBS | DT-01, DT-06 |
| `EXIT(code)` sets `terminal_classification = EXITED` if unset. | OBS | DT-01, DT-06 |
| `EXIT(code)` with `code == 0` yields `execution_outcome = SUCCEEDED`. | OBS | DT-01 |
| `EXIT(code)` with `code != 0` yields `execution_outcome = FAILED`. | OBS | DT-06 |
| Once a primary terminal result is set, `SPAWNED`/`EXIT` classification is not later revised. | MUST | R2 §4 ("cleanup evidence is attached to, and never overwrites, the primary terminal result") -- generalized only for the observed within-fixture case, not as a full precedence rule (see OPEN list) |
| `CLEANUP_ERROR` is appended as attached evidence and never overwrites the primary terminal result. | MUST | R2 §4, directly |
| `SILENCE_TIMEOUT` and `PROCESS_TIMEOUT` are distinct; `HARD_TIMEOUT` is retired. | MUST | R2 §4, directly (not exercised by DP-06/DT-01/DT-06, listed for completeness) |

This subset is sufficient to fully and independently reproduce **DT-01** and
**DT-06**'s existing `expect` blocks. Neither fixture's event sequence
(`SPAWNED -> CHUNK -> CHUNK -> EXIT(0)` for DT-01;
`SPAWNED -> CHUNK -> EXIT(1) -> CLEANUP_ERROR` for DT-06) touches any event
type or condition in the OPEN list below.

## DP-06: genuinely blocked, not resolved

DP-06's script is `INTENT_PERSISTED` with an interruption immediately after
journal append, expecting
`START_UNCERTAIN` / `MAY_HAVE_STARTED` / `UNKNOWN`. Both peers independently
confirmed this cannot be derived from R2 prose alone. Two logically distinct
readings both satisfy R2's text:

1. **Pre-dispatch crash**: intent was durably recorded, but the runner
   crashed before any external dispatch was attempted. No process ever ran.
2. **Post-dispatch, pre-observation crash**: persisting intent itself commits
   dispatch authority; the runner may have already dispatched before crashing,
   with no later event to confirm or deny it.

DP-06's `expect` block silently picks reading (2) as a conservative safety
policy. R2 never states where the dispatch boundary sits relative to
`INTENT_PERSISTED`, append, and reduction, so this is presently
**fixture-authored policy, not an R2 consequence** (cx's finding, ag's
independent confirmation in round 2).

**Disposition**: DP-06 remains `V1_CAPTURE` / `PENDING_FAITHFUL_MAPPING_REVIEW`.
Its `fixture-status-v1.json` note is updated to name this specific gap
(dispatch-boundary ambiguity) rather than a generic "pending review," so a
future reader knows exactly what would need to be ratified to close it.

## OPEN items (R3 backlog, not resolved here)

These require a future ratified amendment to `CONTROLLED-FAKE-RUNNER-CONTRACT-R2.md`
(a would-be R3) before any fixture whose classification depends on them can
get a genuinely independent oracle. Listed, not resolved:

1. Dispatch boundary relative to `INTENT_PERSISTED` (blocks DP-06).
2. Whether `SPAWNED` must precede `CHUNK`/`EXIT`, or either is
   self-authenticating start evidence on its own.
3. Terminal-event precedence beyond the cleanup-specific MUST (e.g. `EXIT`
   followed by `PROCESS_DEADLINE` -- first-wins, last-wins, or invalid?).
4. `TREE_STATE` schema and semantics, including whether a non-empty snapshot
   may synthesize spawn evidence.
5. `CANCEL_ACK` schema, payload, and effect (if any) on classification.
6. The idempotency binding schema (`client_id`/`command_type`/
   `idempotency_key`/`payload` was ag's implementation-derived guess, not an
   R2 requirement) and the `IDEMPOTENCY_HIT`/`IDEMPOTENCY_PAYLOAD_MISMATCH`
   effect_certainty/execution_outcome triples.
7. Classification for an unterminated script (no primary-triggering event
   reached before end of script).
8. Classification for parse/version/schema-negotiation failures (R2 orders
   these steps but never names their resulting classifications).
9. Exit-code domain: negative codes, signal termination, null codes.
10. Time/clock validation: monotonicity requirements, equal-timestamp
    handling, values before spawn/last output.
11. Full enumeration of `terminal_classification` and `effect_certainty`
    values (no authoritative closed list exists in R2 today).
12. Semantic distinction between `MAY_HAVE_STARTED` and `UNKNOWN`.
13. Multiple `CLEANUP_ERROR` events: counting, retention, ordering.
14. Whether cleanup failure can degrade `execution_outcome` when the primary
    result was a successful `EXIT(0)` (DT-06 cannot answer this -- its exit
    code is already 1).
15. Identity handling: duplicate tokens, conflicting `pid`/`birth`, token
    reuse across a tree.

## Disposition (unanimous, 2026-07-29)

1. A new oracle module is built for **DT-01 and DT-06 only**, using strictly
   the MUST/OBS rule subset above.
2. **DP-06 is not touched** -- it remains `PENDING_FAITHFUL_MAPPING_REVIEW`
   with its note updated to cite the dispatch-boundary gap by name.
3. The 15 OPEN items above are recorded as backlog for a future
   `CONTROLLED-FAKE-RUNNER-CONTRACT-R3` ratification round, not resolved by
   fiat in this document.
4. The remaining 32 fully-`LEGACY_CAPTURE` behavioral IDs (DP-01..05,
   SL-01..06, CR-01..06, CS-01..06, HR-01..03, RT-01..03, GB-02/06,
   CJ-01/03/04/06) are out of scope here and untouched.
