# 개발 구현 가이드

## 순서
1. Core contracts + RED tests.
2. fresh SQLite minimal store.
3. fake Session Bridge.
4. concurrency/crash/idempotency.
5. real CC/CX/AG adapters.
6. session continuity/control.
7. Observation.
8. status/peers/logs.
9. Readonly Diag.
10. real live release gate.

## 재사용
SQLite UoW/CAS, binary resolution, Windows process cancellation, session fingerprint,
quota parsing, real provider canary 경험.

## 재작성
Peer/Stream/Record/Offset와 새 extension ports.

## 금지
기존 governance/routing/task service를 이름만 바꿔 M1으로 옮기지 않습니다.

## TDD execution

구현 순서는 `06_GUIDES/TEST_SET/RED_SEQUENCE.md`의 Wave 0→6을 따릅니다. 특히 SQLite concurrency/crash GREEN 이전에 실제 provider adapter 구현을 앞당기지 않습니다.
