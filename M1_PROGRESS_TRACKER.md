# PeerHub M1 Very Simple Renewal — 진행 상황 및 협업 핸드오프

> **원칙**: Side-by-side vertical slice (기존 코드 강제 축소 대신 신규 M1 Core를 안전하게 병렬 구축).  
> **Core 4개 엔티티**: `Peer`, `Stream`, `Record`, `Offset` (Core는 어떤 Extension도 import하지 않음).  
> **1st-party 확장**: `Session Bridge`, `Observation`, `Readonly Diag`.

---

## 📌 전체 진행 체크리스트

### Phase 1: 스펙 동기화 및 검증 환경 준비
- [x] Git worktree 분리 (`feat/m1-very-simple-renewal` 브랜치 / `D:\PkgDev\workspace\peerhub-m1-renewal`)
- [x] M1 최신 스펙 문서 동기화 (`docs/m1_spec/`)
- [x] `docs/m1_spec/tools/validate_package.py` 검증 통과 (`RESULT=PASS`, 15개 표준, 109개 커맨드 분류 완료)

### Phase 2: Core 도메인 모델 (`peerhub/m1/models.py`)
- [ ] JSON Schema 2020-12 스키마와 완벽 호환되는 4대 Core 모델 구현
  - [ ] `Peer`: `peer_id`, `display_name`, `adapter_ref`, `metadata`, `created_at`
  - [ ] `Stream`: `stream_id`, `title`, `state (OPEN|CLOSED)`, `members`, `revision`, `created_at`, `metadata`
  - [ ] `Record`: `record_id`, `stream_id`, `position`, `author_peer_id`, `kind`, `body`, `targets`, `reply_to`, `refs`, `idempotency_key`, `payload_digest`, `created_at`, `appended_at`
  - [ ] `Offset`: `peer_id`, `stream_id`, `read_through_position`, `revision`
- [ ] Canonical JSON 직렬화 & SHA-256 payload digest 생성 로직

### Phase 3: Core 영속성 계층 (`peerhub/m1/store.py`)
- [ ] SQLite WAL + Foreign Keys 기반 순수 Core 스토어 구현
- [ ] Monotonic Append Position 보장 (트랜잭션 기반 canonical ordering)
- [ ] Idempotency 보장 (동일 키 + 동일 페이로드 = 기존 레코드 반환, 동일 키 + 상이 페이로드 = CONFLICT 에러)
- [ ] Offset Compare-And-Swap (CAS) 갱신 로직

### Phase 4: Core 불변식 및 TDD 검증 (`tests/unit/m1/test_core_contract.py`)
- [ ] 스키마 유효성 테스트
- [ ] 동시 append 순서 보장 테스트
- [ ] Idempotent replay & conflict 테스트
- [ ] Offset CAS 동작 테스트
- [ ] Core Invariant 검증 (Core가 Extension 모듈을 import하지 않는지 정적 검사)

### Phase 5: First-party Extensions (M1)
- [ ] **Observation & Diag**: Read-only 진단, 쿼터/rate/세션 관측 모델
- [ ] **Session Bridge**: Single-active delivery claim, runtime execution certainty (`MAY_HAVE_STARTED` 등)

---

## 📝 현재 작업 상태 (Current Status)
- **작업 브랜치**: `feat/m1-very-simple-renewal`
- **현재 진행 중**: **Phase 2 (Core 도메인 모델 작성)**


## Wave 0 / T0 (2026-10-03)
- Wave 0: 26/26 catalog ids implemented and green (tests/m1/{meta,architecture,schema,property}); details docs/m1_impl/wave0_report.md; open questions docs/m1_impl/OPEN_QUESTIONS.md. Review gate pending.
- Traceability: `python tools/m1_traceability.py [--upto N]` (map in docs/m1_impl/waves.json).
- T0: admission denial reason in `peerhub ask` errors (separate commit).

