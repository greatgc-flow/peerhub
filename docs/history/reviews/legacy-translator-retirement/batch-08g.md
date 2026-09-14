# Batch 08g — test_stage2_boundary.py Lessons, Task Checkpoint & Ask Dispatch Preservation Record

> LegacyTranslator retirement, sub-batch 8g of 8 (overall batch 8 of 9).
> Baseline commit: `56f1ba3` (sub-batch 8f).
> Target: partial rewrite of `tests/integration/test_stage2_boundary.py` (5 lessons/task_checkpoint/ask_dispatch test nodes off `LegacyTranslator`).
> Gate result: PASS, 0 failures (`tools/legacy_retirement/runs/batch-08g/report.md`).

## Summary

Rewrote the 5 lessons, task checkpoint, and ask dispatch test nodes in
`tests/integration/test_stage2_boundary.py` off `LegacyTranslator`,
constructing typed `Command` objects directly and wrapping each in
`SimpleNamespace(command=...)` where the original assertion text needed
to survive unchanged.

The 5 rewritten test functions are:
1. `test_legacy_task_checkpoint_translates_and_executes` -> constructs `TaskCheckpointCommand` directly
2. `test_legacy_lesson_propose_translates_and_executes` -> constructs `LessonProposeCommand` directly
3. `test_legacy_lesson_broadcast_translates_and_delivers_to_room_members` -> constructs `LessonBroadcastCommand` directly
4. `test_legacy_lessons_list_translates_and_executes` -> constructs `LessonsListCommand` directly
5. `test_legacy_translation_ask` -> constructs `SubmitDispatch` directly and preserves `out.command.method == "dispatch.submit"`

All other 56 test functions (76 parameterized invocations) in
`test_stage2_boundary.py`, including the 34 already-rewritten 8a-8f
nodes, were **completely untouched** and verified with zero assertion
delta.

## Per-Node Assertion Table (Touched Nodes)

| Test Node ID | Before | After | Delta | Notes |
|---|:---:|:---:|:---:|---|
| `test_legacy_task_checkpoint_translates_and_executes` | 3 | 2 | -1 | Waived 1 translator-shape check (`TranslatedCommand`) |
| `test_legacy_lesson_propose_translates_and_executes` | 3 | 2 | -1 | Waived 1 translator-shape check (`TranslatedCommand`) |
| `test_legacy_lesson_broadcast_translates_and_delivers_to_room_members` | 8 | 6 | -2 | Waived 2 translator-shape checks (`TranslatedCommand`, `LessonBroadcastCommand`) |
| `test_legacy_lessons_list_translates_and_executes` | 3 | 2 | -1 | Waived 1 translator-shape check (`TranslatedCommand`) |
| `test_legacy_translation_ask` | 2 | 1 | -1 | Waived 1 translator-shape check (`TranslatedCommand`); preserved `out.command.method == "dispatch.submit"` |
| **Touched Total** | **19** | **13** | **-6** | 6 translator-shape assertions retired (all waived); every real outcome/state assertion preserved byte-identical |

## Real Assertions Preserved (Not Retired)

- `test_legacy_task_checkpoint_translates_and_executes`: `client.submit` `CommandSuccess` check and `runtime.task_service.get_target("task-1").state["state"] == "CHECKPOINTED"`.
- `test_legacy_lesson_propose_translates_and_executes`: `client.submit` `CommandSuccess` check and `runtime.lesson_service.get_target("lesson-1").state["lifecycle"] == "PROPOSED"`.
- `test_legacy_lesson_broadcast_translates_and_delivers_to_room_members`: `client.submit` `CommandSuccess`, `outcome.result["recipient_profile_ids"] == ("peer-b", "peer-c")`, inbox count `len(inbox) == 1`, `inbox[0].state["message_type"] == "LESSON"`, governance broker delivery target existence (`delivery is not None`), and `delivery.state["status"] == "PENDING"`.
- `test_legacy_lessons_list_translates_and_executes`: `client.submit` `CommandSuccess` and `any(item["target_id"] == "lesson:listed-lesson" for item in outcome.result["lessons"])`.
- `test_legacy_translation_ask`: `out.command.method == "dispatch.submit"`.

## Retired Assertions (Translator-Only Waivers)

See `docs/reviews/legacy-translator-retirement/gate-canary-waivers-batch08g.json`
for the full waiver list (6 entries total). Every retired assertion is a
translator-shape check: `isinstance(translated, TranslatedCommand)`,
`isinstance(translated.command, LessonBroadcastCommand)`, or
`isinstance(out, TranslatedCommand)`.

## Untouched Nodes Confirmation

All 56 untouched test functions (76 parameterized invocations, including
the 34 sub-batch 8a-8f nodes) in `tests/integration/test_stage2_boundary.py`
were verified by the gate to have **exactly 0 assertion delta**, confirmed
via the per-node table in `tools/legacy_retirement/runs/batch-08g/report.md`.

## Gate Extensions

No modifications to `tools/legacy_retirement/gate.py` were required for
this sub-batch; all retired assertion shapes matched existing verified
`translator_only()` patterns.

## Coverage Delta

Zero lost lines, zero lost branches, across all production modules touched
(confirmed by the gate's coverage comparison; the gate reported PASS).

## Gate Command

```bash
python tools/legacy_retirement/gate.py \
    --baseline main \
    --output tools/legacy_retirement/runs/batch-08g \
    --waivers docs/reviews/legacy-translator-retirement/gate-canary-waivers-batch08g.json \
    tests/integration/test_stage2_boundary.py
```
