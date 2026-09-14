# Batch 08e — test_stage2_boundary.py Room & Session Lifecycle Preservation Record

> LegacyTranslator retirement, sub-batch 8e of 8 (overall batch 8 of 9).
> Baseline commit: `7d692c3` (batch 8d messaging/threads handoffs/reactions nodes rewritten).
> Target: partial rewrite of `tests/integration/test_stage2_boundary.py` (6 room_lifecycle/session_lifecycle test nodes off `LegacyTranslator`).
> Gate result: PASS, 0 failures (`tools/legacy_retirement/runs/batch-08e/report.md`).

## Summary

Rewrote the 6 room & session lifecycle test nodes in
`tests/integration/test_stage2_boundary.py` off `LegacyTranslator`,
constructing typed `Command` objects directly and wrapping each in
`SimpleNamespace(command=...)` where the original assertion text
(`client.submit(translated.command)`, `outcome = ...`) needed to survive
unchanged.

The 6 rewritten test functions are:
1. `test_legacy_room_topic_translates_and_executes` -> constructs `NewTopicCommand` directly
2. `test_legacy_init_session_translates_and_executes` -> constructs `SessionOpenCommand` directly
3. `test_legacy_end_session_translates_and_executes` -> constructs `SessionCloseCommand` directly
4. `test_legacy_status_resolves_explicit_room_argument` -> constructs `StatusReadCommand` directly using `_legacy_room_id` resolution
5. `test_legacy_status_resolves_nested_context_and_submission_scope` (parametrized, 3 variants) -> constructs `StatusReadCommand` directly using `_legacy_room_id(arguments, scope)`
6. `test_legacy_status_rejects_empty_room_context_without_fallback` -> retired legacy `LegacyTranslator().translate()` call and `InvalidLegacyArguments` assertion; verifies `_legacy_room_id({}, {}) == ""` directly, preserving full coverage of `_legacy_room_id`'s empty return branch, and retains governance broker target preservation check

All other 55 test functions (71 parameterized invocations) in
`test_stage2_boundary.py`, including `test_native_session_heartbeat_executes_through_client`
and the 22 already-rewritten 8a/8b/8c/8d nodes, were **completely untouched**
and verified with zero assertion delta.

## Coverage Preservation & Real Replacement Discipline (Lesson 2)

During initial gate execution, retiring the `LegacyTranslator().translate()` call from
`test_legacy_status_rejects_empty_room_context_without_fallback` exposed a coverage
regression on line 99 (`return ""`) and branch `(95, 99)` of `peerhub/application/legacy.py`
inside `_legacy_room_id()`. The translator's rejection path was exercising `_legacy_room_id`'s
fallback-exhaustion branch when called with empty arguments and scope. Per Lesson 2 (real
replacement discipline) and Rule 3 (clean reset, no forward patching), the tree was reset
and rewritten so that `test_legacy_status_rejects_empty_room_context_without_fallback`
explicitly verifies the helper's fallback behavior (`assert _legacy_room_id({}, {}) == ""`),
maintaining 100% line and branch coverage across all production modules with zero lost lines
and zero lost branches.

No modifications to `tools/legacy_retirement/gate.py` were required for this sub-batch;
all retired assertion shapes matched existing verified `translator_only()` patterns.

## Per-Node Assertion Table (Touched Nodes)

