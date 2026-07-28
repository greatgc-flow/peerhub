# DP/DT Controlled-Fake Strategy R1

Independent AG/CX review, 2026-07-28.  No provider call is permitted.

| Fixture | Deterministic fake scenario | Evidence status |
|---|---|---|
| DP-06 | persist intent, crash before result, restart; retain `MAY_HAVE_STARTED/UNKNOWN`, reject auto-replay | V1-only |
| DT-01 | PTY emits ordered timestamped chunks, clean exit, terminal receipt | legacy partial; V1 receipt required |
| DT-02 | split UTF-8 and CR/LF across chunks; assert canonical events/order | V1-only |
| DT-03 | silence deadline and hard deadline are exercised separately | V1-only |
| DT-04 | fake ignores first cancel then obeys bounded termination; preserve timeout uncertainty | legacy partial; V1 policy required |
| DT-05 | fake process tree cancellation yields termination or explicit identity uncertainty | V1-only |
| DT-06 | primary partial-output failure plus cleanup failure; primary result remains authoritative | V1-only |

Common rules: isolated local state only; one fake event script per fixture;
canonical transcript and record digest; no automatic replay after uncertain
dispatch; cleanup evidence never overwrites the primary outcome.  Existing
legacy regressions are supporting evidence, not substitutes for V1 records.
