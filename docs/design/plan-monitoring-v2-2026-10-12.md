Plan document, 2026-10-12. Reference, not an instruction file.

# PeerHub Monitoring Specification v2 (Future Release)

## Status and Superseded Statements

Session inspection companion: [Session monitoring pre-TDD specification](plan-session-monitoring-2026-10-13.md) (proposed session snapshot, alert and export additions; owner questions remain).

**2026-10-13:** PRs 1-7 remain pending. The original 0.16.0 allocation is
superseded: that candidate contains stuck-streams PRs 1-3, the model profile
refresh, and the CI trigger change. Monitoring's release version is unassigned;
the staged implementation below remains the design SSOT. The 0.15.2 dashboard
privacy fix does not complete PR-6's full monitor export contract.

**2026-10-14 CLI correction:** The [CLI/Monitoring Addendum](peerhub-cli-monitoring-inspection-2026-10-14.md)
is authoritative for command semantics and defaults below. In particular,
`diag --live` remains the read-only watcher; `monitor` remains the
collection-plus-dashboard workflow. The old proposals to remove `diag --live`
and add `monitor --read-only` are superseded. Other MV2 decisions remain
proposals unless that addendum or the renewal closes them.

Reference plan for unified cross-peer quota monitoring, pace balancing, and CLI interface consolidation.
Source of truth: `@W@\mon_spec_v2.md` (final decisions by cc, cx, ag).
This document explicitly supersedes the following statements from `plan-quota-forecast-2026-10-12.md`:
1. **NO_COLOR empty value**: Superseded. An empty `NO_COLOR=""` is ignored per no-color.org; only non-empty strings disable color. Nonzero `FORCE_COLOR` or `CLICOLOR_FORCE` overrides `CLICOLOR=0`.
2. **--collect-every**: Retained as the explicit collection-cost control. By default each monitor cycle collects; `--collect-every K` collects every Kth cycle. Cycle scheduling uses monotonic deadlines and skips missed slots.
3. **diag --live**: Retained. `diag --live` is the read-only watch over committed evidence; `monitor` composes collection with the same dashboard reader.
4. **Exit codes**: Superseded. Existing status codes are preserved (0 OK, 4 FAILED/PARTIAL, 5 UNAVAILABLE, 6 SCHEMA_VERSION; 2 usage; Ctrl-C 0). New ALERT code 1 is returned ONLY when diagnostic status is OK; JSON reports alerts independently.
5. **Forecast v2 stance**: Superseded and clarified. Recent-rate (1h) is not in the product runtime; it is restricted to offline `tools/quota_backtest.py` as a candidate policy pending promotion criteria.

## 1. Purpose and the 10-Second Answer
- **Purpose**: Provide foreground, transparent quota and rate-limit observability across collaborating peers (`cc`, `cx`, `ag`) without daemons, background webhooks, or hidden network traffic.
- **10-Second Answer**: A single-glance status displayed above tables or via `--line`:
  `PeerHub ALERT cx/X-5H exhausts 11:47 | 5h cc29 ag3P0/G20 cx96 | 7d cc84 ag3P74/G50 cx20 | pace7d cc+3.5 ag3P-0.4/G-22.8 cx+5.8 | balance: cc heavier than ag | lowest 7d pace: ag/G-7D (-22.8) | stale: none`
  Instantly communicates: who has headroom, which pool constrains them, whether weekly pace is balanced, upcoming resets/expiries, and evidence freshness.

## 2. Jobs-to-be-Done
1. **Headroom & Constraints**: Instantly answer who has headroom and which pool (5h vs 7d) constrains each peer. Headroom is observed availability, never a fit guarantee.
2. **Weekly Delegation Balance**: Determine if 7-day pace is balanced (balance follows both conditions in section 3) without guessing or biased recommendations.
3. **Threat Forecasting**: Identify whether current consumption pace will exhaust any pool before reset.
4. **Resets & Expiries**: Track approaching reset countdowns and usable cx reset-credits expiring within 72 hours.
5. **Evidence Freshness & Probe Health**: Verify evidence age (<300s fresh) and separate probe execution faults from stored pool capacity.

