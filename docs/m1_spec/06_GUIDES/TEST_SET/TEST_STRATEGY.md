# PeerHub M1 Test Strategy — Recursive MECE / TDD Ready

## 목표
테스트는 구현 세부가 아니라 **불변식 + 예외 공간 + 상호작용 위험**을 잠급니다.

1. 작은 Core: Peer / Stream / Record / Offset.
2. durable truth: SQLite/canonical position/revision을 재시작 뒤에도 신뢰.
3. uncertainty honesty: runtime side effect를 exactly-once라고 가장하지 않음.
4. concurrency/crash/storage fault를 happy path와 동급 P0로 취급.
5. Diag/Observation의 Core 경계와 read-only 성질 증명.
6. deterministic CI와 real-provider empirical gate 분리.
7. **test-of-tests**로 재귀적 MECE를 자동 검증.

## 현재 baseline
- Requirements: **85**
- Tests: **207**
- Exceptions classified: **85**, OPEN 0
- High-risk interactions: **34**, uncovered REQUIRED 0
- State machines: **6**, OPEN transition 0
- Required-dimension uncovered: **0**

## Test tiers
- architecture: 5
- concurrency: 22
- e2e: 20
- fault: 21
- integration: 42
- live: 6
- meta: 4
- migration: 6
- package: 14
- property: 14
- schema: 16
- security: 1
- soak: 2
- unit: 37

## Case types
- boundary: 26
- fault: 37
- meta: 4
- negative: 56
- positive: 87

## 재귀 규칙
`기능영역 → case type → applicable risk dimension → lifecycle/failure phase → cross-feature interaction` 순으로 반복 분할합니다. 적용성은 requirement의 `required_dimensions`가 선언하고 validator가 실제 test union으로 검증합니다.

## 실행 정책
- PR/push: live/soak 제외 deterministic + META.
- Fast: architecture/schema/unit/property/meta.
- Core merge: SQLite/concurrency/multiprocess/storage fault/migration 포함.
- M1: Bridge/Control/Observation/Diag/fake-runtime E2E/security 포함.
- Release: package matrix + required real-provider canary.
- Scheduled: soak/capacity; 명시 SLO 전에는 evidence-only.

## Flaky 방지
- race/TTL에 `sleep()` 금지. Barrier/Latch/ManualClock 사용.
- writer별 독립 SQLite connection; multi-process는 실제 OS process.
- fault는 VFS/adapter/fake-runtime named injection point 사용.
- property failing seed/example 저장.
- live provider는 deterministic suite 대체가 아니라 별도 empirical evidence.

## 종료 조건
`Requirement → Dimension → Test → Evidence → Gate`와 `State / Exception / Interaction` 그래프가 모두 닫히고 package validator가 PASS여야 합니다.
