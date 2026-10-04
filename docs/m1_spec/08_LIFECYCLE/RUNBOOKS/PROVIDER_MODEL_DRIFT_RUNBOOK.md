# Provider / Model / CLI Drift Runbook

1. Observation 또는 live canary로 drift 사실 확보
2. Core failure와 분리
3. `refresh-model-catalog` Skill로 current facts 재수집
4. JSON Schema 검증
5. 변경 후보 diff 제시
6. adapter compatibility test
7. 승인 후 Catalog/문서/테스트 함께 갱신
8. changed profile만 최소 live canary
9. release
10. 일정 기간 observation 후 종료
