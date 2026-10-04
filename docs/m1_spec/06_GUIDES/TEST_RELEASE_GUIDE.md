# TDD / 배포 / 운영 연결 가이드

## Test tiers

architecture → unit/property → schema → SQLite integration → concurrency/fault → E2E → real provider live → package/install.

## Release gate

문서가 “live gate”라고 써 있는 것만으로 부족합니다.
CI DAG에서 실제 publish job이 해당 gate 성공에 의존해야 합니다.

## Clean install

wheel/sdist 또는 최종 배포 artifact를 fresh environment에 설치한 뒤 smoke합니다.

## Evidence

AI self-report가 아니라 terminal/CI/live output을 증적으로 남깁니다.

## Full M1 test set

구체적인 RED-ready 테스트 설계는 `06_GUIDES/TEST_SET/README.md`를 SSOT entrypoint로 사용합니다. Requirement→Test coverage, concurrency/fault injection, fake runtime E2E, real-provider gate와 package gate까지 포함합니다.

## 테스트 이후

G5 publish가 끝나도 lifecycle은 종료되지 않습니다.

```text
G5 Publish
→ G7 invariant watch
→ stabilization observation
→ feedback/incident/drift triage
→ requirement/regression update
→ next RED
```

운영·rollback·incident·evidence·post-release review는 `08_LIFECYCLE/README.md`를 SSOT entrypoint로 사용합니다.
