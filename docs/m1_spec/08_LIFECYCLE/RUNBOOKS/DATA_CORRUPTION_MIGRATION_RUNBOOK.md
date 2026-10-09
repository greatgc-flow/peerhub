# Data Corruption / Migration Failure Runbook

1. Stop writes or restrict them to a safe boundary
2. Copy the original DB/artifact to preserve evidence
3. Do not silently repair
4. Distinguish corruption vs schema mismatch vs migration partial-apply
5. Check backup/restore proof
6. Run recovery as a dry-run first where possible
7. Verify on the recovery copy
8. Obtain explicit approval before applying to authoritative state
9. Run the Core invariant + migration regression suite
10. Perform a post-recovery consistency scan and recurrence watch
