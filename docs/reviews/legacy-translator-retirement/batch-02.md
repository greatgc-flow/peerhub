# Batch 02 — test_file_locks.py + test_artifact_records.py Preservation Record

> LegacyTranslator retirement, batch 2 of 9.
> Baseline: `main` (after batch 1, commit `189910c`).
> Gate result: PASS, 0 failures (`tools/legacy_retirement/runs/batch-02-final2/report.md`).

## Summary

Rewrote both files' `LegacyTranslator`-based tests to construct typed `Command`
objects directly and submit them through the same real `Client(application_api)`
boundary the translator-based versions used.

- `tests/integration/test_file_locks.py::test_legacy_translation_locks`: file-lock
  acquire/status/release, now via `LockAcquireCommand`/`LockStatusCommand`/`LockReleaseCommand`.
- `tests/integration/test_artifact_records.py::test_all_three_legacy_actions_translate_and_execute`:
  artifact claim/status(draft)/status/finalize, now via `ArtifactClaimCommand`/
  `ArtifactStatusCommand`/`ArtifactFinalizeCommand`.

All other test nodes in both files (12 nodes) were already native and untouched.

## Gate Tool Extension Made During This Batch

Both target functions import `LegacyTranslator` at MODULE scope, not function-local
scope like batch 1's file did. `tools/legacy_retirement/gate.py`'s `translator_only()`
could not see a module-level import from a single function's re-parsed AST, so its
first (correct, fail-closed) response was to refuse every waiver in this batch. A
prior attempt tried to fix this by defaulting the alias set to `{"LegacyTranslator"}`
whenever no import was found locally -- this was caught as a real fail-open
regression (see `e16cdb8`), reverted, and locked in with a new negative selftest
(`test_unverified_import_scope_rejected`).

The properly-scoped fix (`189910c`): `test_ast()` now also extracts verified
module-level `LegacyTranslator` import aliases from the same parsed module tree
(never guessed), threaded through `compare()` into `translator_only()` as an
explicit `module_aliases` parameter. A matching positive selftest
(`test_verified_module_level_import_accepted`) confirms this works without
weakening the negative case. 12/12 selftests pass.

## Second Gate Tool Extension: Translator-Only Helper Function Coverage Scope

`test_artifact_records.py`'s original test exercised `_first_text()`/
`_optional_first_text()` indirectly (these are argument-parsing helpers called
from inside `LegacyTranslator.translate()`'s dispatch to pull `name`/`peer`/etc.
out of `call.arguments`). The rewrite no longer goes through `translate()` at
all, so these two functions' prior coverage would be lost -- but they are
module-level free functions, not part of the `LegacyTranslator` class body, so
the gate's original exclusion scope (class-body-only) did not cover them.

Verified via exhaustive repo-wide grep (not assumed) that `_first_text`,
`_optional_first_text`, and 5 sibling argument-parsing helpers
(`_string_tuple`, `_optional_int`, `_int_or_zero`, `_bool_or_false`,
`_optional_legacy_text`) have **zero callers anywhere except
`LegacyTranslator.translate()`'s own dispatch** -- they exist solely to serve
the translator and become genuinely dead once it is removed, same as the
class body. `_legacy_room_id` (an 8th similarly-shaped helper) was explicitly
NOT added to this list: it has its own direct unit test in
`test_stage2_boundary.py` (batch 9), so its exclusion is that batch's decision
to make together with that test's fate.

`production_coverage()` now also excludes these 7 named helper functions'
line ranges (verified present-and-unique via AST before use, raises if not).
This is the correct fix -- the alternative, which an earlier attempt at this
batch actually did, was leaving orphaned calls to these functions with
discarded return values in the test body purely to keep coverage numbers up.
That was reverted; no padding calls remain in the final test.

## Per-Node Assertion Table (AST-derived)

| # | Test Node | Before | After | Notes |
|---|-----------|:---:|:---:|-------|
| 1 | `test_file_locks.py::test_basic_lock_unlock` | 7 | 7 | Untouched |
| 2 | `test_file_locks.py::test_idempotent_re_lock` | 4 | 4 | Untouched |
| 3 | `test_file_locks.py::test_conflicting_owner` | 1 | 1 | Untouched |
| 4 | `test_file_locks.py::test_unstated_admin_override` | 2 | 2 | Untouched |
| 5 | `test_file_locks.py::test_unlock_ownership_mismatch` | 2 | 2 | Untouched |
| 6 | `test_file_locks.py::test_unlock_absent` | 1 | 1 | Untouched |
| 7 | `test_file_locks.py::test_legacy_translation_locks` | 11 | 8 | **Rewritten** (3 translator-shape assertions retired, real state assertions preserved) |
| 8 | `test_artifact_records.py::test_claim_is_sqlite_durable_and_same_owner_reclaim_preserves_claimed_at` | 9 | 9 | Untouched |
| 9 | `test_artifact_records.py::test_different_owner_is_rejected_until_finalized_then_can_reclaim` | 7 | 7 | Untouched |
| 10 | `test_artifact_records.py::test_draft_registration_updates_claim_and_rejects_unclaimed_name` | 4 | 4 | Untouched |
| 11 | `test_artifact_records.py::test_finalize_hashes_real_file_and_repeat_only_advances_timestamp` | 7 | 7 | Untouched |
| 12 | `test_artifact_records.py::test_status_queries_one_record_and_the_full_stable_list` | 5 | 5 | Untouched |
| 13 | `test_artifact_records.py::test_all_three_legacy_actions_translate_and_execute` | 16 | 8 | **Rewritten** (8 translator-shape assertions retired, all real state/outcome assertions preserved byte-identical) |
| 14 | `test_artifact_records.py::test_cli_executes_claim_status_draft_and_finalize` | 8 | 8 | Untouched |
| | **TOTAL** | **84** | **73** | 11 translator-shape assertions retired (waived), 0 real assertions lost |

## Retired Assertions (Translator-Only Waivers)

See `docs/reviews/legacy-translator-retirement/gate-canary-waivers-batch02.json`
for the full waiver list with individual justifications (11 entries: 3 for
`test_legacy_translation_locks`, 8 for `test_all_three_legacy_actions_translate_and_execute`).
Every retired assertion is an `isinstance(x, TranslatedCommand)` or
`isinstance(x.command, <CommandType>)` check on the translator's own return
shape -- none touch persisted state, service results, or real submission
outcomes. All real behavioral assertions (`locks[0].state[...]`,
`status_result.result[...]`, `client.submit(...).ok`, `record.state[...]`,
etc.) are preserved with byte-identical expressions.

## Coverage Delta

Zero lost lines, zero lost branches, across all production modules touched
(`tools/legacy_retirement/runs/batch-02-final2/report.md`'s coverage table is
empty because there were no changes to report -- confirming the helper-function
exclusion fix above worked as intended).

## Gate Command Used

```bash
python tools/legacy_retirement/gate.py --baseline main \
  --output tools/legacy_retirement/runs/batch-02-final2 \
  --waivers docs/reviews/legacy-translator-retirement/gate-canary-waivers-batch02.json \
  tests/integration/test_file_locks.py tests/integration/test_artifact_records.py
```

Both files must be passed to the SAME gate invocation when using one combined
waiver file -- the gate treats any waiver with zero matching removed
assertions in the run as invalid/unused and fails closed (confirmed: running
against a single file with the combined waiver file correctly fails with
"unused waiver" rather than silently ignoring the mismatch).
