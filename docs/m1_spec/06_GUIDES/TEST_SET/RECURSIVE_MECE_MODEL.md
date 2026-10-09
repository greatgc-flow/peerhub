# Recursive MECE Test Model

Rather than just increasing the number of tests, **partition the test space first and auto-detect empty cells**.

## 1. Primary Functional Partitioning

- Architecture / Boundary
- Core Domain
- Persistence / Concurrency
- Session Bridge / Control
- Observation / Diag
- Continuity / Context
- Migration / Cutover
- Release / Packaging
- Meta-Quality (test-of-tests)

## 2. Recursive Partitioning within Each Function

For each area, partition applicable cases again by the following axes:

`positive / negative / boundary / fault / concurrency / restart / recovery / time / compatibility / security / capacity / live`

Every requirement declares `required_dimensions`, and the union of `dimensions` from its linked tests must cover it 100%.

## 3. Independent Completeness Matrices

- State machines: `STATE_MACHINE_COVERAGE.json`
- Exception space: `EXCEPTION_CATALOG.json`
- Cross-feature interactions: `INTERACTION_MATRIX.json`
- Requirement↔Test: output of `python -m tools.traceability`

## 4. Exit Criteria

1. uncovered requirement = 0
2. uncovered required dimension = 0
3. OPEN state transition = 0
4. OPEN exception = 0
5. uncovered REQUIRED interaction = 0
6. orphan/dangling test = 0
7. package validator PASS

Current baseline: **85 requirements / 207 tests**.
