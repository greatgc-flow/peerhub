# Batch 05 — test_peer_quarantine.py + test_health_quickwins.py Preservation Record

> LegacyTranslator retirement, batch 5 of 9.
> Baseline: `main` (after batch 4, commit `7407356`).
> Gate result: PASS, 0 failures (`tools/legacy_retirement/runs/batch-05/report.md`).

## Summary

Rewrote both files' `LegacyTranslator`-based tests to construct typed `Command`
objects directly and submit them through the same real application-API boundary.

- `tests/integration/application/test_peer_quarantine.py`: `test_legacy_translation_and_api_command_execution`,
  now constructs `PeerQuarantineCommand` directly with `submission`, `peer_id`,
  `reason`, and `actor_id` parameters, submitting via `Client(runtime.application_api)`.
- `tests/integration/application/test_health_quickwins.py`: `test_legacy_translation_and_api_execution`,
  now constructs typed commands directly (`PeerStatusCommand`, `HealthCheckCommand`,
  `CheckGateCommand`, `HealthPrecheckCommand`, `HealthSweepCommand`, `PeerRecoverCommand`),
  submitting each via `Client(runtime.application_api)`.

All other test nodes in both files (4 nodes in `test_peer_quarantine.py` and
13 nodes in `test_health_quickwins.py`) were already native and untouched.

## Gate Extensions & Helper Function Analysis

No modifications to `tools/legacy_retirement/gate.py` were required for this batch.

Regarding argument-parsing helper functions:
`LegacyTranslator.translate()`'s dispatch for these 7 health/quarantine actions
internally invokes `_optional_first_text()`, `_first_text()`, and `_bool_or_false()`.
All three helpers were already included in `_TRANSLATOR_ONLY_HELPER_FUNCTIONS` in
`gate.py` (ratified in batch 2). Neither file exercises `_legacy_room_id()`, so no
secondary test file was needed for the gate invocation.

## Per-Node Assertion Table (AST-derived)

| # | Test Node | Before | After | Notes |
|---|-----------|:---:|:---:|-------|
| 1 | `test_peer_quarantine.py::test_peer_quarantine_sqlite_roundtrip` | 26 | 26 | Untouched |
| 2 | `test_peer_quarantine.py::test_peer_quarantine_cleared_by_peer_recover` | 14 | 14 | Untouched |
| 3 | `test_peer_quarantine.py::test_peer_quarantine_idempotent_refresh` | 8 | 8 | Untouched |
| 4 | `test_peer_quarantine.py::test_legacy_translation_and_api_command_execution` | 12 | 7 | **Rewritten** (5 translator-shape assertions retired, 7 real execution/result assertions preserved byte-identical) |
| 5 | `test_peer_quarantine.py::test_cli_peer_quarantine_execution` | 1 | 1 | Untouched |
| 6 | `test_health_quickwins.py::test_peer_status_registered_and_base_nodes` | 8 | 8 | Untouched |
| 7 | `test_health_quickwins.py::test_peer_status_specific_peer_and_unknown` | 3 | 3 | Untouched |
| 8 | `test_health_quickwins.py::test_peer_status_degraded_and_quarantined_display` | 4 | 4 | Untouched |
| 9 | `test_health_quickwins.py::test_health_check_read_only` | 6 | 6 | Untouched |
| 10 | `test_health_quickwins.py::test_health_check_with_recover_reconciles_circuit` | 5 | 5 | Untouched |
| 11 | `test_health_quickwins.py::test_execute_peer_recover_single_peer` | 6 | 6 | Untouched |
| 12 | `test_health_quickwins.py::test_execute_peer_recover_all_peers_multi_iteration` | 5 | 5 | Untouched |
| 13 | `test_health_quickwins.py::test_execute_peer_recover_unknown_peer` | 4 | 4 | Untouched |
| 14 | `test_health_quickwins.py::test_health_precheck_all_healthy` | 4 | 4 | Untouched |
| 15 | `test_health_quickwins.py::test_health_precheck_degraded_fails_closed` | 5 | 5 | Untouched |
| 16 | `test_health_quickwins.py::test_check_gate_open_and_closed` | 9 | 9 | Untouched |
| 17 | `test_health_quickwins.py::test_health_sweep_fresh_and_stale` | 4 | 4 | Untouched |
| 18 | `test_health_quickwins.py::test_legacy_translation_and_api_execution` | 13 | 12 | **Rewritten** (1 translator-shape assertion retired, 12 real execution/result assertions preserved byte-identical) |
| 19 | `test_health_quickwins.py::test_cli_commands_execution` | 10 | 10 | Untouched |
| | **TOTAL** | **147** | **141** | 6 translator-shape assertions retired (waived), 0 real assertions lost |

## Retired Assertions (Translator-Only Waivers)

See `docs/reviews/legacy-translator-retirement/gate-canary-waivers-batch05.json`
for the full waiver list with individual justifications (6 entries total).

- `test_peer_quarantine.py::test_legacy_translation_and_api_command_execution`:
  1. `isinstance(translated, TranslatedCommand)`: Retire LegacyTranslator translation wrapper check on peer-quarantine.
  2. `isinstance(translated.command, PeerQuarantineCommand)`: Retire translator command-type check on peer-quarantine.
  3. `translated.command.peer_id == 'ag'`: Retire translator peer_id parameter mapping check.
  4. `translated.command.reason == 'repeated timeout'`: Retire translator reason parameter mapping check.
  5. `translated.command.actor_id == 'admin-1'`: Retire translator actor_id parameter mapping check.
- `test_health_quickwins.py::test_legacy_translation_and_api_execution`:
  6. `isinstance(outcome, TranslatedCommand)`: Retire LegacyTranslator translation wrapper check on health quick win actions.

All real behavioral assertions on command execution (`CommandSuccess`, result mappings, circuit states, and projection states) are preserved byte-identical.

## Coverage Delta

Exact coverage coordinates across all production files are 100% preserved. 0 lost lines, 0 lost branches.

## Gate Command

```bash
python tools/legacy_retirement/gate.py \
    --baseline main \
    --output tools/legacy_retirement/runs/batch-05 \
    --waivers docs/reviews/legacy-translator-retirement/gate-canary-waivers-batch05.json \
    tests/integration/application/test_peer_quarantine.py \
    tests/integration/application/test_health_quickwins.py
```
