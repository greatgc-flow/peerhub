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

## cx.pro gate fixes (post ca0ca79), RED probes first
RED (new tests vs. ca0ca79): 16 failed / 34 passed. Failing: restore validation + crash points (SQL-010 x9), read_uow snapshot (SQL-006), terminal finalization + scope binding (CON-008, MP-004), migration FK/check + future-version-before-WAL (MIG-002/TD-14), CON-010 handshake seam. New false-green guards that passed immediately (production was right, oracles were weak): SQL-011 independent oracle, CON-002 cardinality, CON-009 gaps, MIG-002 full preservation (+5 damaging-migration negative controls).
1. `Workspace.restore_snapshot`: validates (integrity_check, version <= supported, Core tables) before touching live state (`SnapshotInvalidError`); order = validate -> temp copy+fsync -> WAL checkpoint (abort if busy) -> NEW generation -> remove WAL/SHM -> atomic `os.replace`. Fault points restore.after_{validate,temp_copy,generation,replace}; each tested for integrity, complete old-or-new state, old tokens stale once the generation changed, and clean retry. Guarded append/ack/finalize rejected after a generation change.
2. Claims bound to (stream, peer): Core passes the write target to `guard(conn, stream_id=, peer_id=)`; mismatch -> `ClaimScopeError`. `ClaimStore.finalize_terminal` is a real fenced terminal write (`bridge_finalizations`, once per generation). CON-008 and real multi-process MP-004 assert current token ok, stale/forged rejected, state unchanged.
3. migrations: FK ON in migration connections, `foreign_key_check` before COMMIT (`MigrationIntegrityError`), future version rejected before WAL conversion (non-WAL fixture, header bytes unchanged).
4. CON-002 counts 400 responses, unique successes, durable rows == successes; MIG-002 asserts full Peers/Streams/members(order)/Records/Offsets preservation with damage controls; SQL-011 checked against raw-SQL + hashlib oracle.
5. `read_uow` begins a read transaction (snapshot test with concurrent writer); CON-009 asserts actual committed positions (TD-01); CON-010 handshake via `append.begin` / `offset.begin` hooks before release.
Mutation probes (all killed): skip snapshot validation; never change generation; guard ignores scope; finalize unfenced; FK pragma removed; foreign_key_check removed; version check after WAL conversion; read_uow without BEGIN; constant record digest. Surviving by design: not deleting the already-TRUNCATEd (empty) WAL; busy-abort path covered by its own test.
Gate: 134 passed; Wave-2 concurrency/integration/migration set 3x: 51 passed each; traceability --upto 2 rc=0; validate_package PASS.
