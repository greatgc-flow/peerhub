# Warm initialization: measured before/after (#335)

Raw data: [claim-init-2026-11-11.json](claim-init-2026-11-11.json). Design and safety
rules: [EXECUTION_KERNEL_BOUNDARIES.md](../implementation/EXECUTION_KERNEL_BOUNDARIES.md).

## Change

Both `ClaimStore` and `Bridge` now compare trigger names and complete SQL inside the
existing write transaction and skip DROP/CREATE when they match. Any mismatch or legacy
registry still rebuilds the owned set atomically. Warm-open schema changes: **80 → 0**.

## Method

`python -m tools.bridge_init_bench --rounds 10` (disposable databases only, 1/2/4/8
workers, no provider calls, no `--db` option by design). Before = 0.24.7
`bridge.py`/`bridge_claims.py`; after = 0.24.8 candidate.

| Workers | p50 total init before → after (ms) |
|---:|---:|
| 1 | 33.4 → 7.4 |
| 2 | 56.8 → 19.5 |
| 4 | 71.1 → 30.5 |
| 8 | 122.8 → 84.8 |

One Windows PC, one run, uncontrolled host load. Times include lock waits. Not an SLA
or a throughput claim; the deterministic effect is the avoided schema churn.

Locking is not workspace isolation: this benchmark says nothing about #323.
