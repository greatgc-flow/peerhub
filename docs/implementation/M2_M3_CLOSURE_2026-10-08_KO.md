# M2/M3 계약 closure 기록 — 2026-10-08

이 문서는 `CONTINUITY_COLLABORATION_AUDIT.md`, `MCP_PORT_AUDIT.md`, `MEMORY_SEARCH_AUDIT.md`와 `REMAINING_WORK_*`에 남아 있던 항목을
닫은 결정과 근거, 증거, 거절한 것, 남은 한계를 기록한다. 구현 증거이며 frozen 계약 수정이나 VERIFIED 승격이 아니다.

## 원칙 (ToGo 09_ROADMAP: M2/M3 Pre-TDD Review)
Core는 Peer/Stream/Record/Offset 그대로이며 extension을 import하지 않는다 · Record가 authority, projection은 재구축 가능 ·
UNKNOWN은 healthy/unlimited/0이 아니다 · MAY_HAVE_STARTED는 blind replay 금지 · 근거 없는 AI 판단은 truth가 아니다 ·
Extension→Extension 구현 import 금지(조합은 `peerhub/cli`) · 베리심플(solver, daemon, marketplace, raw shell 없음) ·
지난 배포분 호환/마이그레이션은 고려하지 않는다(M2/M3는 배포된 적이 없다).

## 슬라이스별 결정

| 슬라이스 | 결정 (원칙 근거) | 구현 | 증거(테스트) |
| --- | --- | --- | --- |
| M2.0 Host | boot()가 `extensions_dir`의 manifest를 발견하고 깨진 manifest는 보고만 하고 건너뜀(failure isolation). 의존성은 `ext_x` 또는 `ext_x==정확한버전`만(solver 거절). boot 시 ENABLED인데 의존성이 깨진 확장은 FAILED(fail closed). 등록된 버전이 바뀐 manifest는 조용히 채택하지 않음. entrypoint가 없으면 ENABLED가 되지 않고 FAILED | `host.py`, `manifest.py` | `test_host_boot_and_dependencies.py` |
| M2.1 Artifact | 중복 commit 검증은 스트리밍(전체 로드 금지), `stage_stream(max_size)`, shard 디렉터리 fsync(POSIX), GC `min_age_seconds` 유예, `export_verified`(검증 통과 시에만 목적지 게시) | `artifact.py` | `test_art_gc_bounds.py` |
| M2.2 Work | CAS는 캐시된 projection이 아니라 ordered stream Record 기준(`_authoritative`). 낡은 projection은 수리. append 후 reducer가 내 Record를 채택했는지 확인(`_confirm`), 레이스에서 진 변경은 성공으로 응답하지 않음. rebuild는 단일 트랜잭션 | `work.py` | `test_work_authoritative_cas.py`, `test_projection_atomic_rebuild.py` |
| M2.3 Skill/Capability | 동일한 authoritative CAS. 트리 digest는 길이 접두 framing(경계 충돌 제거; 미배포라 버전 필드 불필요). `rebuild_index(skill_roots)` 제거: 재구축은 Record만 사용, 디스크 drift는 `verify_skill_integrity`가 탐지. 폴백 frontmatter 파서가 인라인 리스트를 PyYAML과 동일하게 해석 | `skills.py` | `test_skill_catalog_authoritative_cas.py`, `test_skill_catalog.py`, `test_art_gc_bounds.py` |
| M2.4 MCP | 이전 audit 항목은 `MCP_PORT_AUDIT.md`의 "Fixed findings"에서 이미 닫힘(공개 port, 세션 handle, 마스킹). 이번에 추가 작업 없음 | `mcp.py` | `test_mcp_ports.py` |
| M2.5 Backup/Recovery | 배타 복원 경계 = workspace 디렉터리 전체. 모든 `*.db`가 idle이어야 복원(활성 writer가 있으면 거부), 파생 DB는 swap으로 폐기되고 재구축. 하드 크래시 후 이전 디렉터리의 모든 DB가 복구됨. `rebuild_derived_projections`는 Work를 import하지 않고 projection port를 받음 | `backup.py` | `test_restore_extension_dbs.py`, `test_backup_*.py` |
| M2.6 Eval | `run_eval`이 trace/dataset digest를 내용에서 재계산해 검증하고(옵션: Artifact store의 dataset blob 확인), 보고서는 `target_digest`에 묶임. `verify_report_sources` 제공 | `eval.py` | `test_eval.py` |
| M3.0 Search | `search(metadata={key: scalar})` 정확 일치(타입·값), limit은 필터 후 적용 | `search.py` | `test_search_exact_metadata.py` |
| M3.1 Memory | 옛 불완전 이벤트: 기본은 fail closed. `quarantine=True`면 이벤트 단위로 원자적으로 건너뛰고 reason과 함께 목록화(조용한 손실 금지) | `memory.py` | `test_memory_search_recovery.py` |
| M3.2 A2A | Record journal(`a2a_journal.py`): 원격 호출 전에 `submitting`을 durable로 기록, 재시작 시 미해결 submitting은 uncertain(blind replay 금지). `reconcile_task`: 원격이 아는 task면 채택, 원격이 확실히 모른다고 답하면 not_started로 기록하고 **새 attempt**로 재시도 허용, 그 외 실패는 uncertain 유지. 단일 외부 binding(`a2a_http.py`): JSON-RPC over HTTP, https 또는 loopback http만, redirect 금지, 응답 크기 제한, 매핑 없는 상태는 fail closed | `a2a.py`, `a2a_journal.py`, `a2a_http.py` | `test_a2a_durable_http.py` |
| M3.3 Routing / Target Catalog | **mutable Runtime Target Catalog 관리 surface는 만들지 않음(거절)**: 이것은 M1의 최소 model/profile facts이며 선언적 profile 계층(workspace→global→packaged TOML)과 `ask --profile/--model/--effort`, Observation 증거가 이미 충족한다. 레거시 `node bind-profile`은 이 구조로 대체됨 | — | — |
| M3.4 Orchestration | 누적 비용: `bounds.cost_budget`와 결과의 `cost`. 예산이 있으면 모든 시도가 비용을 보고해야 하고 미보고는 unknown이지 0이 아님. 지출은 journal에서 시도 전체를 합산해 재시작으로 회피 불가, 시도 전/후에 검사. 조건: 결정적 predicate `when`/`stop_if`(표현식 언어 거절), 건너뛴 단계의 dependent도 건너뜀. 병렬: opt-in thread pool(`max_fanout` 한도), journal append 직렬화. `max_depth`를 의존 체인에서 실제로 계산 | `orchestration.py` | `test_orchestration_m3_closure.py` |
| M3.5 Approval | 변경 없음(기존 exact-effect single-use) | — | `test_approval.py` |
| M3.6 Runtime Port | hard-cancel: `ProcessRuntimeAdapter`가 capability를 고정 argv 템플릿(shell=False, 선언된 파라미터만)의 별도 프로세스로 실행하고 `cancel`은 프로세스 트리를 kill하고 종료를 **확인**한다. 확인 실패 시 `RuntimeCancellationError`이고 job은 RUNNING으로 정직하게 남는다. `HardDeadlineStepRunner`는 deadline에 작업을 죽이고, 확인 실패는 MAY_HAVE_STARTED. `require_hard_deadline=True`는 이를 선언한 runner에만 성공 | `runtime_port.py`, `orchestration.py` | `test_runtime_port_process.py` |

