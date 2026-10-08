# Recursive MECE Test Model

테스트 수를 늘리는 방식이 아니라 **테스트 공간을 먼저 분할하고 빈 셀을 자동 검출**합니다.

## 1. 1차 기능 분할

- Architecture / Boundary
- Core Domain
- Persistence / Concurrency
- Session Bridge / Control
- Observation / Diag
- Continuity / Context
- Migration / Cutover
- Release / Packaging
- Meta-Quality (test-of-tests)

## 2. 각 기능 안에서 재귀 분할

각 영역마다 적용 가능한 경우를 다시 다음 축으로 나눕니다.

`positive / negative / boundary / fault / concurrency / restart / recovery / time / compatibility / security / capacity / live`

모든 요구사항은 `required_dimensions`를 선언하며, 연결된 테스트들의 `dimensions` 합집합이 이를 100% 덮어야 합니다.

## 3. 독립 완전성 표

- State machines: `STATE_MACHINE_COVERAGE.json`
- Exception space: `EXCEPTION_CATALOG.json`
- Cross-feature interactions: `INTERACTION_MATRIX.json`
- Requirement↔Test: `python -m tools.traceability` 출력

## 4. 종료 조건

1. uncovered requirement = 0
2. uncovered required dimension = 0
3. OPEN state transition = 0
4. OPEN exception = 0
5. uncovered REQUIRED interaction = 0
6. orphan/dangling test = 0
7. package validator PASS

현재 baseline: **85 requirements / 205 tests**.
