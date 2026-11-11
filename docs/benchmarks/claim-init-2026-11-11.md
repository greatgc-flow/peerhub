# Claim initialization: measured, bounded change (#335)

Date: 2026-11-11. Baseline: `05c343624914125b93f3d9cfc5f3c4950da2f358`.
Related: [PR #349](https://github.com/greatgc-flow/peerhub/pull/349), [architecture ADR](../implementation/EXECUTION_KERNEL_BOUNDARIES.md), [raw measurements](claim-init-2026-11-11.json).

## Change and safety boundary

Only `ClaimStore` trigger installation is optimized in this slice. On every open,
inside the existing `BEGIN IMMEDIATE` transaction, compare BOTH names and complete
SQL bodies of the owned trigger set. Exact match means no trigger DDL. Missing,
changed or legacy definitions still replace the complete owned set atomically.
No cached schema-version shortcut; no data/claim/certainty/session policy change.
Unrelated tables' triggers remain untouched. Embedded quotes in legacy trigger
names are escaped during DROP, fixing a reproducible reopen failure.

The larger Bridge registry/migration path remains unchanged deliberately. This
is not a claim that all initialization overhead has been removed. Full warm
initialization still increments the schema cookie 72 times instead of 80;
ClaimStore alone changes from 8 increments to zero on an unchanged reopen.

## Reproduction and results

`python -m tools.bridge_init_bench --rounds 10` uses disposable databases only,
1/2/4/8 workers, ten rounds per worker count, and zero provider calls. Copy the
same tool into an isolated baseline checkout for a paired comparison. Never
point a benchmark at an operational store (there is intentionally no --db option).

Local runtime reconstruction used the published 0.24.7 sdist (Actions dist
artifact 13817903912) plus branch runtime changes; changed runtime blob hashes
were checked against GitHub. This was not a full checkout and not a live test.
The complete repository regression gate is GitHub CI, not these local results.
Linux / Python 3.13.5 / SQLite 3.46.1; 150 warm opens per version:

| Workers | Total p50 before/after, ms | Claim phase p50 before/after, ms | Total p95 before/after, ms |
|---:|---:|---:|---:|
| 1 | 19.095 / 16.640 | 5.504 / 1.778 | 28.069 / 18.416 |
| 2 | 31.641 / 22.804 | 7.981 / 3.297 | 50.392 / 39.689 |
| 4 | 43.546 / 44.272 | 21.268 / 10.119 | 85.784 / 81.370 |
| 8 | 93.269 / 91.169 | 36.687 / 29.278 | 163.423 / 160.857 |

One paired run, not randomized; host load and filesystem cache are uncontrolled.
Four-worker total p50 did NOT improve. Phase times include lock waiting; wait
time was not isolated. The deterministic improvement is avoided schema churn,
not a promised end-to-end speedup or a production throughput SLA.

## Regression evidence

- Baseline new claim tests: 3 failed / 11 passed (schema churn twice, quoted legacy name once).
- Candidate local focused tests: 22 passed / 0 failed, including benchmark argument validation.
- Missing/changed/legacy trigger repair, unrelated-trigger preservation, rollback
  on failed installation, parallel reopen, and future-schema refusal remain checked.
- UPDATE/DELETE/REPLACE/rowid REPLACE are rejected even with recursive triggers OFF.
- Tests are registered in the Windows platform-sensitive inventory.
- The candidate's complete CI results belong in PR #349 and issue #335, bound to the tested SHA.

## #323: locking is NOT workspace isolation

Two real `ask` calls using offline runtimes, distinct streams and the SAME database
and workspace rendezvous inside their delivery iterators and both finish. A second
owner on the SAME peer/stream is rejected. This proves PeerHub claims are scoped,
not a workspace-wide runtime lock. It does not prove actual provider locking or
sandbox behavior. Consequently the AGY writer overlap rejection remains unchanged;
manual parallel asks are not a substitute for enforced read-only isolation.
Actual AGY/cc/cx CLIs and paid/live G3 were not available or executed in this environment.

## Remaining

Measure/review the Bridge trigger+registry migration path separately; do not apply
the ClaimStore optimization there without its migration/fault oracles. Keep #323
open until provider behavior and enforceable isolation are proven. No main merge,
release, or automatic claim of 100/100 architecture quality accompanies this work.
