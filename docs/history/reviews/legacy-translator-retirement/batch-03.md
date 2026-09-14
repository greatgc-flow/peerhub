# Batch 03 — test_thread_new.py + test_update_status.py Preservation Record

> LegacyTranslator retirement, batch 3 of 9.
> Baseline: `main` (after batch 2, commit `f32358c`).
> Gate result: PASS, 0 failures (`tools/legacy_retirement/runs/batch-03/report.md`).

## Summary

Rewrote both files' `LegacyTranslator`-based tests to construct typed `Command`
objects directly and submit them through the same real application-API boundary.

- `test_thread_new.py`: `test_thread_new_sqlite_round_trip_and_duplicate_is_legacy_noop`
  and `test_thread_new_legacy_translation_uses_slug_raw_subject_and_scope_room`,
  now via `ThreadNewCommand` (constructed directly, with `legacy_thread_slug()`
  called explicitly where the translator previously called it internally).
- `test_update_status.py`: `test_legacy_update_status_resolves_room_and_executes`
  (3 parametrized cases) and `test_legacy_update_status_round_trip_preserves_omitted_fields`,
  now via `UpdateStatusCommand`.

4 other test nodes in `test_thread_new.py` and 2 in `test_update_status.py` were
already native and untouched.

## Gate Tool Extensions Made During This Batch

Both files used `LegacyTranslator().translate(...)` -- a chained
instantiation+call in one expression, with no intermediate `translator`
variable, unlike batches 1-2's `translator = LegacyTranslator(); ...`
two-step form. `translator_only()` did not recognize this shape. The
implementer correctly stopped and proposed a capability extension rather
than working around it; verified safe and applied (`f32358c`):
`translator_only()`'s `results` detection now also accepts a direct chained
call whose own class-name identifier is in the same verified `aliases` set,
alongside the existing two-step form.

`test_thread_new_requires_topic`/`test_thread_new_requires_room_id` asserted
`translated == InvalidLegacyArguments(action=..., reason=...)` -- the
translator's own documented rejection shape for invalid legacy call
arguments, not previously recognized by `translator_only()`. Verified
(exhaustive grep) `InvalidLegacyArguments` is constructed only inside
`LegacyTranslator.translate()`'s own dispatch. Added recognition for
`outcome == InvalidLegacyArguments(...)` as a waivable pattern, gated on the
same explicit, AST-verified import-alias discipline as `LegacyTranslator`
itself (function-local or module-level, never guessed).

Both extensions have positive and negative selftests
(`test_chained_translator_call_accepted`, `test_unverified_chained_call_rejected`,
`test_invalid_legacy_arguments_comparison_accepted`). 15/15 selftests pass.

## Behavior Change: InvalidLegacyArguments -> Native TypeError

`test_thread_new_requires_topic` and `test_thread_new_requires_room_id`
originally asserted the translator's own custom rejection object with a
specific reason string (e.g. `"thread-new requires --topic"`). `ThreadNewCommand`
(`peerhub/application/legacy.py:473-479`) is a frozen dataclass with
`subject`/`room_id` as required, non-defaulted fields -- omitting either at
construction already raises a real `TypeError` from Python's own dataclass
machinery. The rewrite uses `pytest.raises(TypeError)` around the
construction call instead of comparing against the translator's custom
error shape.

This is a deliberate, disclosed behavior change, not an oversight: the
NEW test verifies a real native contract (required-parameter enforcement)
rather than the retiring translator's own bespoke validation and message
text. It does **not** verify the exact same error message string the old
test did -- that message was part of the translator's own user-facing
behavior, which is being retired along with it. If a caller depended on
that specific string, this is the place that dependency breaks; no such
caller was found (`LegacyTranslator`/`legacy.py` has zero production
callers, confirmed repeatedly this retirement effort).

## Methodology Note: Third-Party Coverage Verification

