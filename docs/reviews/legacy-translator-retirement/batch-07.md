# Batch 07 — test_broker_status.py + test_proposals.py Preservation Record

> LegacyTranslator retirement, batch 7 of 9 (consensus-adjacent).
> Baseline: `main` (after batch 6, commit `5fe1fa5`).
> Gate result: PASS, 0 failures (`tools/legacy_retirement/runs/batch-07/report.md`).

## Summary

Rewrote both files' `LegacyTranslator`-based tests to construct typed `Command`
objects directly and submit them through the same real application-API boundary.

- `test_broker_status.py::test_broker_status_legacy_translation`: now constructs
  `EffectStatusCommand` directly, submits via `Client(runtime.application_api)`,
  and separately verifies the out-of-range-limit rejection path as a real native
  execution-time validation (see correction note below).
- `test_proposals.py::test_legacy_translation_for_both_actions_executes`: now
  constructs `ProposalAddCommand`/`ProposalVoteCommand` directly, submits each
  via `Client(runtime.application_api)`.

All other test nodes in both files were already native and untouched.

## Consensus-Assertion Preservation (mandatory check for this batch)

`test_proposals.py`'s rewritten function does not touch `counted_votes`,
`recorded_votes`, `decisive_votes`, or escalation state at all -- it only
constructs and submits the two commands, then asserts `vote_outcome.result["choice"]
== "agree"` (unchanged from baseline). Confirmed via the gate's own report:
zero occurrences of `counted_votes`/`recorded_votes`/`decisive_votes` anywhere
in the assertion-change list. The already-ratified four-choice-vote semantics
(`test_all_four_vote_choices_have_exact_quorum_accounting` and siblings,
untouched by this batch) remain exactly as they were.

## Correction Made During This Batch: test_broker_status.py Was Nearly Gutted

An earlier attempt at this batch's `test_broker_status_legacy_translation`
retired ALL of its original assertions (including the out-of-range-limit
rejection check, `isinstance(invalid, InvalidLegacyArguments)`) and replaced
the entire function body with `command = EffectStatusCommand(_submission(),
limit=2); _ = command` -- a command constructed and immediately discarded,
with zero real assertions and zero API submission. This was caught before
commit (the gate's mechanical check does not evaluate whether a translator-
shape waiver's underlying BEHAVIOR has a real replacement, only whether the
specific removed assertion text matches a recognized translator-shape
pattern -- a discarded-value test with no assertions at all can still pass
the gate cleanly if every individual removed assertion happens to be
waivable).

Investigated whether a native equivalent exists for the retired
out-of-range-limit rejection: `peerhub/application/broker_status.py:22`'s
`collect_effect_status()` independently enforces the SAME `1 <= limit <= 20`
range at execution time (`MAX_VISIBLE_EFFECT_DELIVERIES = 20`), raising a
`ValueError` on violation. Empirically verified (not assumed) how this
propagates through the real API: `client.submit()` does NOT let the
`ValueError` escape as a raised exception -- it's caught and surfaced as a
`CommandFailure` with `error.code == ErrorCode.INVALID_PARAMS` and
`error.message == "Invalid parameters: limit must be an integer between 1 and 20"`.

Fixed the test to: (1) submit `EffectStatusCommand(limit=2)` through the real
API and assert `CommandSuccess`, matching every other batch's pattern
(the earlier attempt never submitted anything at all), (2) restore the
`command.method`/`command.limit` field assertions on the directly-constructed
command, (3) verify the out-of-range case via the real, empirically-confirmed
`CommandFailure`/`ErrorCode.INVALID_PARAMS` shape instead of discarding it.

## Per-Node Assertion Table (AST-derived)

| # | Test Node | Before | After | Notes |
|---|-----------|:---:|:---:|-------|
| 1 | `test_broker_status.py::test_effect_status_sqlite_page_fields_order_counts_and_has_more` | 12 | 12 | Untouched |
| 2 | `test_broker_status.py::test_effect_status_zero_unfinished_effects` | 6 | 6 | Untouched |
| 3 | `test_broker_status.py::test_broker_status_legacy_translation` | 5 | 6 | **Rewritten** (5 translator-shape assertions retired, 6 real assertions added -- construction fields, real `CommandSuccess`, and the real `CommandFailure`/`ErrorCode.INVALID_PARAMS` rejection path) |
| 4 | `test_broker_status.py::test_cli_broker_status_json` | 4 | 4 | Untouched |
| 5 | `test_proposals.py::test_legacy_translation_for_both_actions_executes` | 8 | 4 | **Rewritten** (4 translator-shape assertions retired, all real execution assertions -- `CommandSuccess`, `round_id` type, vote `choice == "agree"` -- preserved byte-identical) |
| | Other 40+ untouched nodes across both files | -- | -- | Unchanged, including all consensus/quorum-related tests |

## Retired Assertions

See `docs/reviews/legacy-translator-retirement/gate-canary-waivers-batch07.json`
(9 entries) for the full waiver list. Every retired assertion is a translator-
shape check (`isinstance(x, TranslatedCommand)`, `isinstance(x.command, Type)`,
a field-mapping check duplicated by an equivalent new assertion, or the
out-of-range-limit rejection-shape check discussed above -- which now has a
real, stronger replacement rather than no replacement at all).

## Coverage Delta

Zero lost lines, zero lost branches, across all production modules touched.

## Gate Command Used

```bash
python tools/legacy_retirement/gate.py --baseline main \
  --output tools/legacy_retirement/runs/batch-07 \
  --waivers docs/reviews/legacy-translator-retirement/gate-canary-waivers-batch07.json \
  tests/integration/application/test_broker_status.py \
  tests/integration/application/test_proposals.py
```