| Test Node ID | Before | After | Delta | Notes |
|---|:---:|:---:|:---:|---|
| `test_legacy_room_topic_translates_and_executes` | 3 | 2 | -1 | Waived 1 translator-shape check (`TranslatedCommand`) |
| `test_legacy_init_session_translates_and_executes` | 7 | 5 | -2 | Waived 2 translator-shape checks (`TranslatedCommand`, `SessionOpenCommand`) |
| `test_legacy_end_session_translates_and_executes` | 6 | 4 | -2 | Waived 2 translator-shape checks (`TranslatedCommand`, `SessionCloseCommand`) |
| `test_legacy_status_resolves_explicit_room_argument` | 7 | 5 | -2 | Waived 2 translator-shape checks (`TranslatedCommand`, `StatusReadCommand`); preserved `_legacy_room_id` resolution check and `outcome.command.room_id == "room-explicit"` |
| `test_legacy_status_resolves_nested_context_and_submission_scope` [arguments0] | 6 | 4 | -2 | Waived 2 translator-shape checks (`TranslatedCommand`, `StatusReadCommand`); preserved `_legacy_room_id` resolution check and `outcome.command.room_id == expected_room_id` |
| `test_legacy_status_resolves_nested_context_and_submission_scope` [arguments1] | 6 | 4 | -2 | Waived 2 translator-shape checks (`TranslatedCommand`, `StatusReadCommand`); preserved `_legacy_room_id` resolution check and `outcome.command.room_id == expected_room_id` |
| `test_legacy_status_resolves_nested_context_and_submission_scope` [arguments2] | 6 | 4 | -2 | Waived 2 translator-shape checks (`TranslatedCommand`, `StatusReadCommand`); preserved `_legacy_room_id` resolution check and `outcome.command.room_id == expected_room_id` |
| `test_legacy_status_rejects_empty_room_context_without_fallback` | 2 | 2 | 0 | Waived 1 translator-shape check (`InvalidLegacyArguments`), added `_legacy_room_id({}, {}) == ""` real verification check, preserved broker target check |
| **Touched Total (across 8 parameterized invocations)** | **43** | **30** | **-13** | 14 translator-shape assertions retired (all waived); 1 real assertion added; every real outcome/state assertion preserved byte-identical |

## Real Assertions Preserved (Not Retired)

- `test_legacy_room_topic_translates_and_executes`: `client.submit` `CommandSuccess` and thread target subject assertion (`"Topic"`).
- `test_legacy_init_session_translates_and_executes`: `client.submit` `CommandSuccess`, `ACTIVE` state, owner instance/profile mapping, session existence, and coordinator session state (`RoomSessionState.ACTIVE`).
- `test_legacy_end_session_translates_and_executes`: `client.submit` `CommandSuccess`, `ENDED` state, persisted session existence, and coordinator session state (`RoomSessionState.ENDED`).
- `test_legacy_status_resolves_explicit_room_argument`: `_legacy_room_id` explicit room resolution check, command `room_id == "room-explicit"`, `submitted` `CommandSuccess`, room summary payload fields, and `unread_count == 1`.
- `test_legacy_status_resolves_nested_context_and_submission_scope` (all 3 variants): `_legacy_room_id` resolution against scope/context, command `room_id == expected_room_id`, `submitted` `CommandSuccess`, and exact result dictionary matching.
- `test_legacy_status_rejects_empty_room_context_without_fallback`: `assert _legacy_room_id({}, {}) == ""` fallback rejection check, and `tuple(runtime.governance_broker.list_targets("room")) == rooms_before` broker target preservation check.

## Retired Assertions (Translator-Only Waivers)

See `docs/reviews/legacy-translator-retirement/gate-canary-waivers-batch08e.json`
for the full waiver list (14 entries total):
- 7 wrapper checks: `isinstance(translated, TranslatedCommand)` / `isinstance(outcome, TranslatedCommand)`
- 6 command-type checks: `isinstance(translated.command, SessionOpenCommand)` / `SessionCloseCommand` / `StatusReadCommand`
- 1 translator rejection-shape check: `outcome == InvalidLegacyArguments(action='status', reason='room_id is required in arguments, context, or scope')`

## Untouched Nodes Confirmation

All 55 untouched test functions (71 parameterized invocations, including
`test_native_session_heartbeat_executes_through_client` and the 22 sub-batch 8a-8d nodes)
in `tests/integration/test_stage2_boundary.py` were verified by the gate to have
**exactly 0 assertion delta**, confirmed via the per-node table in
`tools/legacy_retirement/runs/batch-08e/report.md`.

## Coverage Delta

Zero lost lines, zero lost branches, across all production modules touched
(confirmed by the gate's coverage comparison; the gate reported PASS).

## Gate Command

```bash
python tools/legacy_retirement/gate.py \
    --baseline main \
    --output tools/legacy_retirement/runs/batch-08e \
    --waivers docs/reviews/legacy-translator-retirement/gate-canary-waivers-batch08e.json \
    tests/integration/test_stage2_boundary.py
```
