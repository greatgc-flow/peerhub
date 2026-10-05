# Milestone Entry / Exit Gates

## M1 -> M2

M2 production activation 전:
- M1 Core contract GREEN
- Bridge/Observation/Diag GREEN
- crash/concurrency/idempotency GREEN
- live provider + package gate GREEN
- exact candidate-bound fresh PASS evidence

## M2 -> M3

M3 production activation 전:
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
- distributed runtime optionality 확인
- M1/M2 fallback 정상

## Gate evidence rule

`PASS`라는 이름만으로는 충분하지 않습니다.

- exact candidate identity가 일치해야 함
- freshness policy를 만족해야 함
- QUEUED/RUNNING/PENDING/CANCELLED/STALE/UNAVAILABLE/UNKNOWN/SKIPPED는 blocking gate evidence가 아님
- timeout/stale은 HOLD
- 시간 임계치는 코드에 하드코딩하지 않고 CI/release policy에서 외부화
