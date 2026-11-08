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
Resolutions: S1 date+DB source tag (Fable, ag); full DB path only when not the nearest upward store or on error (cc). S2 order peer, family, window for row stability (amended by F10'); limiting pools are surfaced by S4 instead of urgency sorting. S3 reset decided from resets_at<now and shown WITHOUT a number (cc; Fable's "reset 55%" rejected as misreadable); precedence n/a > ? > reset > out > low > aged > ok, so low/out are never hidden by aged (ag); low-by-projection needs >=2 samples (cc); aged threshold = configured TTL (Fable). S4 reason vocabulary: never measured | unmapped | not reported. S5 delivery wording keeps certainty terms (terminal/started/not started) - Fable's done/running/queued would misstate certainty. S6 keep one-line legend (cc). JSON stays byte-identical, so the state words are NOT added to JSON (Fable's enum point rejected).
Final layout:
L1 `PeerHub Monitor 2026-11-08 10:48Z | DB:explicit | quota age 9m`
L2 `next: peerhub observation refresh` (absent when all fresh)
POOLS: PEER POOL MEANING LEFT RESETS AGE STATE PROFILES (one row per pool; numbers never cut; PROFILES cell truncates)
legend: `ok low out aged reset n/a ?  |  G flash A argon 3P claude-via-ag C claude F fable X codex; 5H/7D`
PEERS: one line per peer: profile groups + limiting pool % or `? (reason)`; plan notes e.g. `X-5H n/a (plan prolite)`
footer: `facts ok 2026-11-08 | sessions 100 (2 active) | delivery terminal 166 / started 15 / not started 2 | telemetry 56/60 | details: --expand`
Cut to --expand: bar, exhaust-in, pace, Columns omitted, EXECUTION, SESSIONS, USAGE, Facts lines, latest-session lines. No new flags.
Tests: S8 + state precedence (ABSENT+reset in one peer), no-number reset, low needs 2 samples, next line absent when fresh, JSON byte-identical, width <=100 and narrow <48 path.

## Profile / quota-balancing / parallelism addendum (unanimous ACK: cc.fable, cx gpt-6-astra, ag argon-high; 3 rounds)
Resolutions: D1 ag.standard stays flash-low (2 of 3; eco role + lesson #299 G burn). D5 ag.opus NOT packaged in 0.24.0: "no profile ships before model_ping confirms it" (Fable C18, Argon) beats "inert config" - added in the first release after 3P resets. Per-peer caps take the median of the three proposals.
P1 Tiers (names unchanged): standard=eco, effort=normal (default for implementation), deepthink=quality, <peer>.<top>=top (opt-in, never default).
P2 Matrix: ag.standard flash-low | ag.effort flash-HIGH (was medium) | ag.deepthink claude-sonnet-5-5-high (3P) | ag.argon argon-HIGH in place (was medium; top for ag) | cc.standard haiku | cc.effort sonnet/high | cc.deepthink opus/high | cc.fable fable-5-1/high PACKAGED, pools F-7D only (owner 2026-11-08: separate pool; shared-C unmeasured) | cx.standard luna/low | cx.effort sol/HIGH (was medium) | cx.deepthink sol/xhigh | cx.astra gpt-6-astra/high NEW (top) | cx.terra stays a user overlay.
P3 Compatibility: profile change => binding_change => resumed streams start a fresh generation (existing behavior); no silent re-binding. Release note lists every changed profile.
P4 Plan robustness: generalize X1-X5 to all peers: complete authoritative report missing a declared pool => ABSENT "not reported (plan X)"; failed/incomplete => UNKNOWN; never unlimited. cc C-5H stays (measured). Plan name = display text only.
P5 Benchmark (explicit, <=8 calls, one bounded objective repo task, correctness first then latency/usage): sol/xhigh, astra/high, astra/ultra, opus/high, fable/high (isolated between /usage snapshots, other cc traffic paused, to measure C vs F), argon/medium, argon/high; plus CLI-evidence check of cc effort > high (one call only if advertised). Decides: astra effort (high vs ultra), argon high confirmed, fable shared-C. Ties keep the simpler setting. Results -> docs/benchmarks/profiles-2026-11-08.md.
H Balancing rule (manual, documented): 1) lowest tier meeting the task class floor: bulk/mechanical=standard; docs=standard (formatting) / effort (writing) / deepthink (technical claims); code=effort, escalate to deepthink; review=deepthink (independent reviewer); design/debate=deepthink or explicit top. 2) drop candidates with any applicable pool <5% LEFT, UNKNOWN or stale. 3) prefer highest LEFT% / max(days_to_reset, 0.2) as a tie-breaker across pool families. Monitor prints ONE advisory line "suggest: eco=.. normal=.. quality=.. top=.." from cached observations (tops only in the top slot); never selects.
I Parallelism: default 4 active asks, ceiling 6, counted across batches and manual asks; per-peer caps cc 3 / cx 3 / ag 1 (raise ag to 2 only after observing no G-5H spike); pytest 1 run at a time (never with >3 asks); keep >=6 GB RAM free; on throttling reduce concurrency, never reroute. `batch --max-parallel` stays required; docs recommend 4. Documented in docs/batch.md (concurrency table) + one guide line.

