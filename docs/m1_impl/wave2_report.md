# Wave 2 report (28 ids: SQL-001..011, CON-001..011, MP-001..004, MIG-001/002)

Tests: tests/m1/integration/test_sql_store.py, tests/m1/concurrency/{test_con_threads,test_con_claims,test_mp_processes}.py, tests/m1/migration/test_mig_runner.py (+ TD-14 future-version test).
Harness extended: `Workspace`-backed reopen/workspace_generation, `claims()`, `compute_state_digest`, ManualClock, `mp.py` (spawn processes + real threads, barrier, bounded timeouts, diagnostic dump), `mp_workers.py`, `migration_fixtures.py`.

## RED (new tests vs. Wave-1 production + stubs for new modules): 18 failed / 13 passed
| ids | red reason | GREEN |
|---|---|---|
| SQL-001/004/006 | no `CoreStore.connect()` / `read_uow()` | public raw connection with Core pragmas; `mode=ro` + `query_only` UoW |
| SQL-002/003, MIG-001/002, TD-14 | no migration runner (`NotImplementedError` stub), no version metadata | `peerhub/m1/migrations.py`: ordered steps, one tx, `PRAGMA user_version`, future-version reject, fault points |
| SQL-005, CON-010 | no commit-window / head-check seams (hook never fired -> bounded wait timeout) | `fault_hook` points `append.before_commit`, `offset.before_head_check` |
| SQL-008/010, CON-006/007/008, MP-004 | claim kernel / restore stubs `NotImplementedError` | `bridge_claims.py` fenced claims + `Workspace.restore_snapshot` / `replace_generation` |
| Green on first run (existing Wave-1 behaviour) | SQL-007/009/011, CON-001..005/009/011, MP-001/002/003 | none needed; effectiveness shown by mutation probes |

## Mutation probes (production temporarily broken, test fails, restored)
BEGIN IMMEDIATE -> BEGIN (CON-001, MP-003 fail); foreign_keys pragma removed (SQL-001); offset head check removed (CON-010); idempotency conflict swallowed (CON-004); migration ROLLBACK -> COMMIT on fault (MIG-001); claim fence ignoring generation (CON-007 same-owner retake test; first version survived, test added); TD-19 `>=` -> `>` in heartbeat and `<` -> `<=` in acquire (CON-007); takeover not bumping generation (CON-007); commit-window hook removed (SQL-005); read_uow with both `mode=ro` and `query_only` off (SQL-006; either guard alone is sufficient, so single-guard mutation survives by design).

## Decisions (spec citations)
- TD-14: schema version in `PRAGMA user_version` (ARCH-002 keeps exactly five Core tables); unknown/future version raises `SchemaVersionError`, no downgrade/repair. TD-04/19/25: claim token = workspace gen + claim gen + owner; expiry `now >= expires_at`; `guard` runs inside the Core write tx. TD-01: positions per stream, gaps allowed. Persistence spec: WAL, foreign_keys, busy timeout, workspace identity/generation.
- Open questions Q-W2-1..6 in OPEN_QUESTIONS.md (minimal claim kernel before Wave 3, user_version, no production N+1, busy semantics, CON-010 orderings, expired-heartbeat).

## Gate
pytest tests/m1 tests/unit/m1: 115 passed; Wave-2 concurrency/integration/migration set run 3x: 32 passed each, stable; m1_traceability --upto 2 rc=0 (wave 2 28/28); validate_package PASS.
Not done: Core fault matrix FLT-009/011/012/013 (busy/full/readonly/corrupt) belongs to Wave 6; CoreStore does not yet open read-only/corrupt DBs fail-closed beyond TD-14.
