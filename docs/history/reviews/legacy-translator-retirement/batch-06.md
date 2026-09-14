# Batch 06 — test_capability_matching.py Preservation Record

> LegacyTranslator retirement, batch 6 of 9.
> Baseline: `main` (after batch 5 and gate extensions, commit `b651a29`).
> Gate result: PASS, 0 failures (`tools/legacy_retirement/runs/batch-06/report.md`).

## Summary

Rewrote `tests/integration/application/test_capability_matching.py`'s `LegacyTranslator`-based test
to construct typed `Command` objects directly and submit them through the same real application-API boundary.

- `test_legacy_discover_and_elect_execute_through_command_api`: now constructs
  `DiscoverCandidatesCommand` and `ElectLeaderCommand` directly with typed arguments
  (`submission`, `needs`, `effort`, `actor_id`, `reason`), submitting each via `Client(runtime.application_api)`.

All other 5 test nodes in `test_capability_matching.py` were already native and untouched.

## Gate Extensions & Helper Function Analysis

This batch exercised the two gate enhancements ratified and merged in commit `b651a29`:
1. **Reused result names across sequential scenarios**: A test function reusing the same variable name
   (`translated`) for multiple sequential `LegacyTranslator().translate(...)` calls is now recognized
   when all assignments to that variable are verified translation calls.
2. **Duplicate-expression waiver disambiguation**: Multiple waiver entries with identical `(nodeid, expression)`
   (here, two occurrences of `isinstance(translated, TranslatedCommand)` within the same test function)
   are consumed sequentially without tripping ambiguous-match or over-provisioning errors.

Regarding argument-parsing helper functions:
`LegacyTranslator.translate()`'s dispatch for `discover` and `elect-leader` internally invokes
`_first_text()`. This helper was already included in `_TRANSLATOR_ONLY_HELPER_FUNCTIONS` in `gate.py`
(ratified in batch 2). Neither action exercises `_legacy_room_id()`, so no secondary test file was
needed for the gate invocation.

## Per-Node Assertion Table (AST-derived)

| # | Test Node | Before | After | Notes |
|---|-----------|:---:|:---:|-------|
| 1 | `test_capability_matching.py::test_importer_persists_real_targets_in_legacy_source_order` | 7 | 7 | Untouched |
| 2 | `test_capability_matching.py::test_discover_round_trips_through_real_sqlite_without_a_write` | 6 | 6 | Untouched |
| 3 | `test_capability_matching.py::test_elect_leader_commits_decision_before_claim_and_outcome_after` | 10 | 10 | Untouched |
| 4 | `test_capability_matching.py::test_rejected_and_exhausted_elections_still_have_both_audits` | 8 | 8 | Untouched |
| 5 | `test_capability_matching.py::test_legacy_discover_and_elect_execute_through_command_api` | 9 | 5 | **Rewritten** (4 translator-shape assertions retired, 5 real execution/result assertions preserved byte-identical) |
| 6 | `test_capability_matching.py::test_cli_import_discover_and_elect` | 6 | 6 | Untouched |
| | **TOTAL** | **46** | **42** | 4 translator-shape assertions retired (waived), 0 real assertions lost |

## Retired Assertions (Translator-Only Waivers)

See `docs/reviews/legacy-translator-retirement/gate-canary-waivers-batch06.json`
for the full waiver list with individual justifications (4 entries total).

- `test_capability_matching.py::test_legacy_discover_and_elect_execute_through_command_api`:
  1. `isinstance(translated, TranslatedCommand)`: Retire LegacyTranslator translation wrapper check on discover action.
  2. `isinstance(translated.command, DiscoverCandidatesCommand)`: Retire translator command-type check on discover action.
  3. `isinstance(translated, TranslatedCommand)`: Retire LegacyTranslator translation wrapper check on elect-leader action.
  4. `isinstance(translated.command, ElectLeaderCommand)`: Retire translator command-type check on elect-leader action.

All real behavioral assertions on command execution (`CommandSuccess`, candidate ranking `node_id == 'cx'`, leader selection `node_id == 'cx'`, and outcome `CLAIMED`) are preserved byte-identical.

## Coverage Delta

Exact coverage coordinates across all production files are 100% preserved. 0 lost lines, 0 lost branches.

## Gate Command

```bash
python tools/legacy_retirement/gate.py \
    --baseline main \
    --output tools/legacy_retirement/runs/batch-06 \
    --waivers docs/reviews/legacy-translator-retirement/gate-canary-waivers-batch06.json \
    tests/integration/application/test_capability_matching.py
```
