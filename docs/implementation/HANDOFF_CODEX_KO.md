# 다음 Codex 인계 — 사용자 요청으로 hand off & exit

## 현재 Git / 실행 상태

- Repository: `D:\PkgDev\workspace\peerhub`
- Branch: `feat/extension-cutover-2026-10-06` (origin에 push 완료)
- 최종 HEAD: `b7825042cdbedece67d8d6171475488f359cb3f0`
- 커밋:
  - `cafa02c`: extension-first CLI cutover / v0 retirement / 구조 정리 / durability 보완
  - `487a7dc`: SQLite credential 컬럼의 문자열·BLOB 검사 보완
  - `b782504`: schema 이동 후 `.gitattributes` byte 보존 및 실제 Git checkout 회귀 테스트
- `main` 직접 push/merge, PR 생성, version bump/tag/release/publish는 하지 않았다. Version `0.11.0` 유지.
- 인계 파일 생성 직전 worktree는 clean이었다. **이 파일만 새 untracked 인계 문서**이며, 실행 중 후보 SHA를 바꾸지 않으려고 추가 commit하지 않았다.
- 최종 원격 CI: https://github.com/greatgc-flow/peerhub/actions/runs/37455695962 (`b782504`, 인계 시 in_progress).
- 로컬 전체 pytest는 사용자 종료 요청으로 중단했다. 마지막 unified exec session `89909`는 Ctrl-C 후 exit 1을 확인했다. 진행 약 15%, 완료 결과가 아니다.
- 모든 협업 agent 작업은 종료됐다. 원격 CI는 취소하지 않고 계속 실행되게 두었다. 추가 모델/유료 호출을 하지 않는다.

## 사용자 의도와 기준

ToGo R9 `START_HERE_KO.md`와 frozen 계약이 기준이며 `legacy/v0-main-final` (`57a137cd6a0cc7e89124ea2b87b72995087627a5`)은 동작·복구 참고다. AGY handoff는 참고만 한다.

Core는 Peer / Stream / Record / Offset뿐이다. Diag는 read-only이며 수집/refresh/network/write를 하지 않는다. 기존 diag/ask 기능을 extension에 이관한 뒤 v0 로직/테스트를 제거한다. M1/M2/M3 이름은 runtime 구조에서 제거하되 frozen 문서·durable schema/table/wire 식별자는 보존한다.

사용자는 AG/CX 위임 협업, 영어 peer 통신, 토큰 절약과 꼼꼼한 진행을 요청했다. AG는 plan/read-only 검토에 사용했으나 넓은 context를 읽고 timeout되어 추가 탐색을 중단했다. AG의 일부 잘못된 지적은 기존 Bridge guard와 회귀 테스트로 반박하고 채택하지 않았다.

## 완료된 변경

1. Runtime `core / extensions / cli`, 테스트 `communication / continuity / collaboration`, 역할 기반 tools로 이동. 임시 `peerhub-m1` script와 v0 runtime/전용 테스트/tools retirement. 사용자 DB와 frozen evidence는 보존.
2. Durable ask: fenced delivery claims, response-before-offset, request-id retry/recovered_terminal, uncertain no blind replay, streaming/JSON, profile policy와 short `-p standard`, model/effort, silence/wall/output bounds.
3. Observation의 명시적 quota refresh; Diag quota/headroom/pacing 및 read-only live snapshot/interval/count/NDJSON. `diag --fresh`는 복원하지 않음.
4. Approval의 원자적 single use/CAS; Orchestration retry/resume 예산과 final deadline, optional accepted-plan Record journal/RUNNING certainty/owner/deadline/recovery; Routing Unicode/UNKNOWN/finite validation 및 immutable snapshot/replay.
5. MCP 실제 Core/Work/Artifact/Skill public ports, protocol framing/validation/capabilities/idempotency; Host migration authorizer/atomicity; Artifact staging/CAS integrity; Work/Skill reducer validation; Skill integrity; Eval finite score.
6. Search snapshot pagination/Artifact+Skill sources/rollback; Memory optional authoritative lifecycle/revocation/revision replay와 conservative Unicode context bound.
7. A2A explicit transport/loopback binding, unbound fail closed, uncertain submit no replay, remote identity/state/cancel validation, defensive copies/locking. 실제 외부 interoperability 완료를 주장하지 않음.
8. Backup verified staging/skill sources/secrets/path/link/reparse/manifest guards, offline recoverable directory replacement, fresh lineage/epoch, stale CoreStore fence, full Work pagination. Online/raw-writer/power-loss atomic restore는 미완료이며 명시적으로 제외.
9. Candidate-safe evidence aggregation: archived VERIFIED skips는 현재 PASS가 아니다. 동일 testcase identity의 실제 matrix PASS로만 foreign-cell skip을 해소; failure/error는 항상 blocking.
10. CI를 후보 branch에서도 실행하고 각 지원 cell JUnit을 보관하도록 갱신. `.gitattributes` runtime schema 경로도 `peerhub/core/schemas/*.json -text`로 수정.

v0 로컬 복구 사본: `.peerhub/retired-v0-a6c6964ed6f74daf990da36388d9968a/`. 사용자 데이터는 삭제하지 않았다.

## 검증 — scope를 섞어 VERIFIED로 만들지 말 것

