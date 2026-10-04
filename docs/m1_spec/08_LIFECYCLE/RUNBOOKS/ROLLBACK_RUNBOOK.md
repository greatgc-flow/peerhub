# Rollback / Hold Runbook

1. 새 변경의 추가 확산 중지
2. evidence snapshot 보존
3. invariant/data-integrity 영향 확인
4. binary rollback 가능성 확인
5. schema/data backward compatibility 확인
6. 안전하면 이전 verified artifact로 rollback
7. 안전하지 않으면 containment + forward-fix
8. clean-install/live smoke 재확인
9. Incident/Problem record 연결
10. regression test와 recurrence watch까지 완료 후 종료
