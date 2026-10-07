# 잔여 작업 실행 기록 — 2026-10-06

## 기준과 순서

ToGo R9 `START_HERE_KO.md`와 frozen 계약이 기준이며, `legacy/v0-main-final`은 동작·복구 참고다. AGY handoff의 `diag --fresh`, 단순 subprocess 호출만으로 ask 완료 판정, 검증 전 버전/tag/publish 제안은 채택하지 않았다.

협업 메시지는 영어로 제한했다. CX 작업은 ask/Search/Memory, 승인/실행/라우팅, MCP/continuity로 파일 소유권을 나눴다. AG는 read-only 검토에만 사용했다. AG 검토가 예상보다 넓은 문맥을 읽고 시간 제한에 걸려 추가 탐색을 중단했다. 불확실 재실행과 workspace 우선순위에 대한 AG 지적은 기존 Bridge guard와 workspace 회귀 테스트를 확인한 뒤 채택하지 않았다.

## 이번 실행에서 보완한 사항

- `ask`: legacy `-p/--profile`의 short/qualified ID, 선언적 model policy, 명시적 model/effort override, silence timeout. 잘못된 설정은 DB 초기화 전 거부한다. 불확실 실행은 재실행/ack하지 않는다.
- `diag --live`: 매 프레임 별도 read-only snapshot; interval/count와 NDJSON. provider 수집은 여전히 명시적 `observation refresh`만 담당한다. 색상·governed domains·policy routing을 Diag에 복원하지 않는다.
- 승인: 독립 consumer의 token 이중 소비 차단, stale lifecycle CAS, terminal-safe expiry.
- 실행: 모든 재시도/resume의 예산, final-step deadline 검사, accepted-plan Record binding, durable RUNNING/owner/deadline와 crash uncertainty. hard-deadline 요청은 지원하지 않는 arbitrary callback에 대해 fail closed 한다.
- Routing: Unicode tie-break, UNKNOWN 기본값, finite scalar 검증, 불변 입력/출력 snapshot digest와 replay.
- MCP: 실제 Core/Work/Artifact/Skill 공개 포트, protocol content와 validation, capability-bound writes, secret-safe errors, timestamp-preserving idempotent retry.
- Host/Artifact/Skill/Eval: migration authorizer/transaction, partial-write 처리·staging/CAS 검증, skill integrity/reducer validation, finite scores. Work reducer의 stale/cross-stream/illegal transition도 차단한다.
- Search/Memory: 전체 snapshot pagination, public Artifact/Skill sources, rebuild rollback, authoritative Memory lifecycle/revision/revocation replay, conservative multilingual byte/token bound.
- A2A: 명시적 transport binding, unbound fail closed, submission uncertainty/no blind replay, remote identity/state 검증, 확인된 cancellation, 방어적 snapshot/locking. loopback은 명시적 테스트 binding이며 remote 가용성 증거가 아니다.
- 백업: 검증된 staging publication, skill source 포함, secret-bearing truth는 수정하지 않고 fail closed, traversal/unlisted file/link/reparse 차단, staging 재검증, 전체 Work 페이지 복구. offline restore는 새 lineage/epoch를 기록하고 stale CoreStore를 fence하며 기존 디렉터리를 복구 사본으로 남긴다. 임의의 열린 SQLite handle에 대한 online restore 또는 두 directory rename 사이의 power-loss atomicity는 주장하지 않는다.
- CI: 후보 브랜치에도 전체 지원 matrix가 실행되며 각 cell JUnit을 보관한다. 과거 evidence를 새 후보의 PASS로 재사용하지 않는다.

## 검증 및 release 판정

표적 검증은 개별 JUnit에 보존한다. 최종 전체 기본 스위트, 후보 commit/CI matrix, 새 package와 release evidence는 이 source snapshot 이후 별도로 생성한다. 실패한 이전 실행과 재검증을 합쳐 단일 green 결과로 만들지 않는다.

실제 공개 ask의 cx/ag 성공 및 동일 request-id의 `recovered_terminal`을 확인했다 (`.peerhub/public-ask-live-junit.xml`, 2 PASS). quota 수집은 cx/cc/ag의 7개 window를 MEASURED로 저장했다 (`.peerhub/remaining-validation/core.db`). Claude C-7D remaining_fraction=0이므로 성공 ask 증거는 미완료다. quota 소진을 성공이나 무제한으로 취급하지 않고 반복 유료 호출을 하지 않는다.

version은 `0.11.0`을 유지한다. gate가 모두 PASS하기 전 release/tag/publishing 또는 M2/M3 VERIFIED promotion은 하지 않는다. main 직접 push/merge 없이 별도 후보 브랜치에서 CI를 검증한다.

## 아직 promotion을 막는 계약 항목

상세 감사는 [continuity/collaboration](CONTINUITY_COLLABORATION_AUDIT.md), [MCP/continuity](MCP_PORT_AUDIT.md), [Memory/Search](MEMORY_SEARCH_AUDIT.md)를 함께 읽는다. 각 문서는 감사 snapshot의 남은 항목을 기록하며 아래 integration closure가 우선한다.

| 범위 | 남은 증명/설계 |
| --- | --- |
| M1 cutover/release | 최종 후보 전체 matrix, Claude 실제 성공, candidate-bound package/live/evidence. native resume/control은 unsupported이며 새 capability를 허위 광고하지 않는다. |
| Continuity | authoritative writer CAS/catch-up 및 atomic projection rebuild; Host boot discovery/exact dependency 버전; Artifact GC/fsync/streaming bound; Skill rescan/digest framing/parser parity; Eval authoritative source digest 검증. |
| Collaboration | 실제 hard-cancellable runtime와 누적 cost/parallel/conditional execution closure; incomplete historical Memory event의 명시적 migration 정책; exact metadata search predicate; observed Runtime Target Catalog 관리. |
| Recovery/remote | 모든 raw extension writer까지 포함한 offline/exclusive ownership·crash recovery protocol; 실제 외부 A2A transport interoperability와 durable restart/reconciliation. cluster/daemon/replication은 필수 M3 범위가 아니다. |

백업/MCP/Search/Memory/라우팅/실행의 초기 감사 지적 중 이번에 수정한 사항은 위 구현 목록과 회귀 테스트가 closure 근거다. 표의 unresolved 사항을 로컬 테스트 개수로 덮지 않는다. frozen spec과 이전 maturity/evidence snapshot은 수정하지 않는다.
