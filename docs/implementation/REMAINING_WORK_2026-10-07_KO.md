# 전체 감사 및 잔여작업 — 2026-10-07

기준 커밋 `bcad7bf` (`feat/extension-cutover-2026-10-06`, PR #37 — 오너 확인 전 머지 금지).

## 감사 결과
- 로컬 전체 스위트: 1477 passed / 15 skipped / 14 deselected, 실패 0. pyright 0 errors, `command_inventory --check`, `traceability --upto 9` 통과.
- 건너뜀 15건: CI 전용 매트릭스 케이스와 Windows symlink 권한. 제외 14건은 별도 확인 필요.
- 이번 세션 완료: M1 0.11.0 릴리스, v0 보존(`legacy/v0-main-final`, `v0-legacy-final`), `peerhub monitor` + `--view`, agy `/usage` 토큰 가드, Codex 리셋 쿠폰 증거, ag 검토 triage(12건 수정), CLI 옵션 이름 정리(옛 이름 제거, 호환성 파괴).
- 현재 이름: `--peer`/`--stream`, `--author-peer`, `--idempotency-key`, `--cycles`, `--observation-db`, `--system-dir`; `observation refresh`는 단발, 반복은 `monitor`.

## 잔여작업
### A. 바로 처리
1. PR #37 CI(Windows 3.12~3.14, live-provider-validation) 확인.
2. 메모리 체크포인트 갱신.
3. `cc quota ERROR`(claude `/usage` 프로브 간헐 실패) — 2026-10-07 재현 안 됨(cc 프로브 OK). 재발 시 증거 수집 후 재조사.
4. README 예시 정리 — 완료.
5. 알림 정리: pool 없는 ERROR 행 라벨.
6. `diag --view {plain,rich}` — 완료.

### B. 오너 결정
1. PR #37 머지(v0 폐기)와 다음 버전 번호(옵션 개명은 호환성 파괴).
2. `reset_credit` 증거 종류 정식 도입.
3. 릴리스 정책 TTL 604800, 위임 기본값.
4. 릴리스 노트의 호환성 파괴 표기.

### C. 계약·검증 백로그 (로컬 테스트 수로 덮지 않음)
1. 후보 커밋 묶음 cx/ag 실제 증거, 패키지·릴리스 증거, Claude 실제 성공 호출(C-7D 잔량 0).
2. 연속성: authoritative writer CAS·projection rebuild, Host boot discovery·의존성 버전, Artifact GC/fsync/streaming, Skill rescan/digest/파서 일치, Eval 소스 digest.
3. 협업: 강제 취소 가능한 런타임, 누적 비용·병렬·조건부 실행, 옛 Memory 이벤트 migration 정책, 정확한 메타데이터 검색, Runtime Target 카탈로그.
4. 복구·원격: 모든 raw writer 포함 오프라인/독점 복구, 외부 A2A 상호운용·재시작 조정.
5. M2/M3 VERIFIED 승격은 게이트 통과 전 금지.
