# Batch 09 (FINAL) — LegacyTranslator Deletion & Wire-Contract Preservation

> LegacyTranslator retirement, batch 9 of 9 (FINAL).
> Baseline: `main` (after batch 8h, commit `43994d4`).
> This concludes the entire 9-batch retirement effort.

## Summary

Batch 8 (sub-batches 8a-8h) rewrote every `LegacyTranslator`-using test
node in `tests/integration/`, reducing all production callers to zero.
This final batch:

1. Created `tests/unit/application/test_command_wire_contracts.py`,
   preserving the wire-protocol contract knowledge (`.method` +
   `.encode_params()` shapes) originally exercised indirectly through
   `LegacyTranslator` in 4 unit test files, now exercised via direct
   native `Command` construction.
2. Deleted `LegacyTranslator`, `LEGACY_CATALOG`, and their exclusively
   translator-only supporting symbols from `peerhub/application/legacy.py`.
3. Deleted the 5 unit test files that exercised `LegacyTranslator`/
   `LEGACY_CATALOG` directly.
4. Updated `docs/design/phase0/migration-ledger-v2.json`'s status note
   to reflect the ledger's new historical (non-authoritative) status.
5. Fixed a stale `LEGACY_CATALOG` docstring reference in
   `peerhub/governance/feedback.py`.

## Retired Test Node Inventory

| Original Node | Disposition |
|---|---|
| `test_legacy_consensus.py::test_consensus_legacy_actions_translate_with_wire_params` | -> preserved in `test_command_wire_contracts.py::test_consensus_commands_wire_contracts` |
| `test_legacy_duty.py::test_duty_legacy_actions_translate_to_fenced_wire_commands` | -> preserved in `test_command_wire_contracts.py::test_duty_commands_wire_contracts` |
| `test_legacy_duty.py::test_leadership_legacy_actions_translate_to_workspace_global_params` | -> preserved in `test_command_wire_contracts.py::test_leadership_commands_workspace_global_params` |
| `test_legacy_duty.py::test_leader_actions_default_the_peer_to_unknown` | -> **retired outright, no replacement.** Tested `LegacyTranslator`'s own CLI-argument-parsing convenience: when `--agent`/`--peer` is omitted, the translator defaulted `peer_node_id`/`yielding_peer_id` to the literal string `"unknown"`. Neither `LeaderClaimCommand.peer_node_id` nor `LeaderYieldCommand.yielding_peer_id` has a native default (both are required fields; omitting either raises a real `TypeError` at construction, matching the same required-field pattern established in batch 3 for `ThreadNewCommand.subject`/`room_id`). This behavior had no wire-protocol contract and no independent value once the CLI translation layer is gone. Adding an artificial `= "unknown"` default to the native dataclasses to manufacture a replacement was explicitly considered and rejected: that would be a real, unrequested production behavior change (silently weakening a currently-required field for every real caller in the codebase), out of scope for this retirement. |
| `test_legacy_room.py::test_new_topic_translates_to_thread_create_wire_command` | -> preserved in `test_command_wire_contracts.py::test_new_topic_wire_contract` |
| `test_legacy_room.py::test_clear_room_translates_all_room_boundary_fields` | -> preserved in `test_command_wire_contracts.py::test_clear_room_wire_contract` |
| `test_legacy_room.py::test_thread_react_translates_all_reaction_fields` | -> preserved in `test_command_wire_contracts.py::test_thread_react_wire_contract` |
| `test_legacy_task_lesson.py::test_task_and_lesson_legacy_actions_translate_with_wire_params` | -> preserved in `test_command_wire_contracts.py::test_task_and_lesson_commands_wire_contracts` |
| `test_legacy_translator.py::test_legacy_catalog_matches_ledger` | -> **retired outright, no replacement.** Verified `LEGACY_CATALOG` (deleted) matches the ledger file -- a translator-internal consistency check between two now-nonexistent/non-authoritative artifacts, with no wire-contract value. |

All literal `method`/`encode_params()` expected values in
`test_command_wire_contracts.py` were transcribed verbatim from the
original assertions, never regenerated from the implementation.

## Symbols Deleted from `peerhub/application/legacy.py`

