# Plan 0.24.0 — monitor + CLI unification (2026-11-08)

Consensus: cx.effort, cc.deepthink, ag.argon — unanimous ACK after 4 rounds (independent r1, cross-critique r2, vote r3, final ACK r4).
P0 measured: model_ping 9/9 PASS (ag.deepthink skipped: 3P-7D <1%); CLI versions ag 1.5.3, cc 2.1.319, cx 0.167.0; no model changes.

Resolutions of r3 objections: P1 cx wording; P2 verified overlay-only (~/.peerhub/config/models.toml) -> no code change; M2 cx (INSUFF, '-' only for unavailable); C3 cx (one 'Advanced: stream, record, offset' help line); C6 cc alternative + cx stderr rule; M3 cc addition.
P0 refresh-model-profiles first; release blocks on measured facts.
P1 no profile rename/repoint; guide: while 3P is low, explicitly use ag.argon only for tasks its measured capabilities support, else defer.
P2 cx.terra untouched (user overlay). P3 F-7D: no profile; monitor shows "unmapped".
M1 drop OVERVIEW, one Coverage footer. M2 STATE: INSUFF (dim), '-' only for unavailable, highlight THREAT/STALE, fix alignment; JSON unchanged.
M3 full short codes (STALE:window, PROBE:8d, DRIFT:ag,cc,cx) + `peerhub explain <code>` pointer; each code gets an explain entry in config_data/lessons.toml.
M4 pool->consumer profile column, configured-vs-measured labelled. M5 one staleness line per peer + next command; hide "context: unknown"; header shows resolved DB path+source.
C1 compact human text default regardless of TTY; universal --json; --format hidden alias; schemas unchanged.
C2 roots/verbs/batch stable, no new aliases. C3 one compact Advanced help line. C4 no new verbosity tiers.
C5 errors: one stderr line + outcome code + next: hint, nonzero exit.
C6 DB: --db > PEERHUB_DB > nearest upward .peerhub/core.db (nearest wins, no multi-candidate error); resolved path+source (explicit|env|discovered|default) on stderr/header, never polluting --json stdout; guide documents PEERHUB_DB.
R 0.24.0; Breaking: non-TTY default + --db/PEERHUB_DB precedence swap, with --json migration line. TDD for C1/C5/C6/M1-M5. Impl: cx (code) + cc (review) + ag (docs/guide); I run tests locally/CI.

## ag usability addendum (unanimous ACK, 2 rounds)
Evidence: ag failed 3/4 asks today (shell denied #300; read_file denied with trusted -w; resumed stream fabricated paths); inline --query-file + "no tools" worked.
A1 One ag recipe (<=6 lines) first in `guide` ag section and `explain ag`: inline content via --query-file and say "no tools"; -w = trusted repo root (prerequisite, not a guarantee); --writable every turn (#140); one task per ask; verify flash claims; after an empty/denied_empty reply never resume that stream (overrides stream reuse) - new stream with content inlined.
A2 denied_empty `next:` hint = new stream + inline needed content via --query-file; do not resume.
A3 Merge ag-empty-reply-causes + ag-permission-limit into the recipe entry; record E2 as open observation linked to its issue.
A4 (trust-list preflight) dropped: E2 shows it would pass and give false confidence.

## cx plan-window addendum (unanimous: cc.effort, Fable; 2026-11-08)
Measured: codex app-server `account/rateLimits/read` -> planType "prolite", primary = 7-day window (usedPercent 11), secondary = null. No 5h window on this plan; peerhub kept a 2026-10-30 X-5H row and reported "cx quota: incomplete", so agents read cx as 0%.
X1 Complete response + configured legacy pool absent and not covered by any reported duration -> record EvidenceState.ABSENT, reason "not reported by provider; plan=<planType>", normal freshness TTL. Malformed windows never produce ABSENT. Never manufacture usage.
X2 monitor_read_model: freshest row per pool (not first match); ABSENT pools skipped, not limiting; all pools ABSENT -> unknown, never 100%.
X3 QUOTA row "n/a not reported (plan prolite)" while fresh, then STALE; lesson `cx-plan-windows`.
X4 Profiles keep pools ["X-5H","X-7D"]. X5 planType carried in the ABSENT reason text only (sanitised), shown in PEERS suffix.
Tests: primary-only 7d + secondary null + planType; stale X-5H superseded; malformed primary -> no ABSENT; all-ABSENT -> unknown.

## Monitor redesign addendum (unanimous ACK: Fable, ag.argon, cc.effort, cx.effort)
Evidence: after the 300s TTL every QUOTA value rendered as '-' (screen carried no numbers); 'quota: incomplete' read as 0% by agents; pool codes had no legend; 6 low-signal blocks.
Resolutions: S1 date+DB source tag (Fable, ag); full DB path only when not the nearest upward store or on error (cc). S2 order peer-then-window kept for row stability (cc) - limiting pool is surfaced by S4 instead of re-sorting (Fable's sort rejected). S3 reset decided from resets_at<now and shown WITHOUT a number (cc; Fable's "reset 55%" rejected as misreadable); precedence n/a > ? > reset > out > low > aged > ok, so low/out are never hidden by aged (ag); low-by-projection needs >=2 samples (cc); aged threshold = configured TTL (Fable). S4 reason vocabulary: never measured | unmapped | not reported. S5 delivery wording keeps certainty terms (terminal/started/not started) - Fable's done/running/queued would misstate certainty. S6 keep one-line legend (cc). JSON stays byte-identical, so the state words are NOT added to JSON (Fable's enum point rejected).
Final layout:
L1 `PeerHub Monitor 2026-11-08 10:48Z | DB:explicit | quota age 9m`
L2 `next: peerhub observation refresh` (absent when all fresh)
POOLS: PEER POOL MEANING LEFT RESETS AGE STATE PROFILES (one row per pool; numbers never cut; PROFILES cell truncates)
legend: `ok low out aged reset n/a ?  |  G flash A argon 3P claude-via-ag C claude F fable X codex; 5H/7D`
PEERS: one line per peer: profile groups + limiting pool % or `? (reason)`; plan notes e.g. `X-5H n/a (plan prolite)`
footer: `facts ok 2026-11-08 | sessions 100 (2 active) | delivery terminal 166 / started 15 / not started 2 | telemetry 56/60 | details: --expand`
Cut to --expand: bar, exhaust-in, pace, Columns omitted, EXECUTION, SESSIONS, USAGE, Facts lines, latest-session lines. No new flags.
Tests: S8 + state precedence (ABSENT+reset in one peer), no-number reset, low needs 2 samples, next line absent when fresh, JSON byte-identical, width <=100 and narrow <48 path.
