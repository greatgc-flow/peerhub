# Incident Runbook

1. Check user impact and authoritative state first
2. Preserve logs/Record/Observation/version/hash
3. Stop release/rollout for a correctness invariant breach
4. Apply containment
5. Choose a recovery path: retry / failover extension / rollback / forward-fix / manual reconcile
6. Do not automatically rerun `MAY_HAVE_STARTED`
7. Determine whether there is a Problem after restoring service
8. Separate root cause from contributing factors
9. Add regression/eval
10. Perform recurrence watch after the fix release