- `LegacyTranslator` (class)
- `LEGACY_CATALOG` (module-level dict)
- `LegacyActionCall` (class)
- `TranslatedCommand` (class)
- `InvalidLegacyArguments` (class)
- `KnownLegacyActionNotBacked` (class) -- identified during execution as translator-only support code not explicitly named in the batch plan; confirmed zero other callers repo-wide before deletion
- `UnknownLegacyAction` (class) -- same as above
- `_string_tuple`, `_optional_int`, `_int_or_zero`, `_bool_or_false`, `_optional_first_text`, `_optional_legacy_text`, `_first_text` (module-level helper functions, the same set already named in `tools/legacy_retirement/gate.py`'s `_TRANSLATOR_ONLY_HELPER_FUNCTIONS`)

Total: `82 -> 76` top-level classes in the file (6 removed), plus the 7
helper functions and the `LEGACY_CATALOG` constant.

## Symbols Confirmed Kept (and why)

- **`legacy_room_id`** (renamed from `_legacy_room_id` during this
  batch's verification -- see Correction below): resolves a room ID
  from legacy CLI arguments/context/scope. Called directly (not through
  the translator) by 3 tests in `tests/integration/test_stage2_boundary.py`
  (`test_legacy_status_resolves_explicit_room_argument`,
  `test_legacy_status_resolves_nested_context_and_submission_scope`,
  `test_legacy_status_rejects_empty_room_context_without_fallback`,
  the last case rewritten during batch 8e specifically to preserve
  coverage of this function's empty-return branch). Its own internal
  implementation was inlined (previously delegated to `_optional_first_text`,
  now deleted) via a local closure replicating identical logic -- verified
  behavior-preserving by direct comparison against the original.
- **`legacy_thread_slug`**: a real, independently-used production helper
  (`peerhub/cli.py`), unrelated to the translator's retirement; untouched.
- **All `*Command` dataclasses** (`ConsensusProposeCommand`,
  `ThreadReactCommand`, `TaskCheckpointCommand`, etc., 76 classes total):
  permanent native wire-protocol command types used throughout the
  entire test suite and production code. Only the translator that used
  to construct them from legacy CLI arguments is retired, not the
  commands themselves.

## Correction Made During This Batch's Verification: `_legacy_room_id` Rename

After the dispatched rewrite completed, an independent pyright run (per
standing practice: never trust a dispatch's own "done" claim without
re-verifying) surfaced a real, new error:
`reportUnusedFunction: Function "_legacy_room_id" is not accessed`.

Root cause: pyright's unused-function check for a leading-underscore
("private-by-convention") name is scoped to same-file usage. Before this
batch, `_legacy_room_id` was called from within `LegacyTranslator
.translate()`'s own body (same file), so it was never flagged. Once
`translate()` was deleted, its only remaining callers were in
`tests/integration/test_stage2_boundary.py` (a different file) -- cross-
module usage that pyright's private-symbol heuristic does not count.

Since the function genuinely has real external callers (it is not
actually private/unused), the correct fix is a rename, not a suppression
comment: renamed `_legacy_room_id` -> `legacy_room_id` (dropping the
leading underscore), matching the existing convention already used by
the file's other genuinely-cross-module helper, `legacy_thread_slug`.
Updated the one definition site and all 5 reference sites (1 import + 4
call sites) in `test_stage2_boundary.py`. Re-verified: pyright 0 errors,
full suite still 1475 passed after the rename.

## Verification

- **Grep for zero remaining references** (`LegacyTranslator`,
  `LEGACY_CATALOG`, `LegacyActionCall`, `TranslatedCommand`,
  `InvalidLegacyArguments`, `KnownLegacyActionNotBacked`,
  `UnknownLegacyAction`) outside `tools/legacy_retirement/`: confirmed
  zero real code references. The only remaining hits are a gitignored,
  untracked scratch file (`.peerhub/scratch/compare_cov.py`) and a
  historical-reference sentence in `test_command_wire_contracts.py`'s
  own module docstring (prose, not code).
- **Full suite**: 1477 -> 1475 passed (2 skipped, unchanged). Arithmetic:
  removed 9 collected nodes from the 5 deleted files (1+3+3+1+1), added 7
  new nodes in `test_command_wire_contracts.py`
  (`test_consensus_commands_wire_contracts`,
  `test_duty_commands_wire_contracts`,
  `test_leadership_commands_workspace_global_params`,
  `test_new_topic_wire_contract`, `test_clear_room_wire_contract`,
  `test_thread_react_wire_contract`,
  `test_task_and_lesson_commands_wire_contracts`); net delta -2,
  1477 - 2 = 1475. Confirmed.
- **Pyright**: 0 errors, 0 warnings, 0 informations (after the
  `legacy_room_id` rename correction above; 1 error before it).
- `tools/legacy_retirement/gate.py` was intentionally NOT run for this
  batch -- it has no baseline meaning once the class it gates is
  deleted. `tools/legacy_retirement/` (gate.py, selftest.py, and the
  `docs/reviews/legacy-translator-retirement/` batch records) remain in
  the repo untouched, as historical/reusable tooling and documentation.

## Ledger & Docstring Updates

`docs/design/phase0/migration-ledger-v2.json`'s `meta.note`:
- Before: `"Authoritative migration ledger v2"`
- After: `"Historical record of the legacy CLI action -> peerhub command mapping. No longer authoritative: the LegacyTranslator that implemented this mapping was fully retired in 2026-09-09; all listed legacy actions now have permanent native Command equivalents, exercised directly in tests/unit/application/test_command_wire_contracts.py."`

`peerhub/governance/feedback.py`'s module docstring: dropped the dangling
`` `LEGACY_CATALOG` `` reference from the sentence describing the
`governance.feedback.*` method namespace placement rationale; the
placement rationale itself (co-location with `LessonService`/
`ConsensusService`) is unaffected and remains accurate.

## Conclusion

This concludes the entire 9-batch LegacyTranslator retirement effort:

- Batches 1-6: rewrote 12 individual integration test files off
  `LegacyTranslator`.
- Batch 7: rewrote `test_broker_status.py` + `test_proposals.py`
  (consensus-adjacent), including a real mid-batch correction of a
  gutted test.
- Batch 8 (8a-8h): rewrote all 45 `LegacyTranslator`-using nodes in
  `tests/integration/test_stage2_boundary.py`, across 8 sub-batches,
  with 2 gate-tool extensions (inline `InvalidLegacyArguments`
  comparisons, direct result-field comparisons) and one real mid-batch
  quality correction (tautological default-value assertions).
- Batch 9 (this batch): preserved wire-contract knowledge natively,
  deleted `LegacyTranslator`/`LEGACY_CATALOG` and all exclusively
  translator-only supporting code, deleted the 5 now-obsolete unit test
  files, and updated the migration ledger's status.

`peerhub/application/legacy.py` now exclusively hosts native `Command`
dataclasses and two genuinely-shared helper functions
(`legacy_room_id`, `legacy_thread_slug`) -- zero translator machinery
remains anywhere in the codebase.
