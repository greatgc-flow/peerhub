# Wave 6 report (27 ids: FLT-001..014, E2E-001..012, SEC-001)

Tests: `tests/m1/fault/{test_flt_storage,test_flt_bridge,test_flt_observation}.py`, `tests/m1/e2e/{test_e2e_fake_runtime,test_e2e_core_only}.py`, `tests/m1/security/test_sec_001.py`, `tests/m1/meta/test_meta_wave6_inventories.py` (every EX/INT row mapped to a wave<=6 id has a test). Harness: `tests/m1/harness/{crash_workers,core_only}.py`. Fake runtimes only; real spawned processes with os._exit crash points, barriers and bounded joins.

## RED (right reasons)
- Storage (8 failed first): FLT-011 raw OperationalError masked by "cannot rollback - no transaction is active" (real bug in `_tx`); FLT-012 raw readonly error; FLT-013 header garbage raised raw "file is not a database", page corruption "DID NOT RAISE" at open and Diag reported OK; read-only outdated schema raw error.
- FLT-008: provider session ext-1 was resumed after restore (resume call in log). E2E-009: `peerhub.m1_cli` imported `peerhub.extensions.diag` at import time (ImportError with extensions blocked).
- Already satisfied by Wave 3-5 code (characterization, killed by mutation probes instead): FLT-001..007, 009 (after seam), 010, 014, E2E-001..007, 010..012, SEC-001.

## Production changes
`store.py`: Storage*Error mapping (`storage_errors`), safe rollback, `_preflight` quick_check before migrations, seams `busy_timeout_ms`, `conn_init`, `append.after_commit`. `bridge.py`: `bridge_sessions.workspace_generation` (+ALTER for old stores). `diag.py`/`observation_model.py`: Diag quick_check (authorizer allows the read pragma). `m1_cli.py`: lazy Diag import, exit 4 storage / 5 diag unavailable. Existing test fixed: raw INSERT in test_w3_gate_fixes uses named columns.

## Mutation probes (foreground, restored, git diff checked): 14 run, 12 killed, 2 equivalent survivors
Killed: no preflight; FULL/READONLY not mapped; no after_commit hook; busy timeout 0; session resumed across generations; claim ignores workspace generation; terminal not materialized on recovery; no heartbeat fence mid delivery; eager Diag import; quotes escaped in body (SEC-001); MAY marker skipped.
Survivors: connect-timeout-only change (the pragma still applies: equivalent, killed when the pragma is changed); Diag quick_check disabled (every crafted corruption makes quick_check raise, still FAILED: equivalent, Q-W6-7).

## Decisions / open questions
D-W6-1..3; Q-W6-1..7 (OWNER: Q-W6-1 generation-bound sessions, Q-W6-2 open-time integrity cost).

## Gate
Full `tests/m1 tests/unit/m1`: 460 passed. fault+e2e+security+concurrency 3x green (81 each). traceability --upto 6 ok (wave 6: 27/27); validate_package PASS.
Not done: Diag views of bridge tables, real adapters (T1), ClaimStore/Bridge own connections do not map storage errors (Core only), cx final review.

## Gate fixes (ag.pro review of cecbd5f)
Tests `tests/m1/fault/test_flt_hardening.py` (+ SEC-001 rework). RED: ClaimStore real SQLITE_FULL raised "cannot rollback" instead of FULL; preflight modes missing. Fixed: `rollback_quietly` everywhere, size-threshold preflight (Q-W6-2), old-schema upgrade fixture (claim 1 false as stated, verified), SEC-001 strengthened (NUL/controls, SQL-text recorder).
Probes (foreground, restored): 10 run, 9 killed (claims/migration rollback guard, ALTER upgrade, preflight always-full/always-light, string-built SQL, foreign_keys OFF, offset head bound, BEGIN vs IMMEDIATE killed by earlier-wave tests SQL-001/CON-010/BRG-014); survivor: observation rollback guard (shares the helper, the engine auto-rollback cannot be injected into its connection without a seam).
