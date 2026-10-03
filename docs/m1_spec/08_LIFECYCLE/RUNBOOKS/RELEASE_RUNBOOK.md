# Release Runbook

1. requirement/test/catalog revision freeze 확인
2. G0~G4 실행
3. artifact build + hash
4. fresh environment clean-install
5. real-provider advertised surface canary
6. known risks/deferred trigger review
7. release evidence index 생성
8. publish gate dependency 확인
9. publish
10. stabilization observation 시작
11. invariant breach 시 즉시 `ROLLBACK_RUNBOOK.md`
12. observation 종료 후 Post-release Review
