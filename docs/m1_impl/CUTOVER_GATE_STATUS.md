# M1 public CLI cutover gate status

Scope: the `peerhub-m1` public CLI (`peerhub/m1_cli.py`). Numbering follows the cutover gate brief (items 1-7).

| # | Gate item | Status | Evidence |
|---|---|---|---|
| 1 | Canonical command inventory derived from the real parser (no prose-only inventory) | DONE (this PR) | `tools/m1_command_inventory.py` derives leaves and options from `build_parser()`; committed `docs/m1_impl/command_inventory.json`; `tests/m1/e2e/test_cli_contract.py::test_inventory_*` fail on divergence (`--write` regenerates, `--check` verifies) |
| 2 | Diag quota parity (current Observation evidence for quota/rate-limit/resource pools, no mutation or refresh, UNKNOWN/STALE/UNAVAILABLE explicit) | DONE (this PR) | `peerhub-m1 diag quota` (`peerhub/extensions/diag_quota.py`, reader over `ReadonlyDiag`); `tests/m1/e2e/test_cli_diag_quota.py` (raw-SQL seeded, state digest and file hash unchanged, missing DB never created) |
| 3 | No inert options | DONE (this PR) | `diag health --obs-db` is now effective (observations summary read from that DB; stream part from `--db`); `test_every_published_option_has_an_effect_proof` walks the real parser and fails for any option without a behavioural proof |
| 4 | Disposition CSV + tests | Already present before this PR | not changed here |
| 5 | Exit / help / JSON contract | DONE (this PR) | every leaf: `--help` exit 0 and lists all options, unknown option exit 2 without traceback, stable JSON key sets, documented exit codes 1/2/3/5 asserted |
| 6 | (open) | NOT DONE here | The repository docs do not define this item; it is only in the owner's cutover brief. It must be written into this file with its acceptance test when taken up |
| 7 | (open) | NOT DONE here | Same as item 6 |

## Semantics decided in this PR
- `diag quota` is read-only: `--db` (global) or `--obs-db PATH` (another M1 workspace database; observations and resource pools live in the same SQLite file as Core, there is no separate default file). A database that does not exist is never created.
- Output: per resource pool (pools with no quota evidence appear as explicit UNKNOWN, never unlimited), each item has subject_ref, resource_pool_ref, kind (quota/rate_limit), state exactly as Diag evaluates it, age_seconds, source, the measurement keys present (only `MEASUREMENT_KEYS`, unconverted) and `derived.used_fraction = 1 - remaining_fraction` only when `remaining_fraction` exists. `--json` carries `schema_version: "1.0"`.
- Exit codes (existing contract): 0 ok (including "no evidence", overall UNKNOWN), 4 unreadable/corrupt storage or corrupt evidence rows, 5 database or observation tables unavailable, 6 future schema version.
- `diag health` now adds an `observations` object (`source_db`, `status`, `latest_total`, `by_state`, `error`) read from `--obs-db` (default `--db`); an unavailable observation DB is reported inside that object and does not change the stream result or exit code.
- The diag extension is imported lazily inside `main()`; the Core-only path (E2E-009) still works with extensions blocked.
