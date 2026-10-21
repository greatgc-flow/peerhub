# Changelog

All notable changes to PeerHub are recorded here, following
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
PeerHub remains on 0.x; breaking changes are called out explicitly.
Historical entries are reconstructed from Git tags, commit subjects and PR titles;
they do not claim new validation or publication evidence.

## [Unreleased]

### Added

- `peerhub batch run` validates flat task manifests, executes bounded parallel asks, and reports delivery status with cancellation and stable resume identities.

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
