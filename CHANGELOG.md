# Changelog

All notable changes to PeerHub are recorded here, following
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
PeerHub remains on 0.x; breaking changes are called out explicitly.
Historical entries are reconstructed from Git tags, commit subjects and PR titles;
they do not claim new validation or publication evidence.

## [Unreleased]

### Added

- **Bridge / Runtime — Process-tree termination confirmation (PR 1):** `ProcessResult`
  carries `termination_confirmed: bool | None` (`None` = not attempted; `True` = OS-level
  whole-tree termination confirmed; `False` = unconfirmed). Captured through `BoundedProcess`
  (`wait()`, `abort()`, `terminate()`), `CliRuntimeTarget`, and `ProcessRuntimeAdapter`.
  Durable bridge evidence records `termination_confirmed` in detail JSON for timeout,
  abort, and error paths (`runtime_timeout`, `no_terminal`, `runtime_error`, `control_applied`).
  Surviving child processes when a parent exits prematurely are explicitly tracked as
  unconfirmed (`False`). `ProcessRuntimeAdapter._kill_tree` no longer swallows taskkill exit code 128
  on dead parents as confirmed cancellation. Monotonic certainty states and existing schemas
  remain unchanged.

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
