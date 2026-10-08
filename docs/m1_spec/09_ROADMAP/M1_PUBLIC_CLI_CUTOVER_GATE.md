# M1 Public CLI Cutover Gate

M1 **implementation** and default **public CLI cutover** are intentionally separate. 2026-10-04 current main contains both `peerhub` (legacy public hierarchy) and `peerhub-m1` (M1 side-by-side).

## Required before cutover

1. **Canonical command inventory** — derive the M1 public command tree from the actual parser/package metadata; no prose-only inventory.
2. **Diag quota parity** — M1 public Diag MUST expose current Observation evidence for quota/rate-limit/resource pools without mutating or refreshing it. `UNKNOWN/STALE/UNAVAILABLE` remain explicit.
3. **No inert options** — every published option has tested effect or is removed; current `peerhub-m1 diag health --obs-db` must not remain inert.
4. **Compatibility disposition** — (2026-10-08) closed: the single `peerhub` entrypoint is the only CLI; the v0 command table, importer and selector were removed.
5. **Exit/help/JSON contract** — align with the unified package standard where applicable; do not renumber stable legacy codes merely for symmetry.
6. **Candidate-matched verification** — exact cutover candidate must pass deterministic matrix, live/required provider gates, package/evidence gates and freshness policy.
7. **Rollback** — release rollback is a version pin/HOLD; authoritative M1 data is never rewritten.

## Non-goal

Cutover does **not** reintroduce the old governance/task/leadership architecture into M1 Core. Legacy names are migration inputs, not design authority.
