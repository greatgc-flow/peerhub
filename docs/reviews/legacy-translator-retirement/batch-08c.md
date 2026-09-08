# Batch 08c — test_stage2_boundary.py Messaging & Threads (Mailbox/Broadcast) Preservation Record

> LegacyTranslator retirement, sub-batch 8c of 8 (overall batch 8 of 9).
> Baseline commit: `2ef143f` (gate extension for inline `InvalidLegacyArguments` comparisons).
> Target: partial rewrite of `tests/integration/test_stage2_boundary.py` (5 messaging_and_threads mailbox/broadcast test nodes off `LegacyTranslator`).
> Gate result: PASS, 0 failures (`tools/legacy_retirement/runs/batch-08c/report.md`).

## Summary

Rewrote the 5 mailbox/broadcast `messaging_and_threads` test nodes in
`tests/integration/test_stage2_boundary.py` off `LegacyTranslator`,
constructing typed `Command` objects directly and wrapping each in
`SimpleNamespace(command=...)` where the original assertion text
(`client.submit(translated.command)`, `outcome = ...`) needed to survive
unchanged.

The 5 rewritten test functions are:
1. `test_legacy_thread_append_translates_and_executes` -> constructs `ThreadAppendCommand` directly
2. `test_legacy_send_translates_and_persists_mailbox_delivery` -> constructs `MessageSendCommand` directly
3. `test_legacy_broadcast_translates_executes_and_validates_arguments` -> constructs `RoomBroadcastCommand` directly
4. `test_legacy_check_returns_only_the_callers_private_messages` -> constructs `MessageCheckCommand` directly
5. `test_legacy_mark_read_translates_and_advances_cursor` -> constructs `MessageMarkReadCommand` directly

All other 60 test functions (76 parameterized invocations) in
`test_stage2_boundary.py`, including the 11 already-rewritten 8a/8b nodes,
were **completely untouched** and verified with zero assertion delta.

## Gate Extension Used By This Sub-Batch (Correction Path)

An earlier dispatch attempt for this sub-batch correctly identified (per
the mandatory "STOP on unrecognized pattern" discipline, no unilateral
`gate.py` edit made) that `test_legacy_broadcast_translates_executes_and_
validates_arguments` compares the translate() call directly inline:

```python
assert translator.translate(
    LegacyActionCall("broadcast", {"room_id": "room-legacy-broadcast"}),
    _legacy_submission(),
) == InvalidLegacyArguments("broadcast", "broadcast requires --msg")
```

The existing `InvalidLegacyArguments`-comparison waiver (added batch 3)
only recognized the *variable-bound* form (`outcome = translator.translate
(...); outcome == InvalidLegacyArguments(...)`), not this inline form.
The terminal independently verified the citation against the actual gate
source and test file, extended `translator_only()`'s check to also accept
`_is_translate_call_on(value)` on the comparison's left side (the same
helper already used elsewhere for chained-call recognition), added 2
selftest cases, and committed the extension separately as `2ef143f`
(selftest 20/20) before this sub-batch was redispatched. Full details:
`git log 2ef143f`.

Both inline `InvalidLegacyArguments` assertions in this sub-batch's
`test_legacy_broadcast_translates_executes_and_validates_arguments` are
now waived as translator-shape checks (they test the translator's own
custom rejection strings, which is a legitimate thing to retire since the
translator itself is being deleted -- no native-equivalent replacement
was required, unlike batch 7's `broker_status` case, which needed one
because that check was not a pure translator-shape assertion).

## Per-Node Assertion Table (Touched Nodes)

| Test Node ID | Before | After | Delta | Notes |
|---|:---:|:---:|:---:|---|
| `test_legacy_thread_append_translates_and_executes` | 5 | 4 | -1 | Waived 1 translator-shape check (`TranslatedCommand`) |
| `test_legacy_send_translates_and_persists_mailbox_delivery` | 8 | 6 | -2 | Waived 2 translator-shape checks (`TranslatedCommand`, `MessageSendCommand`) |
| `test_legacy_broadcast_translates_executes_and_validates_arguments` | 6 | 2 | -4 | Waived 4 translator-shape checks (`TranslatedCommand`, `RoomBroadcastCommand`, and the 2 inline `InvalidLegacyArguments` rejection-shape checks) |
| `test_legacy_check_returns_only_the_callers_private_messages` | 6 | 4 | -2 | Waived 2 translator-shape checks (`TranslatedCommand`, `MessageCheckCommand`) |
| `test_legacy_mark_read_translates_and_advances_cursor` | 5 | 3 | -2 | Waived 2 translator-shape checks (`TranslatedCommand`, `MessageMarkReadCommand`) |
| **Touched Total** | **30** | **19** | **-11** | 11 translator-shape assertions retired (all waived); every real outcome/state assertion preserved byte-identical |

## Real Assertions Preserved (Not Retired)

- `test_legacy_thread_append_translates_and_executes`: `outcome` success check and the `runtime.rooms_service.get_target("message:message-append-1")` field assertions.
- `test_legacy_send_translates_and_persists_mailbox_delivery`: `outcome` success check and all mailbox-delivery field/state assertions (`target_id`, delivery record fields, `correlation_id`).
- `test_legacy_broadcast_translates_executes_and_validates_arguments`: `outcome` success check and `len(outcome.result["delivered"]) == 2`.
- `test_legacy_check_returns_only_the_callers_private_messages`: `outcome` success check and the messages-list filtering assertions.
- `test_legacy_mark_read_translates_and_advances_cursor`: `outcome` success check and the cursor-advancement `target_id` assertion.

## Retired Assertions (Translator-Only Waivers)

See `docs/reviews/legacy-translator-retirement/gate-canary-waivers-batch08c.json`
for the full waiver list (11 entries total). Every retired assertion is a
translator-shape check: `isinstance(translated, TranslatedCommand)`,
`isinstance(translated.command, Type)`, or the translator's own
`InvalidLegacyArguments` rejection-shape comparison.

## Untouched Nodes Confirmation

All 60 untouched test functions (76 parameterized invocations, including
the 11 sub-batch 8a/8b nodes) in `tests/integration/test_stage2_boundary.py`
were verified by the gate to have **exactly 0 assertion delta**, confirmed
via the full per-node table in `tools/legacy_retirement/runs/batch-08c/report.md`.

## Coverage Delta

Zero lost lines, zero lost branches, across all production modules touched
(confirmed by the gate's coverage comparison; the gate fails closed on any
coverage regression, and it reported PASS).

## Notes on This Sub-Batch's Dispatch

The rewrite and waivers file survived a background dispatch that was
killed by a host memory constraint after the gate had already run and
reported `Result: PASS` (evidenced by the surviving
`tools/legacy_retirement/runs/batch-08c/` snapshot), but before the doc/
commit/push steps ran. The terminal reviewed the full diff for quality
(no discarded-value or gutted-assertion regressions found; every real
assertion listed above is intact), independently re-ran the gate against
the surviving tree state (fresh `PASS: 0 failure(s)`), wrote this
document, and ran full suite/pyright/commit itself.

## Gate Command

```bash
python tools/legacy_retirement/gate.py \
    --baseline main \
    --output tools/legacy_retirement/runs/batch-08c \
    --waivers docs/reviews/legacy-translator-retirement/gate-canary-waivers-batch08c.json \
    tests/integration/test_stage2_boundary.py
```