## 3. Pace Balance (R1)
- **Metric**: PACE = $\text{used}\% - \text{elapsed}\%$ per 7-day window (signed percentage points). Raw used% cannot be compared across staggered weekly resets; PACE measures deviation from even consumption.
- **Rule R1**:
  - `ag` has two 7d pools: `ag.3P` (3rd-party) and `ag.G` (Gemini). Both are shown.
  - Verdict is **OK** (balanced) iff:
    1. $cc.\text{pace} \le \min(ag.\text{3P}.\text{pace}, ag.\text{G}.\text{pace})$ (cc never outpaces either ag pool), AND
    2. $| \max(ag.\text{3P}.\text{pace}, ag.\text{G}.\text{pace}) - cx.\text{pace} | \le 5\text{ pt}$ (5 pt tolerance is explicit policy).
  - Otherwise report `'cc heavier than ag'` (if condition 1 fails) or `'ag and cx unbalanced'` (if condition 2 fails).
  - Requires fresh valid consistent anchors; inferred anchors labelled conditional; anything stale/unknown/expired => `'balance unknown'`.
  - Display all four 7d PACE values. Show only the factual `'lowest 7d pace: <peer/pool>'`; no `'prefer X'` recommendation.
- **Worked Example (Today's Real Data)**:
  - Inputs: `cc C-7D pace +3.5`, `ag 3P-7D pace -0.4`, `ag G-7D pace -22.8`, `cx X-7D pace +5.8`.
  - Condition 1: $\min(-0.4, -22.8) = -22.8$. $cc (+3.5) \le -22.8$ is **FALSE** (+3.5 > -22.8 by 26.3 pt; also +3.5 > -0.4).
  - Condition 2: $\max(-0.4, -22.8) = -0.4$. $| -0.4 - (+5.8) | = |-6.2| = 6.2\text{ pt} \le 5\text{ pt}$ is **FALSE** (6.2 pt > 5.0 pt policy tolerance).
  - **Verdict**: `'cc heavier than ag'` (and ag/cx unbalanced). $cc$ is pacing hotter than both ag pools, and ag/cx diverge by >5 pt. Factual tag: `lowest 7d pace: ag/G-7D (-22.8)`.

## 4. Forecast Stance and Backtest Promotion Criteria
- **Runtime Stance**: Single window-average forecast only. Projected usage at reset = $\text{used}\% / \text{elapsed fraction}$. Exhaustion ETA = $\text{start} + \text{elapsed} / \text{used fraction}$.
  - Support gates: Fresh valid evidence, consistent anchors, $\ge 5\text{ pt}$ used, elapsed $\ge 10\%$ (5h) or $\ge 20\%$ (7d).
  - Threat gate: Projected reset usage $\ge 105\%$. Below gates: `'not enough data'`, never `'on pace'`.
  - Overrides: Fresh measured 100% overrides gates (observed exhaustion). Stale/expired evidence suppresses forecasts. Labelled: `conditional window-average; accuracy not demonstrated`.
- **Promotion Criteria for Candidate Models** (e.g. 1h recent-rate in `tools/quota_backtest.py`):
  1. Tested offline on $\ge 30$ completed windows per pool/window.
  2. Dataset contains $\ge 10$ exhausted windows and $\ge 10$ quiet windows.
  3. Demonstrates increased recall without degrading precision (target $\ge 80\%$ precision/recall, median lead time $\ge 30\text{m}$ for 5h / $\ge 6\text{h}$ for 7d).
  4. Uncertainty intervals empirically calculated and published. Until met, candidate policies remain out of product.

## 5. Alerts
- **States**: `EXHAUSTED` (fresh 100% used), `THREAT` (projected $\ge 105\%$), `CREDIT_EXPIRY` (cx credit expiring $< 72\text{h}$), `PROBE_FAULT` (probe failure). PROBE_FAULT keeps the existing command-specific failure exit (4); it never produces ALERT=1.
- **Enter/Clear (Hysteresis)**:
  - Enter THREAT at projected $\ge 105\%$.
  - Clear THREAT when projected drops $< 100\%$ across **two distinct valid collections** (in-memory session state prevents flapping; one-shot commands evaluate entry only).
  - EXHAUSTED clears only upon fresh reset or recovery evidence.
  - Staleness or unknown evidence **never** clears an alert (transitions to `'unknown'`, never `'resolved'`).
  - Redraws may mark evidence stale or expired without clearing latched alerts; hysteresis advances only on distinct observations.
- **Deduplication**: 1 Hz redraw updates countdowns/freshness but never re-evaluates alerts. Transitions announced once per session in alerts zone without repeated noise.
- **What is NOT Built**: No daemon, no desktop notifications/popups, no webhooks/email, no audio bells, no alert DB, no traffic interception or auto-routing.

## 6. Collection
- **Cadence**: Monotonic schedule starting every 60s (`--interval-seconds 60`). Missed deadlines skipped; serial non-overlapping probes (never run concurrently).
- **Timeouts**: Per-provider timeout defaults to 30s (`--timeout-seconds 30`) to accommodate cc (7–30s).
- **Failure Backoff**: Exponential backoff on consecutive failures: 120s, 240s, capped at 900s. Success restores normal cadence.
- **Staleness**: Fresh $< 300\text{s}$; Stale $\ge 300\text{s}$; Expired = frame time $\ge$ reset; Unknown = missing/invalid. Latest probe failure is displayed separately from retained evidence.
- **Agy Token Guard**: `ag` collection validates zero model token consumption. On detected token consumption or unverifiable cost, suspend `ag` collection immediately (warn before and during); other peers continue. Flag `--allow-agy-token-use` overrides.

## 7. History and Privacy
- **History**: SQLite observations are immutable append-only records. No automatic pruning or downsampling tools built now. `diag health` reports raw store size only. Export via `monitor --json >> trace.ndjson`. Pruning deferred to future reviewed migration.
- **Privacy**: Strict allowlist schema. The allowlist applies to monitor exports; retained diagnostic JSON contracts are unchanged. Telemetry and exports contain zero prompts, zero completions/responses, zero raw provider CLI output, zero filesystem paths, and zero credit/account IDs.
- **Monitor Export Allowlist**: Defined before TDD as an explicit field list: `observed_at`, `cycle`, `pool id`, `peer`, `used_fraction`, `window start/end epochs`, `state`, `projection state/pace_pt/projected_at_reset/exhaustion_at`, `probe outcome code`, `alert ids`.

## 8. Interfaces

### Command and Flag Changes
| Command / Flag | Status | Behavior / Notes |
|---|---|---|
| `peerhub observation refresh` | Retained | Mutating collection only; outputs brief execution summary. |
| `peerhub diag` | Retained | Read-only snapshot of committed evidence; views `plain`, `rich`, `--json`. |
| `peerhub diag health` | Retained | Stream and store health inspection unchanged. |
| `peerhub diag quota` | Retained | Quota inspection table / JSON unchanged; `--peer` and `--pool` filters. |
| `peerhub diag --line` | **New** | Emits single-line status for prompts/AI status lines; exit codes below. |
| `peerhub diag --live` | Retained | Read-only watch from committed evidence; it invokes no provider probes. |
| `peerhub diag --cycles`, `--interval-seconds` | Retained | Bound and schedule the read-only watch. |
| `peerhub monitor` | Retained | Foreground collect + watch loop; defaults: interval 60s, timeout 30s. |
| `peerhub monitor --read-only` | **Dropped** | Duplicates the existing read-only `diag --live` surface. |
| `peerhub monitor --collect-every` | Retained | Defaults to 1 (collect every cycle); increase K to reduce provider polling. |
| `peerhub monitor --once` | **Removed** | Superseded by `peerhub diag`. |
| `peerhub monitor --json` | Retained | Emits NDJSON using documented allowlist (no full diagnostics payload). |
| `peerhub monitor --peers`, `--cycles`, `--view`, `--system-dir`, `--allow-agy-token-use` | Retained | Existing monitor execution and configuration options preserved. |

### Exit Codes
| Code | Name | Condition / Semantics |
|---|---|---|
| `0` | OK | Diagnostic status clean; no active alerts. Live runs exit 0 on Ctrl-C. |
| `1` | ALERT | Active threat ($\ge 105\%$), exhaustion, or credit expiry $<72\text{h}$; returned **ONLY when status is OK**. Never masks failures; PROBE_FAULT keeps the existing command-specific failure exit (4) and never produces ALERT=1. |
| `2` | USAGE | CLI argument or flag validation error. |
| `4` | FAILED / PARTIAL | Storage fault, unreadable store, probe execution failure (`PROBE_FAULT`), or partial evidence. PROBE_FAULT keeps the existing command-specific failure exit (4); it never produces ALERT=1. |
| `5` | UNAVAILABLE | Database not found / missing store path. |
| `6` | SCHEMA_VERSION | Database schema version mismatch. |

### The `--line` Format
ASCII output, no length cap. ag values are labelled 3P and G in `--line`, and the line carries 'lowest 7d pace: <peer/pool>' as defined in section 3. Contains status word, first alert, 5h used%, 7d used%, 7d pace, balance verdict, lowest 7d pace, and stale list.
**Format**: `PeerHub <STATUS> <FIRST_ALERT> | 5h <USED> | 7d <USED> | pace7d <PACE> | balance: <VERDICT> | lowest 7d pace: <peer/pool> | stale: <STALE_LIST>`
**Example**:
`PeerHub ALERT cx/X-5H exhausts 11:47 | 5h cc29 ag3P0/G20 cx96 | 7d cc84 ag3P74/G50 cx20 | pace7d cc+3.5 ag3P-0.4/G-22.8 cx+5.8 | balance: cc heavier than ag | lowest 7d pace: ag/G-7D (-22.8) | stale: none`

## 9. Standards
- **NO_COLOR**: Respects no-color.org. Empty `NO_COLOR=""` is ignored. Non-empty value suppresses colors.
- **FORCE_COLOR**: Nonzero `FORCE_COLOR` or `CLICOLOR_FORCE` overrides `CLICOLOR=0`. Color requires capable TTY when unset.
- **Escape-free**: Plain text contains no styling escapes; cursor controls are restricted to interactive VT watch output. Non-interactive output, JSON and --line contain no escapes.
- **Windows VT**: Enabled via `ctypes` (`0x0004`). If unavailable, falls back to append mode. Cursor (`\x1b[?25h`) is always restored on exit.
- **Resize**: Clears screen (`\x1b[2J\x1b[H`); the layout adapts to the terminal width; no additional breakpoints are specified; snapshot tests sample 80, 100 and 120 columns as test widths only.
- **Accessibility**: State is never communicated by color alone; paired with explicit words and text glyphs (`! exhausts`, `= on pace`, `: not enough data`).

## 10. Removals List
1. `--collect-every` flag and step calculations: `peerhub/cli/monitor.py`, `docs/implementation/command_inventory.json`.
2. `diag --live`, `--cycles`, and `--interval-seconds` from `diag`: `peerhub/cli/app.py`, `peerhub/extensions/diag_watch.py`.
3. `monitor --once` flag: `peerhub/cli/monitor.py`.
4. Recent-rate / 1h burst projection remnants: `peerhub/extensions/quota_projection.py`, `peerhub/extensions/diag_quota.py`.
5. Empty `NO_COLOR` check treating empty string as active: `peerhub/cli/view.py`.
6. Literal ` [H [2J` string artifacts: `peerhub/cli/monitor.py`, `peerhub/cli/view.py`.
7. Full diagnostic payload in monitor JSON (replace with allowlist): `peerhub/cli/monitor.py`, `peerhub/cli/view.py`.
8. Duplicate `human_duration` logic: `peerhub/extensions/diag_quota.py` vs `peerhub/cli/view.py`.
9. Hard-coded absolute paths in test fixtures: `tests/communication/e2e/test_cli_view_snapshots.py`.

## 11. Acceptance Criteria and Test Outline
- **Categories**:
  1. *PACE & Balance Math*: Evaluate $cc \le \min(ag)$ and $|\max(ag) - cx| \le 5\text{ pt}$ with dual ag pools and edge tolerances.
  2. *Gating & Projections*: Assert 10%/20% elapsed gates, 5 pt used gate, 105% threat gate, and 100% observed exhaustion override.
  3. *Alert Hysteresis & Dedup*: Assert THREAT enters at $\ge 105\%$, clears only on 2 distinct collections $< 100\%$, and never clears on staleness.
  4. *Interfaces & Exit Precedence*: Test exit codes (5 > 6 > 4 > 1 (only if OK) > 0); verify `--line` and `--read-only`.
  5. *Standards*: NO_COLOR matrix (empty vs non-empty), FORCE_COLOR, Windows VT fallback, and escape-free plain output.
- **Fixtures**: Synthetic SQLite databases for idle windows, 1% jitter, moving anchors, expiring credits, and clock skew.
- **Regression Coverage**: Full suite coverage for retained interfaces (`observation refresh`, `diag health`, `diag quota`, global `--db`).
- **Live Checks**: Opt-in (`PEERHUB_LIVE=1`). Strict zero-token ceiling for `agy` (`ag` model tokens == 0). Missing vendor credentials must report `SKIPPED_PREREQ` (never false PASS).

## 12. Non-Goals and Risks
- **Non-Goals**: No daemons/services, no notification popups/webhooks/bells, no automated traffic steering, no ML forecasting, no rich/textual TUI dependencies, no auto-pruning.
- **Risks**: Provider CLI output drift, unintentional agy token expenditure (guarded), quantization slope jitter in short windows, unbounded long-term SQLite growth without pruning.

## 13. Implementation Steps (Release Version Pending)
A future release must document the breaking CLI interface and exit-code changes.
Delivered in ordered, independently releasable PRs: every PR includes its command-inventory/docs/schema updates and the regression tests for retained interfaces (diag, diag health, diag quota, observation refresh).

1. **PR-1: NO_COLOR/FORCE_COLOR standards + ESC/escape rules**: Standards compliance, terminal capability detection, empty `NO_COLOR` ignore, `FORCE_COLOR`/`CLICOLOR_FORCE` overrides, cursor controls restricted to interactive VT watch, and escape-free plain, non-interactive, JSON, and `--line` output.
   - Files: `peerhub/cli/view.py`, `tests/communication/e2e/test_cli_view.py`.
2. **PR-2: Shared snapshot/policy reader + alert state machine (enter/clear/dedup, distinct-observation hysteresis) + exit-code table**: Alert state machine (enter at $\ge 105\%$, clear below 100% across two distinct collections, distinct-observation hysteresis, staleness preservation), exit codes (0 OK, 1 ALERT only when status is OK, 2 USAGE, 4 FAILED/PARTIAL, 5 UNAVAILABLE, 6 SCHEMA_VERSION; PROBE_FAULT keeps exit 4 and never produces ALERT=1).
   - Files: `peerhub/extensions/diag_quota.py`, `peerhub/cli/app.py`, `peerhub/extensions/quota_projection.py`, `tests/communication/e2e/test_cli_diag_quota.py`.
3. **PR-3: Monotonic cadence/backoff/timeouts/staleness and THEN removal of --collect-every**: 60s monotonic interval schedule (`--interval-seconds 60`), 30s timeouts, exponential backoff (120s/240s/900s), staleness thresholds (<300s fresh, $\ge 300$s stale), agy zero-token suspension guard, and then removal of `--collect-every`.
   - Files: `peerhub/cli/monitor.py`, `peerhub/extensions/quota_capture.py`, `tests/communication/observation/test_quota_probes.py`, `tests/communication/e2e/test_cli_monitor.py`.
4. **PR-4: Pace-balance line and diag --line**: Implement R1 dual-pool balance engine ($cc.\text{pace} \le \min(ag.\text{3P}, ag.\text{G})$ and $|\max(ag) - cx| \le 5\text{ pt}$), factual `'lowest 7d pace: <peer/pool>'`, ag values labelled 3P and G in `--line`, and `peerhub diag --line`.
   - Files: `peerhub/extensions/quota_projection.py`, `peerhub/cli/app.py`, `peerhub/cli/view.py`, `tests/communication/observation/test_quota_projection.py`, `tests/communication/e2e/test_cli_diag_quota.py`.
5. **PR-5: Shared snapshot rendering and monitor truth (separate from --line)**: Keep `diag --live`; share snapshot rendering with `monitor`; render the committed snapshot before refresh; report refresh and diagnostic outcomes separately. Keep `--collect-every` and document that `monitor` collects provider evidence.
   - Files: `peerhub/cli/monitor.py`, `peerhub/cli/app.py`, `peerhub/extensions/diag_watch.py`, `tests/communication/e2e/test_cli_monitor.py`.
6. **PR-6: Monitor JSON allowlist/export sanitization with tests**: Monitor JSON allowlist export filtering (`observed_at`, `cycle`, pool id, peer, `used_fraction`, window start/end epochs, state, projection state/pace_pt/projected_at_reset/exhaustion_at, probe outcome code, alert ids), preserve retained diagnostic JSON contracts unchanged, and export sanitization tests.
   - Files: `peerhub/cli/monitor.py`, `peerhub/cli/view.py`, `tests/communication/e2e/test_cli_monitor.py`.
7. **PR-7: Docs/CHANGELOG/release**: Documentation updates, root `CHANGELOG.md`, `README.md`, package version bump to the assigned monitoring release in `peerhub/_version.py`.
   - Files: `CHANGELOG.md`, `README.md`, `peerhub/_version.py`, `docs/implementation/command_inventory.json`.

Each independently releasable.