## Wave 1 (2026-10-03)
- Wave 1: 39/39 catalog ids green (tests/m1/core, tests/m1/property/test_prop_core.py, reworked SCH-011/012/013 per D-W0-1); details docs/m1_impl/wave1_report.md. 82 tests passed, traceability --upto 1 ok, validate_package PASS. Review gate pending; Q-W1-1..5 in OPEN_QUESTIONS.md.

## Wave 2 (2026-10-03)
- Wave 2: 28/28 catalog ids green (SQL-001..011, CON-001..011, MP-001..004, MIG-001/002); tests/m1/{integration,concurrency,migration}; details docs/m1_impl/wave2_report.md. 134 tests passed in tests/m1 + tests/unit/m1 (after cx.pro gate fixes); concurrency/MP run 3x stable; traceability --upto 2 ok; validate_package PASS. Review gate pending; Q-W2-1..6 in OPEN_QUESTIONS.md.

## Wave 3 (2026-10-03)
- Wave 3: 22/22 catalog ids green (CLM-001..003, CERT-001..003, BRG-002..007, 010..019); tests/m1/bridge/*, fakes tests/m1/fakes, harness tests/m1/harness/bridge.py; production peerhub/extensions/bridge.py (+ `renew`/`fenced` on bridge_claims.py). 188 passed in tests/m1 + tests/unit/m1; bridge+concurrency set 3x stable (71 each); traceability --upto 3 ok; validate_package PASS. Details docs/m1_impl/wave3_report.md; Q-W3-1..7 in OPEN_QUESTIONS.md. Review gate pending.
- Wave 3 gate fixes (cx.pro BLOCK, D-W3-1..7): invoke marker, scope/callback binding, authenticated reconcile, no false ack, REPLACE-proof triggers, binding, pending control Records. 212 passed; bridge+concurrency 3x stable (95); traceability --upto 3 ok; validate PASS. Q-W3-8/9 added. Re-gate pending.

## Wave 4 (2026-10-03)
- Wave 4: 11/11 catalog ids green (CTL-001..005, CTX-001..003, BRG-001/008/009); tests/m1/control/*, production bridge.py + catchup.py + direction.py; Wave-3 `pending_control` interim replaced (controls consumed/gating). Details docs/m1_impl/wave4_report.md; Q-W4-1..9 in OPEN_QUESTIONS.md. Review gate pending.
- Wave 4 gate fixes (cx.pro BLOCK, D-W4-1..10): control fencing, invoke-time gating, stranded recovery, redirect scope/budget, boundary validation, direction atomicity, position precedence, cancel-skip reverted, Offset-based catch-up. control 109 / concurrency 17 (3x), full dirs green; traceability --upto 4 ok; validate PASS. Q-W4-10 + OWNER items in OPEN_QUESTIONS.md. Re-gate pending.
- Wave 4 re-gate fixes (D-W4-8b/2b/5b/9b): history-based fresh bootstrap within budget, gate before first creation, full boundary payload validation, latest-by-position RETRY. control 119 (3x), concurrency 17 (3x), other dirs green, traceability --upto 4 ok, validate PASS.
- Wave 4 re-gate 3 fixes: boundary metadata verified against recomputed projection, authorization INSERT/move triggers fully validated, triggers re-installed on reopen (bridge + claims). control 127 (3x), concurrency 17 (3x), other dirs green, traceability --upto 4 ok, validate PASS.
- Wave 4 re-gate 4 (ag.pro): 4 real claims fixed (consumed_attempt write-once, boundary tied to first delivery, legacy triggers dropped on reopen, pinned redirects budget-bounded), claim 5 shown false. control 134 (3x), concurrency 17 (3x), other dirs green, traceability --upto 4 ok, validate PASS.


## Wave 5 (2026-10-04)
- Wave 5: 30/30 catalog ids green (OBS-001..018, DIA-001..010, READ-001/002). Production: `peerhub/extensions/{observation_model,observation,diag}.py` (ReadonlyDiag split into its own module; old `observation_and_diag.py` removed), schema copies in `peerhub/extensions/schemas/`. Tests: `tests/m1/{observation,diag}/`, `tests/m1/concurrency/test_dia_snapshot.py`, `tests/m1/core/test_read_boundaries.py`, ARCH-004 at module level. D-W0-2 / D-W0-4 closed. Details docs/m1_impl/wave5_report.md; Q-W5-1..13 in OPEN_QUESTIONS.md. Review gate pending.
- Wave 5 gate fixes (ag.pro, 2 findings): runtime sqlite authorizer on Diag + Observation read connections, static checker flags dynamic SQL, resource_pools no-replace trigger + immutability audit. Details in wave5_report.md.

## Wave 6 (2026-10-04)
- Wave 6: 27/27 catalog ids green (FLT-001..014, E2E-001..012, SEC-001). Production: typed storage faults + open-time integrity probe + test seams in `peerhub/m1/store.py`, session mapping bound to workspace generation (FLT-008) in `peerhub/extensions/bridge.py`, Diag `quick_check`, Core-only CLI (lazy Diag import, storage exit code 4, diag-unavailable 5). Tests: `tests/m1/{fault,e2e,security}/`, harness `crash_workers.py`, `core_only.py`. D-W2 deferral (FLT-009/011/012/013) closed. Details docs/m1_impl/wave6_report.md; Q-W6-1..7 in OPEN_QUESTIONS.md. ag.pro review fixes applied (rollback guard, size-threshold preflight, SEC-001 hardening). Review gate pending.

## Wave 7 (2026-10-04)
- Wave 7: 19/19 catalog ids green (REL-001..014, IMP-001..004, MIG-003). Production: `peerhub/m1/legacy_import.py` (importer), `schema_version.py`, CLI `legacy-import` + exit 6/7, store `transaction()`/`_insert_record`, `tools/m1_release_evidence.py`, packaging fixes (peerhub-m1 entrypoint, jsonschema dependency, declared matrix), ci.yml matrix, publish.yml live gate. Tests: `tests/m1/package/`, `tests/m1/migration/test_{imp_legacy_importer,mig_003_cutover}.py`. Details docs/m1_impl/wave7_report.md; Q-W7-1..10 (OWNER: 1, 2, 3, 5). Review gate pending.
- Wave 7 gate fixes (ag.pro): per-component importer idempotency (offsets added later import; conflicts reported), console encoding respected (errors=replace, lossless ASCII JSON), REL-012 dispatch check, REL-006 YAML mutations. Q-W7-11..13. Re-gate pending.

## Wave 8 + T1 (2026-10-04)
- Wave 8: 6/6 catalog ids present (LIVE-CC-001/CX-001/AG-001, LIVE-004/005/006); live canary run once manually: cc 2.1.288, ag 1.2.16, cx 0.160.0 all delivered/TERMINAL, project tree unchanged; evidence `docs/m1_impl/live_evidence/2026-10-03.json`. T1: real adapters `peerhub/extensions/adapters/` (cc/cx/ag), 40 offline tests with fake CLIs, 14/14 probes killed. Q-W8-1..7 (OWNER: no real resume, no interrupt/steer). Details in wave8_report.md. Not done: SOAK, cx final review.

## Wave 9 (2026-10-04)
- SOAK-001/002 implemented (evidence-only capacity, hard correctness oracles); 210/210 catalog ids present. `tests/m1/soak/` (smoke runs by default; scaled `-m soak` + `PEERHUB_M1_SOAK=1`), `.github/workflows/soak.yml` (weekly, non-blocking, uploads evidence), moderate-scale evidence in `docs/m1_impl/soak_evidence/`. 8/8 probes killed. Q-W9-1..6 (OWNER: ratify SLO / full scale). Details in wave9_report.md. Not done: full-scale run, run on a real CI runner, cx final review.
