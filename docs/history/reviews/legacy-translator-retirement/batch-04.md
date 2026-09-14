# Batch 04 — test_process_lease_sweep.py + test_lease_status.py Preservation Record

> LegacyTranslator retirement, batch 4 of 9.
> Baseline: `main` (after batch 3, commit `4907ce4`).
> Gate result: PASS, 0 failures (`tools/legacy_retirement/runs/batch-04/report.md`).

## Summary

Rewrote both files' `LegacyTranslator`-based tests to construct typed `Command`
objects directly and submit them through the same real application-API boundary.

- `tests/integration/application/test_process_lease_sweep.py`: `test_legacy_translation_and_api_execution`,
  now constructs `LeaseSweepCommand` directly with `limit` and `reap` parameters,
  submitting via `Client(runtime.application_api)`.
- `tests/integration/application/test_lease_status.py`: `test_lease_status_legacy_translation_and_api_execution`,
  now constructs `LeaseStatusCommand` directly, submitting via `Client(runtime.application_api)`.

All other test nodes in both files (6 nodes in `test_process_lease_sweep.py` and
3 nodes in `test_lease_status.py`) were already native and untouched.

## Gate Extensions & Helper Function Analysis

No modifications to `tools/legacy_retirement/gate.py` were required for this batch.
Both target files used `LegacyTranslator().translate(...)`, which is already supported
by the chained translation call detection ratified and introduced in commit `f32358c`.

Regarding argument-parsing helper functions:
`LegacyTranslator.translate()`'s dispatch for `lease-sweep` internally invokes
`_optional_int()` and `_bool_or_false()`. Both helpers were already included in
`_TRANSLATOR_ONLY_HELPER_FUNCTIONS` in `gate.py` (ratified in batch 2). Neither
file exercises `_legacy_room_id()`, so no secondary test file was needed for
the gate invocation.

## Per-Node Assertion Table (AST-derived)

| # | Test Node | Before | After | Notes |
|---|-----------|:---:|:---:|-------|
| 1 | `test_process_lease_sweep.py::test_expired_lease_recovers_and_applies_policy_backoff` | 12 | 12 | Untouched |
| 2 | `test_process_lease_sweep.py::test_nonexpired_lease_is_completely_untouched` | 3 | 3 | Untouched |
| 3 | `test_process_lease_sweep.py::test_sweep_limit_uses_expiry_then_lease_id_order` | 3 | 3 | Untouched |
| 4 | `test_process_lease_sweep.py::test_second_sweep_is_a_convergent_noop` | 3 | 3 | Untouched |
| 5 | `test_process_lease_sweep.py::test_unknown_dead_pid_recovers_without_reap` | 7 | 7 | Untouched |
| 6 | `test_process_lease_sweep.py::test_legacy_translation_and_api_execution` | 8 | 4 | **Rewritten** (4 translator-shape assertions retired, 4 real execution/result assertions preserved byte-identical) |
| 7 | `test_process_lease_sweep.py::test_cli_lease_sweep_json` | 5 | 5 | Untouched |
| 8 | `test_lease_status.py::test_lease_status_sqlite_roundtrip_lists_multiple_active_leases` | 8 | 8 | Untouched |
| 9 | `test_lease_status.py::test_lease_status_reports_expired_lease_without_mutating_it` | 5 | 5 | Untouched |
| 10 | `test_lease_status.py::test_lease_status_legacy_translation_and_api_execution` | 5 | 3 | **Rewritten** (2 translator-shape assertions retired, 3 real execution/result assertions preserved byte-identical) |
| 11 | `test_cli_lease_status_json` | 3 | 3 | Untouched |
| | **TOTAL** | **62** | **56** | 6 translator-shape assertions retired (waived), 0 real assertions lost |

## Retired Assertions (Translator-Only Waivers)

See `docs/reviews/legacy-translator-retirement/gate-canary-waivers-batch04.json`
for the full waiver list with individual justifications (6 entries total).

- `test_process_lease_sweep.py::test_legacy_translation_and_api_execution`:
  1. `isinstance(translated, TranslatedCommand)`: Retire LegacyTranslator translation wrapper check on lease-sweep.
  2. `isinstance(translated.command, LeaseSweepCommand)`: Retire translator command-type check on lease-sweep.
  3. `translated.command.limit == 7`: Retire translator limit parameter mapping check.
  4. `translated.command.reap is False`: Retire translator no_reap negation mapping check.
- `test_lease_status.py::test_lease_status_legacy_translation_and_api_execution`:
  5. `isinstance(translated, TranslatedCommand)`: Retire LegacyTranslator translation wrapper check on lease-status.
  6. `isinstance(translated.command, LeaseStatusCommand)`: Retire translator command-type check on lease-status.

All real behavioral assertions on command execution (`CommandSuccess`, swept lease ID, post-state `FENCED`, and returned lease peer `"ag"`) are preserved byte-identical.

## Coverage Delta

| File | Lines Before / After / Denom | Branches Before / After / Denom | Lost Lines | Lost Branches |
|------|:----------------------------:|:-------------------------------:|:----------:|:-------------:|
| `peerhub/application/legacy.py` | 541 / 541 / 689 | 14 / 14 / 28 | None | None |

Exact coverage coordinates across all production files are 100% preserved. 0 lost lines, 0 lost branches.

## Gate Command

```bash
python tools/legacy_retirement/gate.py \
    --baseline main \
    --output tools/legacy_retirement/runs/batch-04 \
    --waivers docs/reviews/legacy-translator-retirement/gate-canary-waivers-batch04.json \
    tests/integration/application/test_process_lease_sweep.py \
    tests/integration/application/test_lease_status.py
```
