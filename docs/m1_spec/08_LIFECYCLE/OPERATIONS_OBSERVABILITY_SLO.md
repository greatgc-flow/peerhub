# Operations / Observability / SLO Guide

## 관찰 계층

### A. Correctness invariants — zero tolerance

`closed-loop.json`의 `invariants[]` / `INVARIANT_CATALOG.md`를 그대로 관찰합니다. 운영 문서에 별도 복제 목록을 두지 않아 drift를 방지합니다.

### B. Reliability signals
- append commit success/failure category
- CAS conflict vs storage error
- Bridge delivery result: delivered / failed-before-start / may-have-started / terminal
- restart recovery success
- migration/restore success
- provider discovery/canary result

### C. Capacity/performance signals
- append/read latency percentiles
- pending/unread Record depth
- bridge catch-up depth/time
- DB busy/lock pressure
- observation staleness
- provider quota/rate headroom

### D. Product/operation signals
- repeated manual recovery
- false/ambiguous Diag output
- operator workaround frequency
- provider/model drift
- documentation/process mismatch

## SLO 정하는 순서

1. 먼저 baseline을 수집합니다.
2. 사용자 영향과 업무 중요도를 분리합니다.
3. 측정 가능한 SLI를 선택합니다.
4. target/window를 환경 설정으로 제안합니다.
5. canary/운영 데이터로 검증합니다.
6. 유효하지 않으면 threshold를 재조정합니다.

## Alert fatigue 방지

- invariant breach: 즉시 actionable alert
- transient provider/quota: Observation으로 기록하되 Core failure로 승격하지 않음
- 동일 원인의 반복 이벤트: 하나의 Problem record로 집계
- stale/unknown은 실패로 꾸미지 않고 `UNKNOWN/STALE` 그대로 표시

## 개인정보/비밀정보

운영 evidence는 기본적으로 prompt/transcript/credential을 포함하지 않는 방향으로 설계합니다.
필요한 경우 최소 범위만 별도 보관하고 redaction policy가 확정된 뒤 자동화합니다.
