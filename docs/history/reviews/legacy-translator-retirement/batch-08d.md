# Batch 08d — test_stage2_boundary.py Messaging & Threads (Handoffs/Reactions) Preservation Record

> LegacyTranslator retirement, sub-batch 8d of 8 (overall batch 8 of 9).
> Baseline commit: `9a04d0a` (gate extension for direct translation result field comparisons).
> Target: partial rewrite of `tests/integration/test_stage2_boundary.py` (6 messaging_and_threads handoffs/reactions test nodes off `LegacyTranslator`).
> Gate result: PASS, 0 failures (`tools/legacy_retirement/runs/batch-08d/report.md`).

## Summary

Rewrote the 6 handoffs/reactions `messaging_and_threads` test nodes in
`tests/integration/test_stage2_boundary.py` off `LegacyTranslator`,
constructing typed `Command` objects directly and wrapping each in
`SimpleNamespace(command=...)` where the original assertion text
(`client.submit(translated.command)`, `outcome = ...`) needed to survive
unchanged.

The 6 rewritten test functions are:
1. `test_legacy_thread_promote_translates_and_marks_mailbox_source` -> constructs `ThreadPromoteCommand` directly
2. `test_legacy_append_handoff_and_checkpoint_execute_end_to_end` -> constructs `AppendHandoffCommand` and `ContinuityCheckpointCommand` directly
3. `test_legacy_context_fill_translates_and_executes_read_only` -> constructs `ContextFillCommand` directly
4. `test_legacy_thread_react_translates_and_executes` -> constructs `ThreadReactCommand` directly
5. `test_legacy_thread_react_remove_dispatches_to_unreact` -> constructs `ThreadReactCommand` directly
6. `test_thread_react_rejects_unknown_action` -> removed legacy translator setup and assertions; retained native `ThreadReactCommand` execution and invalid parameter assertions

All other 55 test functions (75 parameterized invocations) in
`test_stage2_boundary.py`, including `test_native_thread_react_remove_executes_through_client`
and the 16 already-rewritten 8a/8b/8c nodes, were **completely untouched**
and verified with zero assertion delta.

## Gate Extension Used By This Sub-Batch

Baseline commit `9a04d0a` extended `translator_only()` in `tools/legacy_retirement/gate.py`
to recognize direct field comparisons on translation results (`is_direct_result_field`),
specifically supporting `outcome.FIELD == literal` (e.g. `translated.reason == 'action must be ADD or REMOVE'`)
on `InvalidLegacyArguments` instances returned by `LegacyTranslator.translate()`.
All 22 gate selftests passed prior to dispatching this sub-batch.

## Per-Node Assertion Table (Touched Nodes)

| Test Node ID | Before | After | Delta | Notes |
|---|:---:|:---:|:---:|---|
| `test_legacy_thread_promote_translates_and_marks_mailbox_source` | 7 | 5 | -2 | Waived 2 translator-shape checks (`TranslatedCommand`, `ThreadPromoteCommand`) |
| `test_legacy_append_handoff_and_checkpoint_execute_end_to_end` | 13 | 9 | -4 | Waived 4 translator-shape checks (`TranslatedCommand` x2, `AppendHandoffCommand`, `ContinuityCheckpointCommand`) |
| `test_legacy_context_fill_translates_and_executes_read_only` | 9 | 7 | -2 | Waived 2 translator-shape checks (`TranslatedCommand`, `ContextFillCommand`) |
| `test_legacy_thread_react_translates_and_executes` | 6 | 5 | -1 | Waived 1 translator-shape check (`TranslatedCommand`) |
| `test_legacy_thread_react_remove_dispatches_to_unreact` | 8 | 5 | -3 | Waived 3 translator-shape checks (`TranslatedCommand`, `ThreadReactCommand`, `translated.command.action == 'REMOVE'`) |
| `test_thread_react_rejects_unknown_action` | 4 | 2 | -2 | Waived 2 translator-shape checks (`InvalidLegacyArguments`, `translated.reason == 'action must be ADD or REMOVE'`) |
| **Touched Total** | **47** | **33** | **-14** | 14 translator-shape assertions retired (all waived); every real outcome/state assertion preserved byte-identical |

## Real Assertions Preserved (Not Retired)

- `test_legacy_thread_promote_translates_and_marks_mailbox_source`: `outcome` success check, `source is not None`, `source.state['promoted_to'] == 'thread-mail-promote'`, `len(promoted) == 1`, and `promoted[0].state['metadata']` promotion provenance check.
- `test_legacy_append_handoff_and_checkpoint_execute_end_to_end`: `client.submit(append_translation.command)` success check, `outcome` success check, GOAL section value, KEY_DECISIONS items, markdown heading check, `replay` idempotency and equality check, and broker continuity note / checkpoint target counts.
- `test_legacy_context_fill_translates_and_executes_read_only`: `outcome` success check, `outcome.state == 'COMPLETED'`, `session_id` match, `tuple(outcome.result['sections'])` match, GOAL value, PENDING_ISSUES items, and verification that no checkpoint target was created (read-only execution).
- `test_legacy_thread_react_translates_and_executes`: `outcome` success check, `state is not None`, `state.state['status'] == 'ACTIVE'`, reaction event count, and reaction event action `ADD`.
- `test_legacy_thread_react_remove_dispatches_to_unreact`: `outcome` success check, `state is not None`, `state.state['status'] == 'REMOVED'`, reaction event count, and reaction event action `REMOVE`.
- `test_thread_react_rejects_unknown_action`: `isinstance(outcome, CommandFailure)` and `outcome.error.code is ErrorCode.INVALID_PARAMS` on client submission of native command with unknown action `TOGGLE`.

## Retired Assertions (Translator-Only Waivers)

See `docs/reviews/legacy-translator-retirement/gate-canary-waivers-batch08d.json`
for the full waiver list (14 entries total). Every retired assertion is a
translator-shape check: `isinstance(translated, TranslatedCommand)`,
`isinstance(translated.command, Type)`, command field comparison (`translated.command.action == 'REMOVE'`),
or the translator's own `InvalidLegacyArguments` rejection-shape checks (`isinstance(translated, InvalidLegacyArguments)`,
`translated.reason == 'action must be ADD or REMOVE'`).

## Untouched Nodes Confirmation

All 55 untouched test functions (75 parameterized invocations, including
`test_native_thread_react_remove_executes_through_client` and the 16 sub-batch 8a/8b/8c nodes)
in `tests/integration/test_stage2_boundary.py` were verified by the gate to have
**exactly 0 assertion delta**, confirmed via the full per-node table in
`tools/legacy_retirement/runs/batch-08d/report.md`.

## Coverage Delta

Zero lost lines, zero lost branches, across all production modules touched
(confirmed by the gate's coverage comparison; the gate fails closed on any
coverage regression, and it reported PASS).

## Gate Command

```bash
python tools/legacy_retirement/gate.py \
    --baseline main \
    --output tools/legacy_retirement/runs/batch-08d \
    --waivers docs/reviews/legacy-translator-retirement/gate-canary-waivers-batch08d.json \
    tests/integration/test_stage2_boundary.py
```