공통: SQLite 트랜잭션이 연결을 닫지 않아 Windows에서 projection DB 삭제/복원이 막히던 문제를 `sqlite_tx`로 수정(host/skills/work 19곳).

## Exit 증거 (ToGo 09_ROADMAP)
- **M2 Exit** "Extension을 모두 꺼도 M1 정상": `test_m2_m3_exit_gates.py::test_m1_works_with_every_m2_m3_extension_disabled` — 모든 M2/M3 모듈 import를 차단한 프로세스에서 M1(Core CRUD, diag, 모듈 import)이 동작하고 M2/M3 모듈이 로드되지 않음. 차단 control 포함.
- **M2 Exit** "derived projection 삭제·재구축 logical equivalence": 같은 파일의 `test_deleting_every_derived_projection_and_rebuilding_from_records_is_logically_identical` — Work/Skill/Capability/Memory DB를 전부 삭제하고 Record만으로 재구축해 동일 상태.
- **M3 Exit** Search rebuild / Memory supersession·revocation replay / deterministic Routing / bounded Orchestration crash recovery / exact-effect Approval: 각각 기존 `test_search*.py`, `test_memory*.py`, `test_routing.py`, `test_orchestration*.py`(재시작 후 비용·stop 유지 포함), `test_approval.py`.
- **확장 간 독립**: `test_arch_extension_graph.py`가 import 그래프를 문서화된 allowlist에 고정(새 cross-extension import는 실패, 죽은 항목도 실패).

## 거절한 것 (과설계)
mutable Runtime Target Catalog(위) · SemVer 범위/의존성 solver · 파일시스템 watcher/hot reload · 선택적 파생 데이터 보존형 복원 ·
AST/JSONPath 조건식 · 분산 큐 · 우아한 종료 단계(SIGINT→SIGKILL) · 다중 통화/동적 가격.

## 정직한 한계
- **A2A**: HTTP binding은 같은 JSON-RPC subset을 구현한 in-process 서버로 검증했다. **제3자 A2A 서버와의 상호운용은 검증하지 않았다.**
- **비용**: 시도 사이에 검사하므로 단일 시도(병렬에서는 최대 `max_fanout`개의 진행 중 시도)가 예산을 넘길 수 있다.
- **병렬 실행**은 opt-in이며 runner의 thread-safety는 호출자 책임이다.
- **ProcessRuntimeAdapter**는 신뢰된 argv를 가정한다(실패 격리와 취소이지 보안 sandbox가 아님). Python plugin도 in-process 실행이다.
- Windows long-path 동작과 symlink 권한이 필요한 테스트는 이 호스트에서 실행하지 못했다.
- `idempotent=True`로 선언된 단계의 MAY_HAVE_STARTED 재시도는 journal 없는 실행에서만 허용된다(명시적 선언을 증거로 간주). 내구 실행에서는 항상 reconcile이 필요하다.

## 승격 상태
M2/M3를 VERIFIED로 올리지 않는다. 승격은 M1 Exit(후보 커밋에 묶인 live·패키지 증거와 CI)가 먼저 PASS한 뒤에 한다.
