# Changelog

All notable changes to PeerHub are recorded here, following
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
PeerHub remains on 0.x; breaking changes are called out explicitly.
Historical entries are reconstructed from Git tags, commit subjects and PR titles;
they do not claim new validation or publication evidence.

## [Unreleased]

## [0.24.6] - 2026-11-10

### Added

- Account state in `peerhub monitor` (#329): one `ACCOUNTS` line in the compact view, detail rows with `--expand` and `capacity.accounts` in JSON (present only when account evidence exists; capped at 50 rows with `accounts_truncated: true` when cut), all from one stored snapshot. Per peer: plan (display only), login state (`ok`/`out`/`?`), and the **login token** expiry (labelled as such, never billing; compact shows it within 14 days or when past; a missing expiry does not mean logged out). `?` = could not be determined, `n/a` = the provider does not expose it (API-key, Bedrock and Vertex logins have no plan or token expiry: `n/a`), `aged` = stale (account evidence ages after 6 hours). A failed probe keeps the last good reading, shown aged with a `probe error` note. Collected only by `observation refresh` / monitor collection from `claude auth status`, codex app-server `account/read` (`refreshToken` false) and `agy -p /credits` (envelope verified like `/usage`; endpoint access only, plan `n/a`, the Gemini accounts file is never read). The probes run in parallel under the one refresh deadline, once per provider, skip a peer whose reading is still fresh (a failed probe is retried after 5 minutes), spend no model turn, never substitute another local login, and a failure never degrades quota collection. Only normalized fields are persisted as the new `account` observation kind (logged-in state, plan label, token expiry); emails, organization names, ids, tokens, raw payloads and any identity-derived hash or reference are never stored. `n/a not reported (plan unknown)` notes take the plan a fresh account observation reports (a plan the quota probe reported is kept). JSON keeps the stored `reason` and adds `display_plan` with `plan_source: "account"` when a fresh account plan fills an unknown 5H plan. Residual subscription fields never become a plan for API-key, Bedrock or Vertex logins, and agy endpoint access needs a `SUCCESS` envelope with explicit zero token and turn counts. The agy token-use stop message names the command that consumed tokens (`/usage` or `/credits`). `--snapshot` stays read-only.
- `peerhub diag running [--json]`: read-only list of lease-active asks (unexpired claim of the current workspace generation whose claim generation matches the delivery, unacknowledged, certainty `NOT_STARTED` or `STARTED`) with peer, profile (read from the binding; null when the ask used no profile) / binding, stream, request id, delivery id, certainty, `started_at`/`elapsed_s` from the newest `session_call` start evidence (null without valid evidence) and `lease_expires_in_s`. Up to 20 rows (oldest first) plus a `truncated` flag; the human table shows the recorded start (`?` without valid evidence). The query starts from live claims and never reads the delivery history of finished scopes (a live stream's own history is still scanned through the existing `(stream_id, peer_id)` index; no index covers the claim generation). A lease is not proof a process is alive; there are no progress or staleness fields. It reads existing tables only: no provider call, quota refresh or write. Query cost is bounded for finished scopes; inside a live stream it grows with that stream's own delivery history (observed: 984 VM steps at 150 rows, about 45k at 9,000 rows; a measurement, not a latency guarantee). A bounding index is deferred to the next planned schema change.

### Fixed

- A provider-disabled agy bucket (`"disabled": true`, e.g. `3p-5h` while the weekly limit is hit) no longer keeps its last value. It is recorded as the existing ABSENT / not-applicable state with the reason `disabled by provider: <description>` (printable ASCII, at most 100 characters), renders `n/a` with no capacity or reset in compact, expanded and JSON output, is skipped like other not-applicable pools, ages to STALE like them, and returns to MEASURED when the provider re-enables it. Earlier rows are kept (#328).
- A peer-level (pool-less) quota probe failure no longer shows `PROBE:<age>` / `probe error` once a strictly newer MEASURED pool observation exists for the same peer and the same probe source (a poller counts as one source: ag `/usage` and its statusline fallback, and a poller that raised, stored as `quota_probe:<kind>`). Another probe source, another peer, or a failure from the same run still shows, and agy refusal / token-use / conflict rows are never retired by a later success; the stored history is unchanged (#328).
- `peerhub explain <lesson id>` (for example `monitor-probe-age`) now matches the exact lesson id before keyword search; order: guidance code, exact profile id, exact lesson id, keywords (#328).

### Changed

- The `ask_refusal` guidance (`peerhub explain ask_refusal`, `docs/guidance-reference.md`) links to the adapter guide instead of an unshipped Engram-era document (#327).
- The README "Doc map" is now one link to `docs/index.md`, and its repository layout lists the actual `tools/` scripts (#327).
- `source_records` reuses `CoreStore._row_to_record` instead of building `Record` by hand; behavior is unchanged (#327).
- Profile cards carry the 2026-11-10 benchmark evidence: `cx.standard`, `cc.standard`, `cc.effort`, `cc.deepthink`, `ag.standard` and `ag.effort` gain benchmark traits and evidence paths. `cc.standard` gets a new `use_for` (adds self-contained code) and `avoid_for` (design decisions, code review, not benchmarked; "multi-step reasoning" dropped), `cx.standard` and `ag.effort` get updated `avoid_for` text, and the `ag.effort` pool-mentioning trait is replaced.
- Two benchmarks with graded runs: [hard](docs/benchmarks/profiles-hard-2026-11-10/README.md) (integer interpreter, 100 cases; separates the low tiers, the top profiles still tie at 100) and [top-tier](docs/benchmarks/profiles-top-2026-11-10/README.md) (regex matcher, 115 cases; the cc tiers tie at 115/115 and `ag.effort` scores below `ag.standard`).
- `--allow-agy-token-use` and `observation refresh` help and the README name `/credits` and account evidence besides `/usage` quota; the guide's upgrade hint points to `peerhub diag running`.

## [0.24.5] - 2026-11-10

### Added

- Profile cards: every packaged profile carries `use_for`, `avoid_for`, `traits`, `evidence` and `card_checked`, built only from benchmarks, lessons, issues and measured quota. `peerhub explain ID` (exact profile ids are matched before the lessons lookup; unknown profile and "no card" are distinguished) and `peerhub profile show ID` print the effective card with the source layer of each key, `profile list` adds a truncated `USE FOR` column, and `--format json` carries the full `card`. A layer that changes `selection_mode`, `model` or `reasoning_effort` drops the inherited card (claims are bound to the binding they were checked against); the loader validates format and caps; the test suite checks that packaged evidence paths and lesson ids exist (issue refs: format only) and that `card_checked` is not older than `checked`.
- `python -m tools.quota_report --db <store> [--since ...] [--json]`: read-only per-pool utilization report. Closed 7D windows get exactly one verdict (EXHAUSTED_EARLY, POSSIBLY_UNUSED, USED, UNKNOWN), open windows get PACE (or EXHAUSTED_EARLY/USED once a 0% is sampled, UNKNOWN with a reason when stale or unanchored), 5H pools get observed exhausted window counts; thresholds are printed. First report: `docs/history/quota-efficiency-2026-11-10.md`.

### Changed

- Model facts re-observed 2026-11-10 (`defaults_revision` 2026-11-10.1): agy 1.5.4, claude 2.1.322, codex 0.167.0; no packaged model or effort changed. `tools.model_ping` PASS for cc.standard, cc.effort, cc.deepthink (model from the stream) cx.standard, cx.effort, cx.deepthink, cx.astra (model and effort from the session log) and ag.standard, ag.effort, ag.deepthink, ag.argon (model from the agy log); their `checked` dates advanced. cc.fable was not re-checked: its F-7D pool was exhausted until 2026-11-10T13:00Z, so it keeps `claude-fable-5-1` and `checked` 2026-11-08; the `claude-fable-5-2` candidate is checked in 0.24.6.
- The refresh recipe (`model-profiles.json`) states ping evidence strength (cx: model and effort; cc, ag: model only), advances `checked` only after a passing ping, and keeps cards evidence-only.

### Fixed

- A cc ask no longer becomes uncertain because an earlier stream line (typically a large tool result in a long ask) exceeded the 256 KB event cap: an intact later `result` event resolves the gap, like cx already did. An oversized `result` event, or a gap after the result, stays uncertain, and the skipped line is still reported as `oversized_event_count` (#325). Behavior and limits: docs/peer-limits.md.

## [0.24.4] - 2026-11-09

### Changed

- The compact monitor shows a `PACE` column (signed whole percentage points, `used% - elapsed%` of the window; `+` ahead of even burn, `-` behind; `-` without a valid anchor, also on aged rows) beside LEFT and RESETS, and the legend defines it. AIs balancing quota no longer need `--expand` for pace. JSON output is unchanged.
- `suggest:` ranks candidates without an applicable `fast` pool first, then the existing worst-pool score, then profile name; eligibility is unchanged and an all-fast tier still yields a pick.
- `peerhub guide` carries a balance recipe (suggest is advisory; reject stale/unknown/reset/<5% pools; prefer non-`fast`; compare 7D PACE, 5H only gates; PACE needs fresh AGE), drops monitor TTY/cycle trivia, merges duplicate lines and is bounded by bytes for the packaged defaults (<=60 lines, <=5,600 B LF-normalized) instead of the word heuristic. `explain pace` and `explain monitor pace` resolve to the monitor lesson, which defines PACE and the ordering. Rule H in `docs/batch.md`, the README and the `monitor` help match.
- The compact legend drops the `precedence:` line (kept in `explain monitor`).
- The `ag-recipe` lesson marks #302 (read_file denied with a trusted `-w`) as a past observation, closed 2026-11-09 as not reproducible; inlining content stays the recipe.

### Fixed

- Non-TTY human `monitor` output printed two frames (a pre-refresh `initial` frame and the completed one); it now prints only the completed frame (about half the bytes; `--expand` 32.7 KB to 16.3 KB). TTY redraw and `--json` framing (`initial` + `complete`) are unchanged.
- A pool at its exact reset instant now counts as reset (no capacity, PACE or suggestion).

## [0.24.3] - 2026-11-09

### Changed

- ag operational guidance documents the 30,000-byte Windows / 120,000-byte POSIX argv prompt limit, including catch-up history; split oversized prompts or use cc/cx, with a new request-id for a changed prompt (#309).

### Fixed

- Oversized prompts now report `ask_prompt_too_large`, the actual byte count and limit, and actionable recovery instead of an unchanged retry; exception and returned-status paths share the guidance (#309).
- `ask_claim_held` guidance and the new `caller-timeout-claim-held` lesson explain the #312 symptom: the store shows that ask completed with a full 6,327-character reply and released its claim at completion; the caller's tool had stopped waiting after the preamble, so later asks hit the still-running turn. Recovery: wait, then replay with the same `--request-id` (#312).

## [0.24.2] - 2026-11-09

### Changed

- Monitor watch defaults to one collection cycle when stdout is not a TTY, and watches until interrupted on a TTY. Explicit `--cycles 0` watches until interrupted everywhere.
- Compact monitor suggestions append `|<profile>` for the best eligible alternative in the same tier from a different peer, keeping the primary ranking unchanged.
- Packaged `monitor-states` lesson (`peerhub explain monitor`) explains states, precedence, alternative peers and cycle defaults.

### Fixed

- Compact monitor `low` now means more than 0% and at most 10% remaining (0% is `out`); a threat projection with at least two samples and more than 10% remaining shows yellow `fast`. The legend wraps to keep every state visible and lists precedence; `low`/`fast` win over `aged`. JSON values and schemas are unchanged.

## [0.24.1] - 2026-11-08

### Changed

- Guide profile advice now follows the task floors in `docs/batch.md`; the explicit ag.argon alternative while 3P is low lives in `docs/batch.md`.
- Renamed lesson `cx-plan-windows` to `plan-windows` for all peers; the old id remains searchable via keyword.
- Store discovery misses now exit 5 with `[store_missing]` and `next:` before creating files or contacting providers (#304). Migration: `peer register` in a fresh directory requires `--db .peerhub/core.db` or `PEERHUB_DB`; creation-capable commands can still bootstrap an explicitly selected store. Git worktrees fall back to the main checkout and its parents only after ordinary discovery misses; with `-w`/`--workspace`, fallback searches the main root only. Explicit `--db`, `PEERHUB_DB`, and nearest-upward discovery keep their precedence.

### Fixed

- Plan-window rule (plan-0.24.0 P4) now covers every peer, not only cx: when a complete, successfully parsed cc `/usage` report (session, week all-models, week Fable) or agy `/usage` report omits a declared pool, `observation refresh` records it ABSENT `not reported by provider; plan=unknown` (monitor: `n/a`, never 0% or unlimited). Malformed or partially parsed reports, agy conflicts and the agy statusline fallback record no ABSENT rows. Declared pools come from the profiles (`cc_pools`/`ag_pools` injected by `observation refresh`; probe defaults otherwise).
- cc `result.permission_denials` are now surfaced as `denied_actions` (`Tool: command`, redacted with the evidence sanitizer before being cut to 120 chars, at most 20, even when the reply has text), and a cc turn that ends with an empty reply and denied tool actions gets the `denied_empty` status like ag (#300, #303). Persisted and replayed results carry the same entries.
- The `denied_empty` error note is provider-neutral (`<peer> denied tool action(s): ...`) instead of always saying `ag`.
- New lesson `cc-allow-rules` (`peerhub explain cc allow rules`): Bash allow rules match the exact command string, so models that shape the command differently are denied in headless runs; prefer prefix rules such as `Bash(peerhub:*)` (#303; cause not reproduced, command shape is a hypothesis).

## [0.24.0] - 2026-11-08

### Changed

- Profile defaults revision `2026-11-08.2`: `ag.effort` changed to `gemini-3.8-flash-high` and `cx.effort` to `gpt-6.1-sol` high; packaged top profiles `cc.fable` (`claude-fable-5-1`, high; F-7D only, shared-C draw unverified) and `cx.astra` (`gpt-6-astra`, high; X-5H/X-7D) were added. Profile changes are binding changes, so resumed streams start a fresh generation. `ag.argon` stays `gemini-4-argon-medium` (benchmark tie rule; high scored the same for +24s). Benchmark: [profiles-2026-11-08](docs/benchmarks/profiles-2026-11-08.md) (one ceiling-limited task, not a ranking).
- `docs/batch.md` documents capability-matched profile balancing and the concurrency table (default 4 active asks, ceiling 6; cc 3 / cx 3 / ag 1); the guide adds one `Load:` line.

- Behavior change: `peerhub ask` exits 1 (was 0) for the `denied_empty` case described below; callers that treated an empty `delivered` reply with `denied_actions` as success must handle the new status.

- Breaking: compact human text is the default even on non-TTY output. Migration: add `--json` to scripts that consumed implicit JSON; `--format` remains a hidden compatibility alias.
- Breaking: global `--db` now takes precedence over `PEERHUB_DB`, followed by the nearest upward `.peerhub/core.db`. Resolved path and selection source appear on stderr or the monitor header.
- Monitor defaults to a compact POOLS/PEERS screen with a facts/session/delivery/telemetry footer. Last-known quota remains visible with configured-TTL aging; reset windows show no capacity number, and projected low capacity requires two samples in the same window. `--expand` preserves the detailed grouped renderer and its short codes. JSON output remains byte-identical; human-only history reads are read-only and cached for redraws.
- CLI errors include an outcome code and `next:` hint on one stderr line; command roots, verbs and batch contracts stay stable.
- Model observations refreshed to 2026-11-08, defaults revision `2026-11-08.1`: ag 1.5.3, cc 2.1.319, cx 0.167.0. Existing P0 evidence: model_ping 9/9 PASS; ag.deepthink skipped because 3P-7D was below 1%. No profile changes were included in that refresh; no new model pings were run.
- Guide documents `PEERHUB_DB` and the explicit ag.argon alternative while 3P is low, limited to tasks supported by measured capabilities; otherwise defer.
- cx plan windows: when a complete `account/rateLimits/read` response omits a configured pool (e.g. no 5h window on planType `prolite`), `observation refresh` records that pool as ABSENT "not reported by provider; plan=<planType>" (never malformed or partial responses; normal freshness TTL, then STALE). The monitor skips such pools when computing a peer's quota (freshest row per pool), shows them as `n/a not reported (plan ...)`, and shows `? (not reported)` instead of `incomplete`/100% when every pool is unreported. New lesson `cx-plan-windows`. JSON keys are unchanged.
- ag usability: `guide` and `peerhub explain ag` lead with one ag recipe (inline content via `--query-file`, "no tools", trusted `-w`, `--writable` every turn, one task per ask, never resume a stream after an empty or `denied_empty` reply). The `denied_empty` `next:` hint is now "new stream with content inlined; do not resume". Lessons `ag-empty-reply-causes` and `ag-permission-limit` merged into `ag-recipe`; the read_file denial with a trusted `-w` is recorded as an open observation (#302).

### Fixed

- `peerhub ask` no longer reports an ag turn as `delivered` when the response is empty and the provider denied tool action(s) (#300). The result status is `denied_empty` (certainty stays `TERMINAL`, `denied_actions` kept), with guidance `ask_denied_empty`. `batch` classifies it `UNVERIFIED` (`denied_empty`).

## [0.23.3] - 2026-11-01

### Documentation

- `peerhub explain` gains five operating lessons (ag empty replies and allow rules, ag bulk-profile heuristics, cx sandbox and prompt hygiene, quota shaping, shell batching on Windows); shipped in this release (#284-#290).

## [0.23.2] - 2026-11-01

### Fixed

- `guide` caps profile fields by character count, replaces terminal controls and Unicode line separators, and marks truncation with `~` (#277).

### Changed

- CI: the Windows full-suite job ceiling is 60 minutes (was 35) and prints the 25 slowest tests; workflow or selector changes run the full Windows suite (#280).

## [0.23.1] - 2026-10-31

### Fixed

- `ask`: a request with durable `prespawn_failure` evidence (state `NOT_STARTED`) no longer blocks its stream with `pending_earlier_record` / `session_binding_mismatch` once the binding changes; the record is superseded. `STARTED`, `MAY_HAVE_STARTED` and `UNCERTAIN` records are never consumed (#270).

### Changed

- `peerhub guide` states that ag needs `--writable` even for read-only asks, that `-w` must be the trusted project folder, and that shell commands need allow rules (#268). The guide stays at 60 lines.

### Documentation

- `docs/adapters/cx.md` documents the cx sandbox workflow: no `.git` writes, installs or pytest temp dirs, so the orchestrator commits and runs the suite (#271).
- cx.terra quick benchmark (#267); CI action bumps: setup-python 7.1.0 and pypi-publish (#257).

## [0.23.0] - 2026-10-29

### Changed

- Model profiles have one source: `model-defaults.toml` holds profiles, pools and observed versions; `docs/model-profiles/model-profiles.json` (now `schema_version` 2) keeps only the refresh recipe, so its `profiles`, `exact_model_pools`, `revision` and `observed_*` keys are removed and the schema no longer lists profile ids. Adding, changing or deleting a profile such as `ag.argon` is one toml table. Tests assert invariants instead of pinned values; `tools/model_ping` pings effective profiles (opt-ins included).
- `test_reset_credits` derives its credit grant/expiry from the current time; the fixed 2026-10-29 expiry made it fail once that date approached.
- Packaged model facts re-observed 2026-10-29 (claude 2.1.312, codex 0.165.0, agy 1.4.9): no packaged model or effort change. New cx model `gpt-6.1-terra` (tier and cost unverified) is not packaged; opt in through `~/.peerhub/config/models.toml` (see `docs/model-profiles/README.md`).

## [0.22.3] - 2026-10-26

### Fixed

- `batch run`: a worker setup failure (query write, argv builder, spawn) no longer drops the task row and crashes the aggregate report; setup failures are `FAILED`, post-launch exceptions are `UNCERTAIN` with child tree cleanup, and every declared task always appears in the report (#256).

### Changed

- Release-gate guard tests derive the expected release-candidate matrix cell count from `publish.yml` instead of a literal, so a matrix change fails in PR tests rather than at release (#254).

## [0.22.2] - 2026-10-21

### Fixed

- Release workflow expects all 10 release-candidate matrix cells (Python 3.11-3.15 x Ubuntu/Windows); v0.22.1 added 3.15 but kept the 8-cell check, so its `release-evidence` job refused and nothing was uploaded to PyPI. v0.22.1 is superseded by v0.22.2, which carries the same Python 3.15 and #249 fixes.

## [0.22.1] - 2026-10-21 (superseded candidate)

### Fixed

- Python 3.15 support: `requires-python` cap raised to `<3.16`, 3.15 added to classifiers and the CI/publish matrices (full suite passes on CPython 3.15.0).
- Concurrent first use of a fresh workspace no longer races on `workspace.generation` (#249): unique temp file per writer and atomic create-if-absent.

## [0.22.0] - 2026-10-21

### Added

- Immutable cc/cx account-bound peers via `account bind|list|show`, protected root validation, scrubbed child environments and account-scoped session identities.

- `peerhub batch run` validates flat task manifests, executes bounded parallel asks, and reports delivery status with cancellation and stable resume identities.

### Known limits

- ag multi-account is unsupported; bound-peer quota is not observed.

## [0.21.6] - 2026-10-20

### Fixed

- Show exact 0.5% and tiny positive quota remainders consistently as `<1%`, including PEERS (#205, #241).
- Keep on-pace conditional ETAs out of exhaustion headlines and EXHAUST-IN alerts without changing forecast policy (#210, #241).
- Let explicit monitor snapshot groups override invalid display defaults (#229, #241).
- Reject non-finite monitor intervals and timeouts before collection in both JSON schemas (#230, #241).
- Retain buffered Codex JSON-RPC responses after app-server exit and safely ignore non-object messages (#232, #241).
- Select shared-pool readiness evidence by capture-first parsed instant, then capture sequence (#239, #241).
- Preserve existing agy pool drift and its check date after failed or unmeasured probes (#240, #241).

## [0.21.5] - 2026-10-20

### Fixed

- ag empty responses name the cause: agy headless auto-denies tools that need the `command` permission and returns an empty response; the ask result now carries `denied_actions` and the CLI prints a one-line note (#195).
- Monitor: a tiny non-zero quota remainder shows `<1%` (0% always means exhausted) and `cx reset credits` uses relative time (#189).

## [0.21.4] - 2026-10-20

### Added

- Complete the ag exact model/pool registry for flash, argon, Claude opus/sonnet and GPT OSS slugs; add `ag.argon` (`gemini-4-argon-medium`, A-7D) as the tenth profile. Existing tiers remain unchanged; arbitrary-model monitor/readiness integration remains out of scope (#194).
- `peerhub profile list` shows Known models (pool), with additive `known_models` JSON; the bounded guide includes argon and slug discovery (#194).

### Fixed

- Find quota reset evidence across all error lines, recognize cc `You've hit your 5-hour limit` and preserve hour-only resets such as `3pm` raw. Advice keeps uncertain-stream recovery requirements because a stream may remain quota-blocked after reset (#194).
- Apply `PEERHUB_LIVE_AG_MODEL` to public ask measurements, annotate only cases applying it, and reject unknown registry slugs with a stale metadata hint (#194).
- Monitor headers and EXHAUST-IN cells show `past forecast` when quota remains after a forecast passes, reserving `exhausted` for zero remaining quota; INSUFFICIENT means insufficient projection data (#194).

## [0.21.3] - 2026-10-19

### Fixed

- Uncertain asks whose provider error is a quota/usage limit (ag exit 3, stderr-only errors) now report `provider_limit` and a one-line wait-for-reset note; status stays uncertain because edits cannot be excluded (#186).
- Refusal classifier recognises cc limit phrasings such as `You've hit your limit`, `Claude AI usage limit reached` and `5-hour limit reached` (#186).
- Live gate: `PEERHUB_LIVE_AG_MODEL` may replace the ag model when its default pool is exhausted; every JUnit case then records `live_ag_model_override`. For v0.21.3 the ag canary ran on `gemini-4-argon-low` (A-7D) because G-7D was exhausted: the packaged `ag.standard` model was NOT exercised (#191).
- Live-gate quota canary waits 60s instead of 30s for slow cc usage probes (#183).

### Changed

- Monitor: the soonest-exhaust header says `exhausted` for an empty pool, probe age uses `2h57m`-style durations, and the INSUFFICIENT legend reads `window just started` (#189).

## [0.21.2] - 2026-10-19

### Changed

- Share session receipt validation between session inspection and monitor, and use one profile layer loader for CLI configuration and runtime resolution (#177).
- Remove the unused `format_dashboard` renderer and rename its regression test to identify the active `format_frame` renderer (#177, #178).

### Fixed

- Fence open stores on changes to either lineage marker (`workspace.generation` or `restore.epoch`), while allowing first-generation adoption without fencing existing connections (#177, #178).
- Resolve monitor profiles from the selected store's workspace root (#177).
- Bound context benchmark subprocess time and output, and resolve Windows `.cmd`/`.bat` shims through the shared binary resolver (#177, #178).

### Docs

- Document in-flight ask behavior across upgrades and restarts; wait for running asks (they lock dependency files), inspect results before re-sending, and recover from a broken upgrade (#175, #180, #181).
- Align `ag` adapter documentation with its explicit `--writable` requirement and place deferred backlog start triggers and deliberate probe/response-field deferrals under `docs/STATUS.md` ownership (#167, #173, #177).

## [0.21.1] - 2026-10-19

### Changed

- Uncertain ask guidance and guide now direct default-timeout asks to a new stream with explicit `--timeout-seconds` (long reviews: 1800 or more), preserving retry cautions (#169, PR #171).

## [0.21.0] - 2026-10-18

### Added

- Cached-only `peerhub diag readiness [--json]`: configured profile readiness from stored quota and matching delivery evidence, with shared pools and contextual diagnostic actions; no provider calls or writes (#145, part 1).

### Changed

- Monitor QUOTA visual refresh S2 adds semantic colors, precise remaining bars, compact per-peer probe notes, a space-permitting state legend, and the soonest exhaustion summary (#147 S2).

### Fixed

- Skip oversized cx events when a later intact answer and completion establish delivery; preserve uncertainty for lost terminal evidence (#164, part A).

## [0.20.0] - 2026-10-18

### Breaking

- **Bare `peerhub` exit 2 -> 0:** Invoking `peerhub` without arguments now prints the offline `peerhub guide` and exits successfully (#121, #153).
- **Native `ag` requires explicit `--writable`:** Because `agy` does not enforce a read-only sandbox when `--mode accept-edits` is omitted, native `ag` execution fails closed before spawn (`PrespawnError` / `failed_not_started`) unless `--writable` is set. Cached replay of acknowledged requests still returns before policy validation (#140).
- **Single-peer ask stream fencing:** `peerhub ask` rejects targeting a different peer on a stream with established `ask:` prompt records (`StreamPeerMismatchError`, exit 2, guidance `ask_stream_peer_mismatch`) (#125).
- The deprecated monitor `--legacy` alias reaches its scheduled sunset and is no longer accepted in v0.20.0. Use `peerhub monitor --json --schema-version 1.1` for the frozen machine contract; grouped human output remains the default.

### Added

- Guidance registry (`peerhub/cli/recovery.py`) maps ask outcomes to meanings, causes, recovery argv, cautions, documentation, and contextual exit codes. Offline `peerhub explain CODE [--json]` exposes exact-code recovery guidance (#117, #150).
- Offline AND-token lessons lookup through `peerhub explain <free words>`, packaged `lessons.toml`, generated `docs/lessons.md` with drift checks, and a compact guide pointer (#117, #150).
- Factual tool-only turn completion metadata (`empty_response: true`, `task_effects: "unverified"`) when a provider completes tool actions without a final text message (#126, #150).
- Argon `A-7D` quota pool: group-derived Antigravity pool labels (`G-*`, `3P-*`, `A-*`), `gemini-argon-wtu-weekly` mapping to `A-7D`, and exact `gemini-4-argon-{low,medium,high}` model mappings in both model-profile artifacts (defaults revision `2026-10-18.1`), backed by raw usage fixtures and parser tests (#149).
- Weekly Dependabot updates for pip and GitHub Actions, plus `SECURITY.md` with vulnerability reporting guidance and supported-version scope (#151).

### Changed

- Monitor quota-first dashboard: one-line status header with UTC timestamps and database source, followed by a deterministic QUOTA table (`PEER POOL REM [bar] RESET-IN PACE STATE AGE`; `EXHAUST-IN` at widths of at least 100 columns). Quota rows precede PEERS, EXECUTION, SESSIONS, USAGE, and CONTEXT; short viewports disclose omitted rows with `peerhub monitor --snapshot --group capacity --expand`. Stale and unknown rows preserve identity and reasons; snapshot rendering does not synthesize a refresh (#146).
- Remaining-quota bars fill consistently, all capacity rows stay together, and reset countdowns are separated from burn forecasts. USAGE falls back to input plus output tokens and shows cache hit percentage when provider total tokens are absent (#146, #147).
- Missing-store errors include the selection source, a quoted creation command using global `--db`, and `Learn: peerhub guide` (#121).
- `pending_earlier_record` guidance describes processed-status-dependent outcomes (`delivered`, `not_executed`, `failed_not_started`, `uncertain`); explain JSON represents context-dependent exit codes as lists (#135).

### Fixed

- Monitor QUOTA `RESET-IN` counts down from measured provider `resets_at` timestamps; stale and unknown observations retain `-` (#146).
- Monitor displays cx reset credits and soonest expiry from existing read-only evidence; fixture coverage verifies cc last-good quota and AGE survive a `/usage` timeout (#157).
- Windows stderr decoding consults console output and OEM code pages (`GetConsoleOutputCP` / `GetOEMCP`) before UTF-8 replacement, preserving localized launcher errors including cp949 messages; launch failures receive `ask_launch_failed` guidance (#120).
- Bounded terminal provider error summaries survive verbose startup stderr; aggregate cx response text is retained unless explicit phase metadata distinguishes final text from commentary (#127).
- Review follow-ups add an empty-response stderr note, restrict selector guidance to actual selector argument errors, and provide specific guidance for unsupported store schemas, compare-and-swap conflicts, and idempotency conflicts (#153).

### Docs

- Peer sandbox limits and an installed-CLI profile smoke recipe, linked from the documentation index (#134, #148).
- `ag` silence-timeout guidance: leave it unset or set it at least as high as `--timeout-seconds`; long-task examples use `--timeout-seconds 7200` (#118).
- Windows upgrade instructions: stop monitor, upgrade, then restart; explain temporary PyPI index lag (#123).
- Generated guidance and session lifecycle reference with registry parity checks, qualified session-state definitions, safe actions, and contextual exit codes (#135).
- Windows PowerShell operations guide covering parallel asks, `$using:`, quoting, `--query-file`, non-ASCII paths, and parser-verified examples (#136).
- Aligned guide, README, remote-ask, and migration documentation with current store selection, write-policy, and recovery rules (#130, #134, #148).

## [0.19.0] - 2026-10-17

### Breaking

- **Implicit `./peerhub` store discovery removed:** Removed implicit `<cwd>/peerhub` subfolder walk during store discovery (owner-approved O-1 in October 2026 audit). Store discovery now searches `.peerhub/core.db` in the current directory and parent directories only. Explicit `--db PATH`, `PEERHUB_DB`, and `--workspace` selection are unchanged. When no store is selected and a legacy `<cwd>/peerhub` store exists, missing-store error emits a hint to use `--db` pointing to it; stores of other schema versions remain refused as usual.
- **Bare monitor changes:** Default monitor viewport prioritization now ranks `capacity` and `peers` ahead of other low-priority groups in short viewports (default behaviour change), dropping other groups before clipping quota rows. Loaded quota rows (`capacity` and peers quota) are shown in full by default across all pools and windows without `--expand`; in short terminals rows beyond the viewport are disclosed as `+N rows hidden by terminal height` (distinguishing loaded data from displayed rows). When a detail loop stops early due to viewport height, one line `+N rows hidden by terminal height` is emitted with reserved space.

### Added

- `peerhub monitor --expand`: human dashboard option (supporting watch and `--snapshot`) rendering all loaded rows of detail groups (`sessions`, `usage`, `context`, `runtime`) instead of capping at 2 rows (quota is always fully shown; `--expand` applies to other groups). Expanded session rows include the stable session handle. Bounded by existing DB read caps (100) with truncation disclosure; rejects `--expand` with `--json` (Slice 1).
- Quota displayed by default: loaded quota rows (`capacity` and peers quota) are shown in full by default across all pools and windows without `--expand`; in short terminals rows beyond the viewport are disclosed as `+N rows hidden by terminal height` (distinguishing loaded data from displayed rows).
- Peers pools: model pools and observed versions metadata in `docs/model-profiles/model-profiles.json` and packaged `model-defaults.toml` with cross-artifact parity verification (S1); multi-pool selection in monitor peers view selecting minimum remaining quota across fresh measured pools for mapped profile models; explicit `incomplete` and `account-wide`/`unmapped` status handling (S2).
- Drift report persistence inside quota observation payload, retaining unknown bucket IDs and comparing installed CLI versions against packaged baselines (S3).
- Monitor model facts drift line (`Facts: needs_recheck (...) - refresh-model-profiles`) and detailed JSON output without extra probes (S4).
- Offline `peerhub guide` subcommand (<=60 lines, ~900 tokens) providing `.standard`-first profile table, session reuse examples, delivery safety guidance, discovery commands, and drift recovery (S5).
- Compact Safety section in `peerhub guide` detailing per-peer write boundaries (cx sandbox enforced, cc enforced, ag NOT enforced per issue #140), uncertain/pending_earlier_record recovery rules, core.db store hazards, cost side effects, and secrets persistence.
- Knowledge Placement Policy in `docs/knowledge-placement.md` (13-point policy) governing installed-release essentials, evidence-dependent lessons, and safety guidance placement; linked in `CONTRIBUTING.md` and `.github/PULL_REQUEST_TEMPLATE.md`.
- Efficiency section in `peerhub guide` (<=3 lines) covering role-based stream reuse, `.standard`-first economical baseline, and `--query-file`/`--json` transport choices.
- Report Issues section in `peerhub guide` (<=3 lines) detailing repository issue submission, `gh issue list --search` triage, environment details, and privacy redaction.
- Guide impact policy in `CONTRIBUTING.md`, PR verification checklist in `.github/PULL_REQUEST_TEMPLATE.md`, and `guide-impact` issue label for tracking safety-fact updates across PRs.
- Bounded delta reads in Session Bridge for native, local history, and redirect-candidate delta queries, paging reads and stopping before the current record.

### Changed

- Documentation diet: archived obsolete 10,368-line call map (`peerhub-production-call-map-R1.json`) and 4 historical debate documents (4,777 lines across session monitoring/reuse/usage review) to Git history, backed by a comprehensive archival ledger (`docs/history/ledger-2026-10-17.md`) preserving retrieval commands, SHA-256 hashes, and accepted/rejected/deferred decisions. Shrunk `docs/design/README.md` to active/deferred/history links and unified status ownership under `docs/STATUS.md`.
- Monitor `--legacy` sunset extended to 0.20.0: explicitly extended the sunset of the hidden deprecated `--legacy` alias (and frozen schema 1.1 watch) through 0.19.x to 0.20.0; removal deferred as no removal evidence is needed and existing callers remain supported.

### Fixed

- Preserved write access on resumed Codex (`cx`) sessions when `--writable` is enabled by passing `-c sandbox_mode="workspace-write"` on `exec resume` (resumed sessions previously defaulted to read-only sandbox mode, dropping write permissions even when the session was created writable). Safe read-only (`-c sandbox_mode="read-only"`) is maintained when `--writable` is not set. Scoped guaranteed read-only sandbox enforcement to `cx` only; documented `cc` read-only enforcement via headless permission denial and `ag` read-only as provider-controlled without sandbox enforcement (known limitation: headless `agy` does not enforce a read-only boundary when `--mode accept-edits` is omitted). Added opt-in live canary tests for read-only sentinel creation.
- ASCII and plain monitor separators: replaced Unicode middle dot (`·`) with ASCII pipe (`|`) in `--view plain` and non-Unicode outputs, avoiding `?` replacement artifacts in ASCII terminal environments (#114).
- Redirected `--format auto` JSON: `--format auto` emits JSON when standard output is not a TTY (redirected or piped) (#114).
- Missing-store JSON error parity: core read commands emit structured JSON (`{"status": "UNAVAILABLE", ...}`) when a database is not found and JSON output is requested (or under redirected `--format auto`), matching the machine error contract (#114).

## [0.18.2] - 2026-10-17

### Added

- Monitor peers group (default set): per peer quota -> profile/model [config layer] -> latest session (fresh/reused, generation) -> context; unknown stays unknown, stale quota is shown as STALE, no model-specific quota-pool inference, sample truncation disclosed.
- Model-fact provenance: footer with revision, checked date/age and source commands; per-profile source in docs/model-profiles/model-profiles.json; 2026-10-16 CLI observations (cc 2.1.301, codex 0.163.0, agy 1.4.0). Profiles unchanged; agy gemini-4-argon-* noted as available, not selected.

### Changed

- **Breaking - store schema:** schema version 4. Stores written by this version cannot be opened by earlier releases (they refuse with their existing future-schema error); back up .peerhub/core.db before first use. Core runtime connections come from one factory (WAL, busy_timeout, foreign_keys, recursive_triggers).

### Notes

- Extension DDL ownership, trigger-definition verification and the rest of the schema-v2 proposal are intentionally deferred (see docs/design/monitor-peers-view-2026-10-16.md).

## [0.18.1] - 2026-10-16 (superseded candidate)

_Never published as a release; shipped in v0.18.2._

### Changed

- Make the grouped TTY monitor adapt to terminal height and width, prioritize
  important group summaries, and show the selected database/source plus safe
  read-error context. Refresh warnings and token-use stops are visible in the
  frame; resize redraws reuse the cached snapshot.

## [0.18.0] - 2026-10-16

### Changed

- **Breaking — monitor output defaults:** bare `peerhub monitor` now renders a group-aware
  visual dashboard on TTYs and aligned plain sections when redirected. JSON is
  explicitly machine-oriented (`--json`, schema 2.0 by default); the captured
  schema 1.1 contract remains available with `--schema-version 1.1`. The hidden
  `--legacy` alias is deprecated through 0.18.x and will be removed in 0.19.0.
  Existing schema 1.1 scripts should use `--json --schema-version 1.1` explicitly.
- Monitor group selection, catalog, defaults, snapshot behavior, and provider
  collection cadence are unchanged.

## [0.17.0] - 2026-10-16 (superseded candidate)

_Never published as a release; shipped in v0.18.0._

### Changed

- **Breaking — monitor default output:** bare `peerhub monitor` now watches the
  selected grouped view in human and JSON modes, replacing the prior compact
  schema 1.1 dashboard. Existing ungrouped dashboard consumers can use
  `peerhub monitor --legacy`; explicit `--group` continues to select grouped
  schema 2 output and cannot be combined with `--legacy`. See
  `peerhub monitor groups` for the ordered group catalog and descriptions.

### Fixed

- Detect peers omitted by the bounded monitor scope sample and mark execution
  and coverage as partial instead of reporting a complete sample.

## [0.16.1] - 2026-10-16

### Added

- Grouped read-only monitoring for sessions, delivery execution, usage, context,
  runtime provenance, capacity, coverage, and store health. Queries are bounded,
  share a consistent snapshot, and do not invoke provider CLIs.
- Capacity views include typed quota projections and source/pool provenance;
  context remains unknown unless provider evidence proves its identity and freshness.

### Fixed

- Corrected scale-test inventory classification and provider usage normalization,
  including timezone handling for quota reset dates.

## [0.16.0] - 2026-10-14

### Added

- **CLI inspection and monitoring:** grouped read-only status, profile, session,
  diagnostic and observation views around shared projections. Output schemas and
  monitor cadence are consistent across text and JSON consumers. List and
  session reads are bounded; Core record totals still scan history. Inspection
  commands do not mutate state.
- **Session reuse PR3:** Python `ask(fresh=True)` / `ask(session=HANDLE)` and
  mutually exclusive CLI `--fresh` / `--session HANDLE`. Fresh sessions receive
  bounded Stream catch-up. Exact selection validates scope, lineage, adapter,
  effective binding and continuity; rejection never allocates a substitute.
- Durable normalized selectors participate in request identity. Replay returns
  the original session snapshot after expiry; changed selectors conflict.
  Definitive unstarted queued selector failures atomically commit rejection
  evidence, result and Offset progress, preserving uncertain-execution guards.
- **Breaking — complete request identity:** new prompts include adapter
  fingerprint and workspace lineage. Changed proofs conflict before replay;
  older prompts lacking them remain inspectable but cannot replay through ask.
- **Session reuse PR2:** retained session history with opaque handles, monotonic
  generations and an MRU pointer. Default asks choose the latest compatible
  retained session, including an older binding that becomes compatible again.
  Retention caps each peer/Stream at 20 non-tombstoned sessions and expires
  sessions after more than 30 days of inactivity; audit tombstones remain.
- Fenced, exactly-once STARTED call receipts and terminal telemetry receipts
  update monitoring counters atomically. Unprovable legacy totals remain null;
  billing usage does not become context occupancy.

### Changed

- **Profiles — model defaults refreshed (#96):** `cc.standard` now selects
  `claude-haiku-5-5`; `ag.deepthink` selects `claude-sonnet-5-5-high`, replacing
  retired `gemini-3.1-pro-*` models. The intermediate `ag.pro` profile is
  removed in this release.
  Availability still depends on the provider catalog and quota.
- **CI — triggers narrowed:** branch pushes no longer start the full matrix;
  pushes to `main`, pull requests targeting `main`, and manual dispatch still do.
- **Breaking — model profiles:** *.pro removed (retired IDs raise the existing unknown-profile configuration error); cx.effort medium (`gpt-6.1-sol`); ag.effort flash-medium (`gemini-3.8-flash-medium`). 9 profiles retained: `{cc,cx,ag}.{standard,effort,deepthink}`.
- **Breaking — PR2 ask output:** JSON always carries nullable `session` and
  `error`; text output ends with one session trailer after allocation. Scoped
  request-id replay returns the original durable handle/mode/reason/telemetry
  snapshot without session lookup or provider execution. Selector-less historical
  asks remain inspectable but have no replay shim; proven unstarted legacy
  prompts are consumed with `legacy_selector_unsupported`, and uncertain work
  remains blocked. Registry/receipt migration is atomic and idempotent.

- **Breaking — session reuse PR1:** removed PeerHub `ask --resume`, the Python
  `ask(resume=...)` keyword and runtime caller preference, without an alias.
  Ordinary asks reuse the compatible current peer/Stream session; use a dedicated
  Stream for a separate task. Vendor resume arguments remain adapter details.

### Fixed

- The live release gate accepts the session telemetry receipt in terminal
  evidence, stops after a failed stage to avoid further provider calls, and
  removes stale stage XML before a new run.
- `diag health` now reports `PARTIAL` with a nonzero exit code when a requested
  observation database cannot be read, instead of hiding that failure behind
  an otherwise healthy store result.
- **Termination confirmation — stuck-streams PR 1 (#95):** timeout, abort, and
  error evidence now records whether the whole process tree was confirmed
  terminated. A dead parent with surviving children remains unconfirmed;
  termination evidence alone does not automatically unblock a timeout.
- **Output drain-and-truncate — stuck-streams PR 2 (#97):** exceeding the output
  cap no longer kills the provider. Pipes keep draining while retained output
  preserves a bounded head and tail and reports `output_truncated`. Bounded
  event parsing can still capture completion and usage beyond the retained text;
  oversized or malformed events cannot establish successful completion. Total
  and silence deadlines still apply.
- **Provider refusal classification — stuck-streams PR 3 (#98):** quota,
  usage-limit, invalid-model, and authentication refusals with sufficient
  pre-work evidence return `NOT_EXECUTED`, consume the prompt without replay,
  and leave the stream reusable on a clean session generation. Ask and diag
  expose the refusal reason and available reset time. Process certainty stays
  `STARTED`; failures after tool execution, incomplete evidence, and writable
  Agy refusals remain uncertain. Successful answer text cannot trigger refusal
  settlement; repeating a request ID returns its stored refusal.
- Retained-session startup seeds historical counters/scopes only during initial
  migration or when a seed table is absent, avoiding lifetime-history aggregation
  on every ask. Atomic upgrade and monotonic generation allocation are preserved.
- Ask execution evidence now selects only the requested record/peer deliveries
  in SQL, instead of loading the peer's lifetime evidence history. Extension-owned
  delivery/evidence indexes also accelerate replay and per-stream refusal reads;
  attempt ordering, certainty and retained session identity are unchanged.
- Resume capability is independent of each call. Native context watermarks stay
  at the consumed prompt, so concurrent records and later same-peer responses
  enter the next delta. The idempotent writer upgrade corrects known CLI capability
  values and invalidates old native continuity lacking complete context proof,
  preserving custom local sessions, immutable replay and uncertain blocking.

## [0.15.2] - 2026-10-12

Contains the work planned for 0.15.1, which was never tagged or published:
the live gate caught an IANA timezone regression, so 0.15.2 supersedes it.

### Fixed

- **Timezone — IANA zones rejected in the 0.15.1 candidate:** resolve Claude `/usage` IANA
  names such as `Asia/Seoul`, `America/New_York` and `America/Los_Angeles` with
  standard-library `ZoneInfo`, preserving DST-aware reset dates derived from
  `now` in the resolved zone and fixed offsets for known abbreviations. Declare
  `tzdata` for hosts without an IANA database, including Windows. Missing
  timezone data yields `UNAVAILABLE` with reason `timezone_database_missing`;
  unknown names retain `unsupported_timezone:<name>`.

- **Privacy — dashboard JSON serialization:** replaced blanket `asdict()` in
  `dashboard_data()` with explicit serialization. Raw reset-credit tokens
  (`id_ref`) are removed and replaced with 12-character SHA-256 hashes
  (`id_hash`) matching `diag quota`. Unallowlisted activity, quota, and
  diagnostic payload fields are scrubbed from dashboard JSON output
  in `peerhub diag --json` and `peerhub monitor --json`.
- **Timezone — quota reset parsing:** resolved known timezone abbreviations
  (`EDT`, `EST`, `CDT`, `CST`, `MDT`, `MST`, `PDT`, `PST`, and similar) to fixed
  UTC offsets in Claude usage probe parsing. Derived the calendar date from `now`
  converted to the resolved target timezone instead of local system time, fixing
  up to 13-hour reset offsets across day/midnight boundaries. Explicit unsupported
  timezones now fail closed as `UNAVAILABLE` evidence with reason
  `unsupported_timezone:<name>` instead of silently falling back to local time.
- **Display — header wide-character path truncation:** truncated header store
  path segments by cumulative display width using `visible_len` instead of code-point
  slicing, preventing header splitting across lines when file paths contain wide
  (e.g. Korean) characters.

## [0.15.0] - 2026-10-12

### Changed

- **BREAKING — projection JSON:** `status` is replaced by `state`;
  `basis` and `exhausts_before_reset` are removed. Consumers must use the single
  `projection` object with `pace_pt`, `projected_at_reset`, `state`,
  `exhaustion_at`, `measured_at`, `anchor_basis` and `reason`. No compatibility
  aliases are emitted. The optional `recent_samples` projection argument is
  removed; forecasts use conditional window averages. Dashboard JSON also
  supplies `forecast_basis` and the shared `threats` summary.
- **BREAKING — forecast thresholds:** forecasts require at least 5 percentage
  points used and 10% elapsed for windows shorter than one day, or 20% elapsed
  for windows of at least one day. Supported projected usage at reset must reach
  105% to be a threat; lower values are on pace. Below either support gate the
  state is `insufficient`. Fresh measured exhaustion takes precedence over
  support gates. Numeric PACE remains available with consistent anchors even
  below the forecast gates. Thresholds are policy, not accuracy guarantees;
  inferred reset-minus-duration anchors remain unvalidated and conditional.
- **BREAKING — store behavior:** monitor and diagnostics return exit **5** for
  a missing selected store before any provider probes. They never implicitly
  create a store or redirect an explicit missing path. Initialize deliberately
  with `peerhub --db <path> peer register --peer <id> --name <name>`.
  Observation refresh also refuses a missing automatically selected store;
  explicit `--db`/`PEERHUB_DB` collection bootstrap behavior is retained.
- **BREAKING — screen layout:** monitor and diagnostics now show a threat
  summary, one stable pool table (usage, PACE(pt), reset, forecast, freshness),
  credits, latest asks, probe health and one legend/controls line. The WIN column
  and per-row conditional suffix are removed. Update text-output consumers.
  Supported TTY/VT output redraws in place; non-TTY/no-VT output appends one
  escape-free frame per cycle. JSON emits one object per cycle.

### Added

- Read-only `python -m tools.quota_backtest --db <store> [--json]` replays the
  shared engine and reports completed-window threat/exhausted, false alarm,
  missed and quiet/ok classes. Open windows are not scored. Zero completed
  exhausted windows means accuracy is **not demonstrated**.
- CHANGELOG-derived release bodies via
  `python -m tools.release_notes --version X.Y.Z`; supplying the extracted section
  is a manual draft-release step.

### Fixed

- Singular threat wording and a terminal-width legend/controls line.
- TTY cursor restoration, shrinking frames, resize handling and bounded redraw
  cadence; countdown redraws do not collect extra observations.
- Documentation removes unsupported recent-rate and everyday-test timing claims
  and aligns opt-in xdist and disposable-venv guidance.

## [0.14.0]

### Added

- Quota exhaustion projections and a refreshed monitor dashboard; `monitor
  --system-dir` and store path display (#80).
- Untested remote-ask SSH recipe and A2A inbound-server design record (#79).

### Fixed

- ACL-safe writable canary workspaces, clearer unconfirmed Windows kill errors
  and neutral native-resume prompts (#76).
- Store-selection guidance for monitor/refresh and maintainer handoff docs
  (#74, #77, #78). Version prepared in #81.

## [0.13.0]

### Added

- Opt-in real-provider canaries for usage, native resume, writable asks and
  cancellation (#72).
- Maintainer release/evidence guidance, Core recipe, AGENTS.md and platform
  limitations (#68, #70).

### Changed

- Requirement-to-test links derive from the catalog SSOT; M2/M3 dimension
  checks close catalog gaps (#71).
- Current spec and documentation are English (#67).

### Fixed

- Quota validity, explicit binary override, shared process-tree kill,
  console warnings and diagnostic tool errors (#69). Version prepared in #73.

## [0.12.6]

Explicit native resume for cc/cx/ag.

## [0.12.5]

Per-ask usage telemetry, bounded catch-up and native process-tree cancellation.

## [0.12.4]

Candidate-bound M2/M3 catalog release evidence.

## [0.12.3]

Unified verification, tested distribution bytes and enforced slow/E2E live gates.

## [0.12.2]

Writable asks, A2A 1.0 wire and retirement of the v0 runtime surface.

## [0.12.1]

Windows process-tree cancellation confirmation.

## [0.12.0]

Extension-first public CLI cutover and package support matrix.

## [0.11.0]

M1 Core, first-party extensions, real adapters and release gates.

## [0.10.1]

Packaged provider model-profile refresh.

## [0.10.0]

Smart lifecycle parity: backup, restore and workspace reset.

## [0.9.1]

Release: prepare v0.9.1 patch release and decouple live gate (historical tag).

## [0.9.0]

Ci(publish): decouple PyPI publish from the self-hosted live gate (historical tag).

## [0.8.0]

Release: prepare v0.8.0 profile and CLI audit (historical tag).

## [0.7.0]

Chore(release): bump version to 0.7.0 (historical tag).

## [0.6.0]

Chore(release): bump version to 0.6.0 (historical tag).

## [0.5.0]

Chore(release): bump 0.4.0 -> 0.5.0 -- R4/P4b governance gateway convergence + quality audit (historical tag).

## [0.4.0]

Chore(release): bump 0.3.0 -> 0.4.0 -- R0-R5 roadmap + D-CTX Increment 1 complete (historical tag).

## [0.3.0]

Chore(release): bump 0.2.0 -> 0.3.0 -- dotdir consolidation backlog complete (historical tag).

## [0.2.0]

Docs+chore(release): reflect completed AI-collaboration integration fixes, bump 0.1.16 -> 0.2.0 (historical tag).

## [0.1.16]

Docs: reflect completed LegacyTranslator retirement across README and design docs (historical tag).

## [0.1.15]

Feat(adapters): add cx.effort and cx.deepthink profile tiers with reasoning-effort support (historical tag).

## [0.1.14]

Feat(config): layered model and profile configuration with workspace/global/default precedence (historical tag).

## [0.1.13]

Chore(release): bump version 0.1.12 -> 0.1.13 (historical tag).

## [0.1.12]

Chore(release): bump version 0.1.11 -> 0.1.12 (historical tag).

## [0.1.11]

Chore(release): bump version 0.1.10 -> 0.1.11 (historical tag).

## [0.1.10]

Chore(release): bump version 0.1.9 -> 0.1.10 (historical tag).

## [0.1.9]

Chore(release): bump version 0.1.8 -> 0.1.9 (historical tag).

## [0.1.8]

Docs(readme): fix pip install version pin and entrypoint claim (historical tag).

## [0.1.7]

Fix(telemetry): use dynamic version in dashboard header (historical tag).

## [0.1.6]

Feat(telemetry): user-friendly dashboard UI, intuitive header badges, and visual failover routing indicators (historical tag).

## [0.1.5]

Feat(telemetry): dynamic viewport height budgeting, guaranteed SUMMARY visibility, and fixed column alignment (historical tag).

## [0.1.4]

Feat(telemetry): pure Python statusline command, CC source_msg countdown parser, and adaptive SUMMARY layout (historical tag).

## [0.1.3]

Feat(telemetry): calibrated live telemetry collector to match P: ground truth (historical tag).

## [0.1.2]

Feat(diag): dynamic quota/context refresh, countdown engine, ESC exit, and responsive TUI (historical tag).

## [0.1.1]

Fix(broadcast): use PipeRunnerConfig to probe target peer versions (historical tag).

## [0.1.0]

Fix(cli): use dynamic workspace_home_id detection to prevent identity mismatch (historical tag).