## Monitor exhaustive review (unanimous after 3 rounds: cc.fable, cx.astra, ag.argon) - fix list
Sources: Fable, Astra, Argon round-1 reviews (all confirmed K1-K4, K6, K7; K5 = wording/honesty, not >100 cols).
F1 (K1/K2) Pool-less observations (pool_ref None: probe ERROR/UNAVAILABLE/peer-scope ABSENT) never become POOLS rows and never feed header age / next:. Header age = max age over rendered pool rows; `next:` only if a rendered row is aged/stale/?. A peer whose latest probe failed gets one PEERS note "probe error (age)" instead.
F2 (K3) PEERS: per configured profile group, its own limiting pool: `ag argon 64% (A-7D) | effort/standard 90% (G-5H) | deepthink <1% (3P-7D)`; plan notes appended.
F3 (K4) PROFILES cell without peer prefix.
F4 (K5) Footer 2 lines, honest: `facts ok 2026-11-08 | sessions >=92 active (sampled) | telemetry 122/165 | details: --expand` and `delivery (sampled): terminal 165 / started 15 / not started 0`. Never print a bare `?` count.
F5 (K6) `suggest: eco=.. normal=.. quality=.. top=..` line after PEERS, computed by rule H (tier floor; drop profiles with any pool <5%, ?, aged, reset, n/a-only; score = min over the profile's pools of LEFT%/max(days_to_reset,0.2); tops only in top=); a slot with no candidate prints `-`.
F6 (K7) DB path line only when the resolved path differs from the nearest upward store or on error (compare paths, not source); fix test_database_path_selection accordingly.
F7 (Fable#8) ABSENT ages like any row: use evaluated `state`, not `stored_state`; expired ABSENT -> aged/stale, never n/a forever.
F8 (Astra#7) cx probe: any malformed/non-dict window or incomplete envelope => no ABSENT (UNKNOWN), with tests for `secondary=[]`/"broken".
F9 Unknown pool families (X-UNK etc.) label `?`, not `unmapped` (which means "no profile").
F10 (Argon N1) Sort POOLS by peer, family, window.
F11 (Argon N2, partial) Legend keeps only state words (`ok low out aged reset n/a ?`); family meanings are already in MEANING. (Full removal rejected: agents need the state vocabulary - prior S6.)
F12 (Astra#8) `--group peers` must not report "never measured" from missing capacity data; load what it needs.
F13 (perf) Cache parsed profiles; replace the per-observation second-connection queries with one IN(...) query inside the frame snapshot.
F14 One FAMILIES table drives MEANING, legend and grouping; cx configured pools come from profiles (no second constant).
JSON stays byte-identical; tests: fix test_monitor_compact_view:74 and test_database_path_selection:103-104; add prefix-free PROFILES, aged ABSENT, suggest line, malformed cx window, pool-less rows hidden, sort order.
## Amendments (accepting both NACKs)
F4' footer says `sessions >=N retained (sampled)` (ACTIVE = resumable mapping, not running).
F1' `next:` also when any rendered row is reset, or a configured pool has no evidence at all; an empty POOLS table prints `no quota evidence; next: peerhub observation refresh`.
F10' Explicitly amends approved S2 ordering to peer, family, window (so 5H/7D of one family sit together); update S2 text in the plan and the affected tests.
F5'' (3 more rounds; final wording from cx.astra, ACK cc.fable/ag.argon): suggest stays FRESH-only (H unchanged); when no slot qualifies print `suggest: no eligible profiles; next: peerhub observation refresh`; PEERS prints `(aged)` once per line. Rejected: aged-eligible with 60 min / 20% margin (no measured burst data). Revisit only with measured burst evidence.
