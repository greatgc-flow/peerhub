# Data Corruption / Migration Failure Runbook

1. write를 중지하거나 안전 경계로 제한
2. 원본 DB/artifact를 복제하여 evidence 보존
3. silent repair 금지
4. corruption vs schema mismatch vs migration partial-apply 구분
5. backup/restore proof 확인
6. dry-run 가능한 복구를 먼저 실행
7. recovery copy에서 검증
8. authoritative state에 적용 전 명시 승인
9. Core invariant + migration regression suite 실행
10. post-recovery consistency scan과 recurrence watch
