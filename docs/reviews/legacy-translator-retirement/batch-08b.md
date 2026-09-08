# Batch 08b — test_stage2_boundary.py Leadership & Duty/Terminal Preservation Record

> LegacyTranslator retirement, sub-batch 8b of 8 (overall batch 8 of 9).
> Baseline commit: `366f4e8` (sub-batch 8a).
> Target: partial rewrite of `tests/integration/test_stage2_boundary.py` (6 leadership/duty_and_terminal test nodes off `LegacyTranslator`).
> Gate result: PASS, 0 failures (`tools/legacy_retirement/runs/batch-08b/report.md`).

## Summary

Rewrote the 6 `leadership` and `duty_and_terminal` test nodes in
`tests/integration/test_stage2_boundary.py` off `LegacyTranslator`,
constructing typed `Command` objects directly. Where the original test's
exact assertion text needed to survive unchanged (`client.submit(translated
.command)` etc.) but `translated` no longer comes from a real `translate()`
call, the constructed command is wrapped in `SimpleNamespace(command=...)`,
following the precedent set in batches 2 and 8a.

The 6 rewritten test functions are:
1. `test_legacy_leader_claim_translates_and_executes` -> constructs `LeaderClaimCommand` directly
2. `test_legacy_leader_yield_translates_and_executes` -> constructs `LeaderYieldCommand` directly
3. `test_legacy_terminal_close_translates_and_executes` -> constructs `TerminalCloseCommand` directly
4. `test_terminal_close_can_end_duty_and_room_session` -> constructs `TerminalCloseCommand` directly (with session-close fields)
5. `test_terminal_close_reports_session_failure_after_duty_close_and_retries` -> constructs `TerminalCloseCommand` directly inside its local `translated_close()` helper
6. `test_legacy_terminal_duty_sweep_expires_only_timed_out_lease` -> constructs `TerminalDutySweepCommand` directly

All other 59 test functions (76 parameterized invocations) in
`test_stage2_boundary.py`, including the 5 sub-batch 8a nodes, were
**completely untouched** and verified with zero assertion delta.

## Per-Node Assertion Table (Touched Nodes)

| Test Node ID | Before | After | Delta | Notes |
|---|:---:|:---:|:---:|---|
| `test_legacy_leader_claim_translates_and_executes` | 13 | 8 | -5 | Waived 5 translator-shape checks (`TranslatedCommand`, `LeaderClaimCommand`, and 3 field-mapping checks on `peer_node_id`/`reason`/`domain`, each duplicated by construction-time keyword arguments) |
| `test_legacy_leader_yield_translates_and_executes` | 8 | 4 | -4 | Waived 4 translator-shape checks (`TranslatedCommand`, `LeaderYieldCommand`, and field-mapping checks on `yielding_peer_id`/`reason`) |
| `test_legacy_terminal_close_translates_and_executes` | 3 | 2 | -1 | Waived 1 translator-shape check (`TranslatedCommand`) |
| `test_terminal_close_can_end_duty_and_room_session` | 7 | 6 | -1 | Waived 1 translator-shape check (`TranslatedCommand`) |
| `test_terminal_close_reports_session_failure_after_duty_close_and_retries` | 12 | 11 | -1 | Waived 1 translator-shape check (`TranslatedCommand`, inside the local `translated_close()` helper) |
| `test_legacy_terminal_duty_sweep_expires_only_timed_out_lease` | 6 | 5 | -1 | Waived 1 translator-shape check (`TranslatedCommand`) |
| **Touched Total** | **49** | **36** | **-13** | 13 translator-shape assertions retired (all waived); real assertions on `outcome.result`, lease/session state, and duty-sweep counts preserved byte-identical |

## Retired Assertions (Translator-Only Waivers)

See `docs/reviews/legacy-translator-retirement/gate-canary-waivers-batch08b.json`
for the full waiver list (13 entries total). Every retired assertion is a
translator-shape check: `isinstance(translated, TranslatedCommand)`,
`isinstance(translated.command, Type)`, or `translated.command.FIELD ==
literal` where the literal is now supplied directly as a constructor keyword
argument at the same call site (so the value is still verified, just via
construction rather than a post-hoc field-equality assertion).

## Real Assertions Preserved (Not Retired)

Every non-translator-shape assertion in the 6 touched nodes is unchanged:
- `test_legacy_leader_claim_translates_and_executes`: `outcome` success check and downstream leadership-state assertions.
- `test_legacy_leader_yield_translates_and_executes`: `outcome` success check and downstream yield-state assertions.
- `test_legacy_terminal_close_translates_and_executes`: `client.submit(translated.command)` success check, `duty_lease_coordinator.get_lease(...).state.value == "RELEASED"`.
- `test_terminal_close_can_end_duty_and_room_session`: `outcome.result["duty_close"]["status"] == "ok"` and the remaining session-close result assertions.
- `test_terminal_close_reports_session_failure_after_duty_close_and_retries`: all retry/failure-outcome assertions across both `client.submit(translated_close(...).command)` calls.
- `test_legacy_terminal_duty_sweep_expires_only_timed_out_lease`: `outcome.result["expired_count"] == 1` and sibling sweep-result assertions.

## Untouched Nodes Confirmation

All 59 untouched test functions (76 parameterized invocations, including the
5 sub-batch 8a nodes) in `tests/integration/test_stage2_boundary.py` were
verified by the gate to have **exactly 0 assertion delta** -- confirmed via
the full per-node table in `tools/legacy_retirement/runs/batch-08b/report.md`.

## Gate Extensions & Helper Function Analysis

No modifications to `tools/legacy_retirement/gate.py` were required for
sub-batch 8b. The existing `translated.command.FIELD == literal` waiver
pattern (added for earlier batches) covered every field-mapping assertion
retired here.

## Coverage Delta

Zero lost lines, zero lost branches, across all production modules touched
(confirmed by the gate's coverage comparison; the gate fails closed on any
coverage regression, and it reported PASS).

## Notes on This Sub-Batch's Dispatch

The rewrite, waivers file, and gate run (evidenced by the surviving
`tools/legacy_retirement/runs/batch-08b/` snapshot showing `Result: PASS`)
were completed by the dispatched peer, but the dispatch was killed by a
host memory constraint before the doc/commit/push steps ran. The terminal
independently re-ran the gate against the surviving tree state (fresh
`PASS: 0 failure(s)`), reviewed the full diff for quality (no discarded-
value or gutted-assertion regressions found, consistent with every real
assertion listed above), wrote this document, and ran full
suite/pyright/commit itself.

## Gate Command

```bash
python tools/legacy_retirement/gate.py \
    --baseline main \
    --output tools/legacy_retirement/runs/batch-08b \
    --waivers docs/reviews/legacy-translator-retirement/gate-canary-waivers-batch08b.json \
    tests/integration/test_stage2_boundary.py
```
