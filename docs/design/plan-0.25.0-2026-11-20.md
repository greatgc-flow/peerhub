# 0.25.0 plan (debate 2026-11-20, unanimous cc/cx/ag)

One release 0.25.0 = finished items (#414 child bars + Guide link, model refresh + ag.neon) + D1-D15. Milestones: M1 D5 guide + D6 text + D12 + D11 profile fields; M2 D1+D2+D3+D13+D14+D15; M3 D4+D7+D8; M4 D10. D9 and D16 are cut. Gate: full tests, pyright, CI full matrix, live gate, every CLI output inspected by eye (owner rule), live pings once at the end.

## Decisions (D)

New fact that changes P1/P2: the PeerHub store (SQLite) ALREADY holds per-session usage (usage_json, context_watermark), daily latency sums and daily usage-coverage counts (measured/absent/unsupported/unknown/missing) per stream+peer; the monitor's "100 of 3,285" is only its display sample. So we do NOT add a second store (no asks.jsonl). The gap is a per-profile read model, outcome marking and a cost proxy.

D1 (P1 revised) Telemetry on the existing store: an implementer first lists what per-ask fields the store already has (model/effort, fresh|resumed, wall time, tokens in/out/cache r/w, quota readings) and adds ONLY missing ones in additive columns/tables; unreported = NULL, never 0; no token estimates (chars/4 rejected). Outcome: default `unknown`; `peerhub outcome <ask-id> accepted|rejected|retried` appends a correction. Pool-% delta stored only when exactly one ask was in flight on that pool, else NULL. A ping test per peer proves tokens or an explicit `unreported`.
D2 (P2) `peerhub profile stats [--json]`: on-demand aggregation (no cache file) per profile: n, coverage %, p50 wall, cache-read %, tokens per ask, pool-% per accepted answer (only where D1 allows), last benchmark run, `stale` when the profile's model/effort changed after the last measurement. `peerhub profile list` gets one extra column `stats` (n and stale flag). No new monitor panel.
D3 (P3) Stream budget: before a RESUMED ask, if the previous ask of that stream reported input >= 128k tokens, or 12 resumed turns when tokens are unreported, print ONE stderr line with the fresh-stream command and a handoff hint (decisions, open work, file refs). Warn only, never block; thresholds in config. No `handoff` command (an LLM summary costs tokens; the hint is enough).
D4 (P4) Prompt layout: every PeerHub-built prompt (recipes, vote templates) puts the static preamble first and variable facts last; test = first N bytes identical across asks. No provider cache-marker injection (not supported by the CLIs). The claim "ag/cx cache is 0%" is checked through D1 data before any saving is promised in the changelog.
D5 (P5) `peerhub guide` <= 1.6 KB of decisions (peer choice, tiers, stream reuse rule, fresh votes, batch cap, timeout, uncertain handling, pointer line to `peerhub profile` and `peerhub explain <topic>`); rare hazards/store/upgrade/API move to `explain` topics; max-parallel 3 everywhere (one constant); tests: size cap, every pointer topic resolves.
D6 (P6+N1) Vote policy as guide/explain text plus verdict template (`VERDICT / BLOCKERS / NITS`, <= 300 tokens): routine check = ONE reviewer, fresh; consequential (release, schema, deletion, public API) = 3 independent peers, unanimity. NO new `vote` command (batch run already exists). Standalone asks and votes start fresh unless a stream is named.
D7 (P7) `peerhub suggest --tier standard|effort|deepthink [--task review|code|read]` prints the exact `peerhub ask ...` command and one reason line: gates 5H < 90% and snapshot age <= 15 min; among eligible peers prefer lower 7D PACE per owner target cc <= ag == cx; cc.deepthink, cx.deepthink and ag.effort are never suggested (explicit use still works). No `--peer auto`.
D8 (P8) `peerhub monitor` (watch) never probes provider quota by default; `--probe` opts in; dispatch/suggest refreshes only when the snapshot is older than 15 min. If the current code already behaves like this, only document it.
D9 (P9) CUT. Enforced read-only/worktree for ag is deferred (0.25+), not built.
D10 (P10 reduced) A canary suite, not a CLI feature: 6 fixed tasks with hidden checks (2 easy, 2 medium, 2 hard, tuned so the best profile does not sit at 100) in `docs/benchmarks/suite-v1/`, a runner in `tools/` that appends versioned JSONL, and its results feed D2 `last benchmark run`. No live run is part of the release gate; the first baseline is run after release with owner approval.
D11 (P11) Profiles: `cx.deepthink` becomes an alias of `cx.effort` with effort xhigh (new profile fields `alias_of`, `effort_override`; stderr deprecation note when used; still works); `cc.deepthink` stays (not suggested); `ag.effort` stays selectable but not suggested; `ag.neon` = gemini-4-neon-high added (done); oa/ga/ca stay unchanged and tagged `unmeasured` in `profile stats` (model ids cannot be verified without keys).
D12 (P12) Process rule in AGENTS.md/CHANGELOG: no further monitor cosmetics and no top-tier benchmark reruns until D1-D3 data exist.
D13 (N3 cc) `peerhub profile check`: lists profiles whose model id is missing from the installed CLI's model list (where the CLI can list) and profiles with no measurement for 30 days. Read-only.
D14 (N4 cc) After each ask on a TTY, one stderr receipt line: `profile - wall - tokens in/out/cache% - pool delta` using NULL as `-`; `PEERHUB_NO_RECEIPT=1` silences it.
D15 (N1 cc) Context diet: when the inlined query file exceeds 64 KB, warn once on stderr suggesting paths or a diff; never truncate.
D16 Rejected as unneeded or unsafe: ag cache-marker injection, local regex/syntax lint pre-flight, token estimates, `peerhub vote`, `peerhub handoff`, `--peer auto`, parallel ag reads.


## Amendments (A, binding; they override the D text)

A1 (D1/cx) Pool-% delta is stored (percentage points) only when PeerHub observed exactly one in-flight ask on that pool for the whole interval between comparable readings; NULL across quota resets.
A2 (D2/cx) `n` = total asks; coverage reported per metric; pool cost = sum of valid deltas / number of asks in that same cohort whose latest outcome is `accepted`; NULL when the denominator is 0.
A3 (D3/cc) The threshold counts total prompt tokens = fresh input + cache read + cache write >= 128k; 12 resumed turns when unreported.
A4 (D4/cx) Per template variant a test proves the complete static preamble is byte-identical across two renders with different variable facts.
A5 (D6/cx) Routine checks and all three consequential votes always run FRESH, even when a stream name is given (a named stream is for review rounds on one diff only).
A6 (D7/cc+cx) A missing or expired snapshot is refreshed once. A peer whose reading stays unusable remains eligible but ranked last with reason `quota unknown`. If NO peer has a usable 5H and 7D reading, print one reason line to stderr and exit nonzero (never an empty suggestion). Equal 7D PACE ties break in order ag, cx, cc (owner target cc <= ag == cx; amendment by cc in round 3).
A7 (D8/cx) A missing snapshot counts as expired for dispatch and suggest.
A8 (D10/cx) Hidden checks live outside the peer's task workspace and run after the peer returns; every result records the suite version and effective profile settings.
A9 (D11/cx) `oa`, `ga` and `ca` profiles are tagged `unmeasured` until each has a recorded measurement.
A10 (D14/cc) The receipt line starts with the ask id: `<ask-id> - <profile> - <wall> - tokens in/out/cache% - pool delta`.
A11 (release plan) The OWNER ordered ONE release containing everything (finished items + D1-D15). To honour cc's schema argument, that single release is version 0.25.0 (minor bump, additive schema only, no destructive migration). No second release. Gate: full tests, pyright, CI full matrix, live gate, plus the owner's rule (every CLI output incl. guide, explain, help, monitor, profile, suggest, outcome, profile check, receipt and warnings inspected by eye). Live peer pings (quota) run once, after the code is final, for the three peers' token-reporting test and the model refresh; the owner already asked for the model refresh.

