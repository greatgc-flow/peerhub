# Incident Runbook

1. 사용자 영향과 authoritative state를 먼저 확인
2. 로그/Record/Observation/버전/hash를 보존
3. correctness invariant 위반이면 release/rollout 중단
4. containment 실행
5. 복구 경로 선택: retry / failover extension / rollback / forward-fix / manual reconcile
6. `MAY_HAVE_STARTED`는 자동 재실행 금지
7. 서비스 복구 후 Problem 여부 판정
8. root cause와 contributing factors 분리
9. regression/eval 추가
10. fix release 후 recurrence watch
