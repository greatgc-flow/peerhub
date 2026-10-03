# Post-release Review

## 시점

고정 일수 대신 release 성격에 맞는 stabilization window를 설정합니다.
데이터 migration, Bridge semantics, provider adapter 변경처럼 위험도가 높을수록 충분한 관찰을 요구합니다.

## Review 질문

### Correctness
- zero-tolerance invariant 위반은 없었는가?
- unknown/uncertain 상태를 성공으로 오인한 사례가 없었는가?

### Reliability
- restart/recovery/catch-up이 예상대로 동작했는가?
- manual recovery가 반복되었는가?

### Provider / Observation
- provider/model/CLI drift가 있었는가?
- quota/rate/availability를 정확히 표현했는가?

### Operations
- runbook만으로 대응 가능한가?
- evidence 수집이 충분했는가?
- alert가 actionable했는가?

### Product simplicity
- 새 기능 때문에 Core가 불필요하게 커졌는가?
- 설정/Skill/Catalog/Extension으로 옮길 수 있는 로직이 있는가?

### Learning
- 새 regression test가 필요한가?
- exception/interaction inventory를 확장해야 하는가?
- 다음 release의 입력으로 승격할 항목은 무엇인가?

## 종료

각 발견은 terminal disposition 또는 다음 lifecycle item으로 연결되어야 합니다.
“검토함” 자체는 종료 상태가 아닙니다.
