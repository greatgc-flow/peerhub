# TDD / 배포 가이드

## Test tiers
architecture → unit/property → schema → SQLite integration → concurrency/fault → E2E → real provider live → package/install.

## Release gate
문서가 “live gate”라고 써 있는 것만으로 부족합니다.
CI DAG에서 실제 publish job이 해당 gate 성공에 의존해야 합니다.

## Clean install
wheel/sdist 또는 최종 배포 artifact를 fresh environment에 설치한 뒤 smoke합니다.

## Evidence
AI self-report가 아니라 terminal/CI/live output을 증적으로 남깁니다.