- Ask/Search/Memory: 단일 green focused report **137 PASS**, `.peerhub/ask-memory-search-final-junit.xml`.
- Approval/Orchestration/Routing/Runtime의 마지막 allocated suite: **95 PASS**, 해당 agent 감사 기록 참조.
- Continuity: **160 PASS / 1 Windows symlink permission SKIP**; 이후 추가 safety regressions **23 PASS**.
- Evidence: **92 PASS** focused run + 마지막 targeted **12 candidate / 6 matrix tests PASS**.
- Parent CLI/watch/backup/transport focused run: **94 PASS** (후속 credential/schema 보완 전).
- 최종 credential 보완: Backup 원래+regression **26 PASS**, Pyright 0 errors.
- Windows checkout 원인 수정: checkout positive/negative controls + wire schema **18 PASS**. 이후 helper의 Git output decoding만 UTF-8/replace로 명시했다; 최종 전체 CI에서 재검증 필요.
- 전체 Pyright 0 errors, inventory check와 frozen Wave 0–9 traceability(210 IDs) PASS; source/package retirement와 importer 확인.
- 실제 공개 cx/ag ask + 같은 request-id 재사용: **2 PASS**, `.peerhub/public-ask-live-junit.xml` (cutover 작업 snapshot; 최종 HEAD proof로 재stamp하지 말 것).
- `cafa02c` 실제 live: **5 PASS / 7 deselected**, `.peerhub/g3.xml`, **cafa02c에 binding됨**. 최종 b782504의 live PASS로 재사용하지 말 것.
- quota 실제 refresh: cx/cc/ag **7 MEASURED windows**, `.peerhub/remaining-validation/core.db`. 당시 Claude C-7D remaining_fraction=0; 성공 ask 검증 미완료. 반복 유료 호출하지 않았음.
- Wheel/sdist `.peerhub/dist-487a7dc/` build PASS; 격리 설치 `.peerhub/candidate-installed/`에서 실제 credential guard PASS. 앞선 wheel에서도 restart read/readonly live/assets/retired script absence를 확인. 최종 후보 package/evidence는 별도 생성·binding 필요.
- 로컬 전체 실행들은 추가 보완 또는 사용자 종료로 중단했다. **최종 전체 단일 green JUnit은 아직 없다.** `.peerhub/final-candidate-junit.xml`이 존재하더라도 완료/HEAD를 확인하지 않고 사용하지 말 것.
- 이전 CI `37453671139`/487a7dc는 Linux4 PASS였으나 Windows3.11에서 schema CRLF/LF 불일치 3개 실패. 원인은 이동된 schema path에 Git -text 규칙 미적용; 마지막 commit으로 수정했다. 실패 artifact `.peerhub/failed-win311-487a7dc/matrix.xml` 보존. 후보 갱신으로 이전 CI는 cancelled; 전체 PASS가 아님.

## 다음 실행 순서

1. 최종 CI `37455695962`의 exact HEAD와 모든 8 cell 결과/실패 JUnit 확인. 실패가 있으면 원인 수정 후 새로운 후보에서 재검증.
2. 소스 변경을 멈춘 최종 상태에서 `python -m pytest -q --junitxml=.peerhub/final-candidate-junit.xml --maxfail=8`를 **완료까지** 실행. Pyright / inventory / traceability / clean build도 최종 scope로 확인.
3. 최종 wheel의 source/schema bytes와 격리 설치 확인. 원격 matrix JUnit과 GitHub run metadata를 보관하고 candidate-bound gate evidence를 생성. 기존 archived/이전 후보 evidence를 재stamp하여 새 PASS를 만들지 않는다.
4. Claude quota availability 확인 후 명시적 성공 live 검증이 가능할 때 진행. 최저 profile + tiny English prompt + 불필요한 retry 없음. 현재 quota/인증 실패는 UNKNOWN/UNAVAILABLE/HOLD이며 통과가 아님.
5. 실제 blocking gates가 닫힌 뒤에만 version/tag/release와 promotion 검토. 현재는 HOLD이며 M2/M3 VERIFIED를 주장하지 않는다.

실행 예:

```powershell
git status --short
gh run view 37455695962 --json headSha,status,conclusion,jobs
python -m pytest -q --junitxml=.peerhub/final-candidate-junit.xml --maxfail=8
python -m pyright
python -m tools.command_inventory --check
python -m tools.traceability --upto 9
```

Git push는 성공했으나 사용자 global credential helper가 옛 `D:\PortableDev\...\gh.exe`를 가리킨다는 warning이 있었다. 이를 task와 무관한 global 설정 변경으로 고치지 않았다.

## 남은 계약 항목 / 문서

- [실행 기록과 promotion backlog](REMAINING_WORK_KO.md)
- [구조 정리와 v0 복구](STRUCTURE_CLEANUP_KO.md)
- [Continuity/Collaboration 감사](CONTINUITY_COLLABORATION_AUDIT.md)
- [MCP/Continuity 감사](MCP_PORT_AUDIT.md)
- [Memory/Search 감사](MEMORY_SEARCH_AUDIT.md)

남은 큰 항목: authoritative writer CAS/catch-up와 atomic projection rebuild, Host boot/exact dependency, Artifact GC/fsync/streaming bound, Skill rescan/digest/parser parity, Eval source-digest validation, 실제 hard-cancellable/cost-bounded orchestration, incomplete Memory event migration 정책, exact metadata search, observed Runtime Target Catalog, raw extension writer까지의 restore ownership/crash protocol, 실제 A2A interoperability/durable reconciliation. Cluster/daemon/replication은 필수 M3 범위가 아니다.

이 작업 전체를 완료했다고 보고하지 말 것. 현재는 주요 이관·retirement·안전 보완 및 감사가 완료되었고, 최종 전체/matrix/live/evidence closure와 위 계약 항목은 미완료다.
