# Bridge initialization candidate — LOCAL VERIFIED, NOT APPLIED

Baseline: `df2e52f26dfa30eba3c8bf108278537cf0e5041e`.
This folder preserves an executable proposal and its measured evidence. It
**does not change the repository's runtime**. Previous PR CI is not evidence
that this patch passed full CI. Overall architecture score remains 90.7/100.

## Change

Compare complete names and SQL of all 36 Bridge-owned protection triggers
inside the existing ClaimStore write transaction. Matching definitions avoid
DROP/CREATE. Missing, modified or extra definitions still trigger atomic
reconciliation. Quote embedded quotation marks in legacy trigger names.

Only the CREATE-prefix IF NOT EXISTS is removed for SQLite's stored-SQL
comparison; do not normalize body whitespace or quoted literals. Legacy
single-slot session registries retain the original drop/reinstall migration
path. Registry initialization, projection repair and migration hooks still
execute on every open. Public methods, runtime dispatch, certainty, claims,
replay, session selectors and provider policy are unchanged.

## Local evidence

The runtime was reconstructed from the published 0.24.7 sdist plus the branch
runtime changes. CoreStore, Bridge and ClaimStore source SHA-256 values were
matched to the previously recorded branch hashes; runtime_contract and the
source_records compatibility iterator were restored from branch contents.
This is not a complete repository checkout.

Linux / Python 3.13.5 / SQLite 3.46.1 / pytest 9.0.2. No provider calls.

- Baseline on the new tests: **86 passed, 5 failed** (warm/concurrent/after-
  migration schema churn and quoted legacy trigger repair).
- Candidate: **91 passed, 0 failed**.
- The packaged patch was separately applied to the reconstructed baseline;
  its source and test bytes matched the tested candidate exactly, and the
  packaged test module again had **91 passed, 0 failed**.
- git apply --check and compileall: PASS. New-test local marker warnings do
  not represent a warning-free full repository run.
- AST comparison: only Bridge.__init__ changed; no Bridge method was added
  or removed. Original post-registry trigger SQL was moved unchanged.
- Full repository CI, Windows, Pyright, command inventory and live G3: NOT_RUN
  for this candidate. No claim that all future-schema races or all concurrent
  migration scenarios have been covered by these focused tests.

The 91 cases include missing/changed variants for all 36 protection triggers,
quoted legacy names, literal whitespace, unrelated-trigger preservation,
failed-install/registry rollback, immutability with recursive_triggers OFF,
parallel warm opens, future-schema refusal, both legacy column layouts,
default reuse and failed legacy migration rollback.

## Measurements

Five rounds at 1/2/4/8 workers; 75 warm opens per version. One before/after run,
not randomized. Cache/load uncontrolled; phase time includes lock waiting.
All stores are disposable. These results are not an SLA or a production claim.

| Workers | Total p50 before / after (ms) | Total p95 before / after (ms) |
|---:|---:|---:|
| 1 | 8.735 / 4.821 | 11.431 / 5.223 |
| 2 | 15.945 / 9.311 | 20.658 / 13.027 |
| 4 | 26.136 / 17.652 | 54.538 / 31.426 |
| 8 | 54.645 / 36.463 | 114.030 / 63.641 |

Deterministic effect: complete warm Core+Claim+Bridge initialization changes
SQLite's internal schema counter **72 -> 0 times per open**, not the product
schema version. Report: results.json. Reproduce with benchmark.py in isolated
before/after checkouts; never target a user database.

## Apply and validate in a complete checkout

Stay on the review branch, never main. Require a clean worktree and inspect
any changes newer than the recorded baseline before applying.

```sh
git status --short
git apply --check docs/implementation/candidates/bridge-init-20261111/candidate.patch
git apply docs/implementation/candidates/bridge-init-20261111/candidate.patch
python -m pytest -q tests/communication/bridge/test_bridge_init_reconciliation.py
python -m pytest -q
pyright
```

The patch adds the regression module and its platform-sensitive inventory
entry alongside the Bridge edit. Run the parser-derived command inventory
check and full supported hosted matrix according to CONTRIBUTING. Bind every
result to the applied commit. Do not apply merely because this README exists.

SHA-256 of peerhub/extensions/bridge.py:
- Before: 9435576de11d19c8adb6167a09f2db5926f63a60b99377a2618d4cdf9ef3d2ff
- After: 78a2d5a6ae9aa37efe2413ba2170c63195aa93fd9378b4e485b0f9c434306f05
- candidate.patch: 667d57a24b351030b7b4e60f4797acd63dfd9788aaa3b3abc9a3e5448981f482

Do not close #323/#335, relax AGY isolation, merge main, bump a version or
publish a release based only on these local results.
