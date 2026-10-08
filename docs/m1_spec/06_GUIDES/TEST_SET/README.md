# PeerHub M1 Test Set — Recursive MECE Baseline

이 디렉터리는 구현 전에 RED를 작성할 수 있는 **machine-readable test contract**입니다.

## 핵심 SSOT
- `requirements.json` — **85 requirements** + required dimensions
- `test-catalog.json` — **205 concrete tests**
- `RECURSIVE_MECE_MODEL.json/.md` — 테스트 공간 분할/종료 조건
- `STATE_MACHINE_COVERAGE.json` — 상태 전이/금지 전이 coverage
- `EXCEPTION_CATALOG.json` — **85** 예외/경계/장애 terminal disposition
- `INTERACTION_MATRIX.json` — **34** high-risk cross-feature interactions

## 사람이 읽는 View
- `TEST_TRACEABILITY_MATRIX.*`
- `TEST_CASE_CATALOG.*`
- `BOUNDARY_EXCEPTION_MATRIX.md`
- `FAULT_INJECTION_MATRIX.md`
- `RED_SEQUENCE.md`
- `RELEASE_GATE_MATRIX.md`

## 핵심 규칙
1. requirement가 테스트 하나만 갖는 것으로 완료되지 않습니다.
2. requirement의 모든 `required_dimensions`를 linked tests의 dimension union이 덮어야 합니다.
3. 상태 전이/예외/고위험 상호작용은 별도 inventory에서 다시 교차검증합니다.
4. DEFERRED/OUT_OF_SCOPE는 rationale/trigger 없이 허용하지 않습니다.
5. P0 required dimension은 live를 제외하고 P0 deterministic evidence가 있어야 합니다.
6. META tests + package validator가 테스트셋 자체 drift를 차단합니다.

현재 baseline: **85 requirements / 205 tests / OPEN exception 0 / uncovered required interaction 0**.

## Release gate traceability

`TEST_RELEASE_GATE_MAP.json`이 205개 테스트를 primary release gate에 1:1 배정합니다. Gate dependency는 `../../08_LIFECYCLE/release-gates.json`이 정의합니다.
