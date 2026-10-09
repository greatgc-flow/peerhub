# Milestone Entry / Exit Gates

## M1 -> M2

Before M2 production activation:
- M1 Core contract GREEN
- Bridge/Observation/Diag GREEN
- crash/concurrency/idempotency GREEN
- live provider + package gate GREEN
- exact candidate-bound fresh PASS evidence

## M2 -> M3

Before M3 production activation:
- Artifact provenance/digest GREEN
- Work projection replay/rebuild GREEN
- Skill/Catalog drift gate GREEN
- MCP security/boundary GREEN
- Backup restore/replay/reconcile GREEN
- Eval dataset/regression loop GREEN

## M3 -> Optional Track mode

- Search/Memory provenance GREEN
- A2A isolation GREEN
- Routing/Ochestration failure isolation GREEN
- Approval evidence GREEN
- Confirm distributed runtime optionality
- Normal M1/M2 fallback

## Gate evidence rule

The name `PASS` alone is not enough.

- exact candidate identity must match
- must satisfy the freshness policy
- QUEUED/RUNNING/PENDING/CANCELLED/STALE/UNAVAILABLE/UNKNOWN/SKIPPED are not blocking gate evidence
- timeout/stale is HOLD
- Time thresholds are not hardcoded in code but externalized in CI/release policy
