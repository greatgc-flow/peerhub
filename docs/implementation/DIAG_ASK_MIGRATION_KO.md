# diag / ask 확장 이관 — 수정된 작업 기준
> **2026-10-08:** `core.legacy_import`, 그 frozen fixture와 importer 테스트, `PEERHUB_CLI` selector는 모두 제거되었다. 단일 `peerhub` CLI만 남고, v0 소스는 Git 브랜치 `legacy/v0-main-final`에서만 접근한다. 아래 서술은 당시 기록이다.


이 문서는 1차 이관 기록이다. 이후 v0 retirement와 역할 기반 경로 정리는 [현재 구조 정리](STRUCTURE_CLEANUP_KO.md)를 따른다. v0 runtime은 현재 제품에서 제거되었다.

기준: `ToGo/PeerHub_VerySimple_MasterRoadmap_FINAL_R9_20261005/START_HERE_KO.md`와 그 문서가 지정한 Core / Session Bridge / Observation / Public CLI Cutover 계약.

- 작업 시작 main: `1d330a1281b99b8050698547091e488d40623d97`
- v0 비교·복구 기준: `legacy/v0-main-final` / `57a137cd6a0cc7e89124ea2b87b72995087627a5`
- AGY 인계안은 참고 자료다. M2/M3 소스가 존재하거나 merge됐다는 사실을 production promotion / VERIFIED로 간주하지 않는다.

## 이번 구현

`ask`는 기존 `extensions.bridge.Bridge` / `ClaimStore` / `CliRuntimeTarget`을 연결한다. 별도 generic dispatch / lease / governance 엔진을 만들지 않는다.

1. Peer identity와 provider kind는 구분한다. builtin alias는 자동 등록하고, 사용자 지정 Peer는 `peer register --adapter cx|cc|ag`로 연결한다.
2. 대화는 `--stream` 또는 `peer:<peer_id>:chat`이다. prompt와 response는 durable Record다.
3. 실행 전에 claim과 invocation marker를 기록한다. 응답을 append한 뒤 Offset을 ack한다.
4. `--request-id` 재시도는 원래 timestamp·prompt·author·model/profile binding을 유지한다. 완료된 응답은 재사용하고, 다른 payload/binding은 conflict다. 불확실한 실행은 자동 재실행하지 않는다.
5. CLI 시간·출력 제한을 적용한다. 성공 여부와 별개인 부분 출력은 vendor JSON을 그대로 노출하지 않고 텍스트로 표시한다. JSON 모드에서는 결과 객체만 출력한다.
6. 작업 소요 시간과 certainty는 `activity` Observation으로 기록한다. 요청 실행만으로 quota나 token 사용량을 추정하지 않는다.

`diag` 기본 호출의 숨은 v0 우회를 제거했다. `ReadonlyDiag`의 한 read-only snapshot으로 Peer / Stream / Observation / pool을 표시한다. 사용률·잔여량·window 경과·reset은 저장된 관측 근거가 있을 때만 표시한다. `MEASURED / STALE / UNKNOWN / ERROR / ABSENT / UNAVAILABLE`을 보존한다.

`observation refresh --peers cx cc ag`가 명시적 수집 명령이다. provider probe와 DTO, Windows direct-binary resolver 및 quota parsing/transport 테스트를 extension으로 이관했다. 이 경로는 v0 Runtime / dispatch / telemetry projection에 의존하지 않는다. Codex의 누락·NaN·범위 밖 사용률을 0 또는 정상 quota로 바꾸지 않는다.

기존 `diag --fresh`를 유지하는 인계안은 읽기 전용 계약 위반이므로 채택하지 않았다. 실행 예:

```powershell
peerhub observation refresh --peers cx cc ag
peerhub diag
peerhub diag quota --json
peerhub ask cx "Reply with OK" --stream demo --request-id demo-001 --json
peerhub ask cx --query-file prompt.txt --workspace . --model MODEL --effort low
```

`--db PATH`는 command 앞의 global option이다. 명시한 경로가 없더라도 다른 workspace DB로 대체하지 않는다. `--observation-db`는 `PEERHUB_DB`와 별도로 선택할 수 있다.

## 삭제한 부분과 유지한 부분

중복 초기 구현 `extensions/session_bridge.py`와 그 전용 `tests/unit/m1/test_extensions.py`를 삭제했다. 실제 Bridge claim / fencing / certainty 테스트가 이를 대체한다. 미래 schema 거부 테스트도 현행 store를 대상으로 정리했다. 삭제한 파일은 Git의 작업 시작 commit에서 복구할 수 있다.

Provider adapter는 현재 native session resume / interrupt / steer를 구현했다고 주장하지 않는다. Session Bridge의 fresh generation + bounded catch-up이 현재 continuity 경로다. v0 `--profile`, silence timeout, live monitor 및 optional-domain presentation을 그대로 복원했다고 주장하지 않는다. 필요한 UX는 각 확장 계약에 맞춰 별도로 이관하며 generic 플랫폼을 재도입하지 않는다.

## 검증과 release 경계

2026-10-06 local 검증: 실제 Codex와 AGY의 tiny ask가 각각 `TERMINAL`로 끝나 응답 Record / Offset을 남겼다. 실제 quota 수집에서 cx 2개 window, cc 1개 window, ag 4개 window를 관측했고 기본 diag가 표시했다. 당시 cc weekly quota는 소진 상태였으므로 Claude ask의 새 live 성공 근거는 만들지 않았다. Claude transport/parser/certainty는 hermetic adapter 테스트로 검증한다.

이 문서는 dirty worktree의 local 구현 상태다. 기존 commit의 CI / live / package evidence를 이번 변경의 증거로 재사용하지 않는다. 전체 deterministic matrix, 필요한 provider live gate, candidate-matched package/evidence와 rollback 계약 정리가 끝나기 전에 version bump / tag / push / 공식 release를 진행하지 않는다.

최종 local checks: architecture / diag / Observation / CLI / streaming / future-schema 관련 208 PASS; 어댑터와 M2/M3 별도 270 PASS / Windows symlink 1 SKIP. CLI helper 분리 후 관련 89 PASS 재검증. 수정한 runtime 파일의 Pyright 0 errors, parser inventory check 및 Wave 0–9 traceability PASS. 현재 버전의 local wheel build와 새 extension 파일 포함 / 삭제한 prototype 제외 확인도 PASS다. 첫 incremental build에서 발견한 삭제 파일의 stale `build/lib` 복사본은 제거 후 다시 빌드했다.
