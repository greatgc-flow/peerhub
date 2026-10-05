# Release / Promotion / Rollback Guide

## Promotion ladder

```text
Developer GREEN
→ Release Candidate
→ Clean-install Smoke
→ Real-provider Canary
→ Preview / Limited Cohort (optional)
→ Stable
→ Stabilization Watch
```

단계를 건너뛰려면 이유와 승인 evidence를 남깁니다.

## Publish 전 필수 gate

Gate의 기계 판독 dependency는 `release-gates.json`, 210개 테스트의 primary gate 매핑은 `06_GUIDES/TEST_SET/TEST_RELEASE_GATE_MAP.json`이 SSOT입니다.

```text
G0 → G1 → G2 → G3 ┐
              └→ G4 ├→ G7 Invariant → G5 Publish
G2 → G6 Soak (non-blocking by default)
```

`06_GUIDES/TEST_SET/RELEASE_GATE_MATRIX.md`의 G0~G5를 사용합니다.
추가로 lifecycle 관점에서는 다음이 필요합니다.

- current requirement/test SSOT와 artifact revision 일치
- release notes에 known risk/deferred trigger 포함
- rollback 가능한 이전 artifact/hash 확보
- migration이 있다면 dry-run + backup/restore proof
- live-provider가 광고되는 경우 실제 canary 성공
- release evidence index 생성

## Rollback / Hold trigger

즉시 rollback 또는 publish hold:

- Core correctness invariant 위반
- destructive migration의 검증 불일치
- 새 버전에서 반복 crash-loop 또는 unrecoverable corruption
- stale owner가 terminal evidence를 덮어씀
- read-only 경로가 mutation 수행
- advertised provider가 release gate에서 실제로 동작하지 않음

일반 성능 저하는 측정된 SLO/threshold에 따라 `hold / mitigate / continue`를 결정합니다.

## Rollback 원칙

1. 데이터 schema downgrade를 자동 가정하지 않습니다.
2. binary rollback과 data rollback을 분리합니다.
3. rollback 전 authoritative state snapshot/evidence를 보존합니다.
4. migration 적용 후 구버전이 읽을 수 없는 경우 forward-fix가 더 안전할 수 있습니다.
5. rollback 후에도 incident는 닫지 않습니다. root cause + regression + recurrence watch까지 진행합니다.

## Release channel은 옵션

프로젝트 규모에 따라 `preview`, `candidate`, `stable` 등을 선택할 수 있습니다.
채널 이름은 Core 계약이 아니며 설정/CI pipeline 수준에서 관리합니다.

## Evidence validity before promotion

`G5 publish`는 dependency gate가 단순히 존재하는지가 아니라, 동일 candidate identity에 대해 최신 `PASS`인지 확인해야 합니다. 취소/대기/오래된 canary 결과는 publish 승인으로 승계하지 않습니다.