`peerhub/application/legacy.py`'s `_legacy_room_id()` (lines 76-99) is called
internally by `LegacyTranslator.translate()`'s `thread-new`/`update-status`
dispatch (among others) -- removing that call path from `test_thread_new.py`
initially showed as **lost coverage** when the gate was run against only the
2 batch files. `_legacy_room_id` was deliberately NOT added to `gate.py`'s
`_TRANSLATOR_ONLY_HELPER_FUNCTIONS` exclusion list in batch 2, because
(unlike the other 7 helpers) it has its own DIRECT unit test in
`tests/integration/test_stage2_boundary.py` (batch 9's file,
`_legacy_room_id(arguments, {"room": "room-newer"}) == "room-explicit"` etc.)
-- meaning it's treated as an independently-testable utility, not pure
translator-support.

Because the gate only measures coverage across whatever test files are
passed to one invocation, `test_stage2_boundary.py`'s separate coverage of
`_legacy_room_id` was invisible to a gate run scoped to just the 2 batch-3
files. Re-ran the SAME gate invocation with `test_stage2_boundary.py` added
to the file list (unmodified -- included purely to prove its coverage of
`_legacy_room_id` is unaffected by this batch, not because anything in it
changed) and confirmed **PASS, 0 failures, 0 lost coordinates**. Whether
`_legacy_room_id` and its direct test are themselves retired is still batch
9's decision to make, unchanged from before.

## Per-Node Assertion Summary

Full per-node table (75 nodes across all 3 files passed to this gate
invocation) is in `tools/legacy_retirement/runs/batch-03/report.md`. Only
the batch-3 target nodes changed:

| Test Node | Before | After | Notes |
|-----------|:---:|:---:|-------|
| `test_thread_new_sqlite_round_trip_and_duplicate_is_legacy_noop` | 12 | 10 | 2 translator-shape assertions retired |
| `test_thread_new_legacy_translation_uses_slug_raw_subject_and_scope_room` | 7 | 6 | 6 translator-shape assertions retired, 6 equivalent real assertions added on the directly-constructed command |
| `test_thread_new_requires_topic` | 1 | 0 | Translator rejection-shape assertion retired; replaced with `pytest.raises(TypeError)` (not an `ast.Assert` node, so not counted here -- see behavior-change note above) |
| `test_thread_new_requires_room_id` | 1 | 0 | Same as above |
| `test_legacy_update_status_resolves_room_and_executes` (x3 parametrized) | 6 each | 4 each | 2 translator-shape assertions retired per case, real assertions on `outcome.command.room_id` etc. preserved |
| `test_legacy_update_status_round_trip_preserves_omitted_fields` | 9 | 7 | 2 translator-shape assertions retired, all persisted-field assertions preserved byte-identical |

All other 65 nodes across the 3 files (including all of `test_stage2_boundary.py`): unchanged, 0 delta.

## Retired Assertions

See `docs/reviews/legacy-translator-retirement/gate-canary-waivers-batch03.json`
(19 entries) for the full waiver list with individual justifications. Every
retired assertion is a translator-shape check (`isinstance(x, TranslatedCommand)`,
`isinstance(x.command, <Type>)`, a field-mapping check duplicated by a new
equivalent assertion on the directly-constructed command, or the
`InvalidLegacyArguments` rejection-shape comparison discussed above).

## Coverage Delta

Zero lost lines, zero lost branches, across all production modules touched.

## Gate Command Used

```bash
python tools/legacy_retirement/gate.py --baseline main \
  --output tools/legacy_retirement/runs/batch-03 \
  --waivers docs/reviews/legacy-translator-retirement/gate-canary-waivers-batch03.json \
  tests/integration/application/test_thread_new.py \
  tests/integration/application/test_update_status.py \
  tests/integration/test_stage2_boundary.py
```

`test_stage2_boundary.py` is included ONLY for its coverage contribution to
`_legacy_room_id` (see methodology note above) -- it is not part of this
batch's rewrite scope and none of its own test nodes changed.
