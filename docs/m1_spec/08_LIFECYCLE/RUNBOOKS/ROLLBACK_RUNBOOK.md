# Rollback / Hold Runbook

1. Stop further rollout of the new change
2. Preserve an evidence snapshot
3. Check invariant/data-integrity impact
4. Check binary rollback feasibility
5. Check schema/data backward compatibility
6. If safe, roll back to the previous verified artifact
7. If unsafe, apply containment + forward-fix
8. Recheck clean-install/live smoke
9. Link Incident/Problem records
10. Close after completing regression tests and recurrence watch
