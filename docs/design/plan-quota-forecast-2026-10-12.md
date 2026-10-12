Plan document, 2026-10-12. Reference, not an instruction file.

Update, 2026-10-12: added the merged monitor A2 screen specification from
`screen_cx.out` and `screen_ag.out`, using the owner's final cc decisions.
Consistency edits below retain numeric PACE below forecast gates, permit
consistent inferred anchors with explicit provenance, align forecast wording
and terminal refresh policy, and correct the associated acceptance/risk text.

# PeerHub PR-A: honest quota forecasts and release 0.15.0

## Status, placement and evidence

This records the owner's resolved v2 scope; implementation and release are pending.
Base: `main` at `00be05720ec3a21e4a2105bb1b54a0a5d818f2e3`;
documentation branch: `docs/plan-quota-forecast`.
`docs/README.md`, requested as the taxonomy, is absent at this base.
Placement follows the existing design/decision index, `docs/design/README.md`;
this reference plan does not acquire that index's ratified-design authority.
Maintainer and release guidance remains in `CONTRIBUTING.md`.

Evidence source directory supplied by the owner:
`D:\PkgDev\_sys\data\temp\claude\D--PkgDev-workspace\4041a42e-f452-4f0d-9a0f-fc49b63dfd31\scratchpad`.
Read sources: `plan_v2.md`, `q_peerhub.out`, `r2_cx.out`, `r2_ag.out`,
`q_engram.out`, `q_sb.out`. The last two establish repository boundaries only.
The final v2 amendments supersede conflicting round proposals, including
hysteresis, a forecast flag, exit 2 and release 0.14.1.
These local review files are provenance, not portable repository dependencies.

## Problem and the owner's evidence

The owner reported an implausible ETA beside a small green PACE, confusing
dashboard redraws and store behaviour, and conflicting documentation.
`q_peerhub.out` traces those observations through reviews of #80 and #83-#86:

- F1-F3: collectors infer start as reset minus duration without demonstrating
  provider window semantics. A 60-second gate extrapolates quantised usage;
  early exhaustion can be suppressed and forecasts can survive reset.
- F4-F5: PACE uses read time while ETA uses measurement time, with independent
  rounding/colour. The review replay reports 1% at measurement +79 seconds,
  read +120 seconds: approximately 2h09m ETA beside rounded 0pt PACE.
  Dates are omitted, probe failures look like pools, and only one threat is named.
- F6-F8: recent-rate claims lack production samples; monitor prints literal
  ` [H [2J` and can create a missing store while diagnostics avoid creation.
- F9-F11: formula/string tests do not establish forecast usefulness; the review
  says the supplied 49 rows are insufficient to establish accuracy. STATUS,
  CONTRIBUTING and HANDOFF disagree or duplicate claims and release guidance.

These are reported observations and code-review findings, not newly measured
accuracy evidence. GitHub release notes were inaccessible during that review.
The checkout still contains the recent-rate claim in `docs/STATUS.md` and
the literal redraw string in `peerhub/cli/monitor.py`.

## Resolved decisions relevant to PeerHub

| Decision | PeerHub disposition |
| --- | --- |
| D1 | Link-count guard changes belong to SecondBrain; no PeerHub change. |
| D2 | Preserve evidence honesty: a waiver or simulation never counts as PASS. No PeerHub waiver mechanism is added. |
| D3 | The ignore-rule change belongs to SecondBrain. PeerHub has no `history/` rule to replace; audit tracked root archives without inventing a change. |
| D4 | One engine and timestamp policy; fresh evidence and consistent anchors, including labelled inferred anchors; forecast support requires elapsed >=10% AND used >=5 percentage points; threat only at projected reset usage >=105%. Below support gates: `not enough data`, never `on pace`. PACE is independent of these gates. |
| D5 | Missing selected store: exit 5, `database not found: <path>`, no creation and no monitor probes. |
| D9 | Create root `CHANGELOG.md` in implementation PR-A; its selected version section is the SSOT for the GitHub Release body. PeerHub has no winget package. |
| D10 | Release 0.15.0, reflecting breaking projection JSON and exit behaviour; no legacy aliases or duplicate compatibility fields. |

Fresh measured exhaustion precedes support thresholds. Stale/expired evidence
and unknown or invalid anchors suppress forecasts; inference alone does not.
ETA remains inline and is
labelled `conditional, window-average`; predictive accuracy is not demonstrated.
No new third-party dependency in PR-A; minimal Windows VT enablement uses ctypes.

## Implementation PR-A file and behaviour scope

All paths below are future PR-A work, not changes made by this documentation task.

| Files | Resulting responsibility |
| --- | --- |
| `peerhub/extensions/quota_projection.py` | `project_exhaustion` is the sole derivation engine; remove recent-rate/hybrid logic. |
| `peerhub/extensions/quota_probes.py` | Record anchor basis; validate each provider's window assumptions through opt-in traces. Inference does not establish validated semantics or alone suppress a conditional forecast. |
| `peerhub/extensions/diag_quota.py`, `peerhub/extensions/diag.py` | Consume the shared projection, remove duplicate derivation/JSON fields, preserve read-only snapshots. |
| `peerhub/cli/view.py`, `peerhub/cli/monitor.py` | Shared PACE/ETA state, inline forecasts, all-threat header, separate probe health, capability-safe redraw and truthful cadence help. |
| `peerhub/cli/app.py`, `peerhub/cli/store_select.py` | Consistent missing-store exit before collection; preserve explicit/env/discovered selection and prevent fallback to a different store. |
| `tests/communication/observation/test_quota_projection.py`, `test_quota_probes.py` | Engine boundaries and provider anchor validity. |
| `tests/communication/e2e/test_cli_view.py`, `test_cli_monitor.py`, `test_cli_diag_quota.py` | Presentation, JSON parity, missing stores, terminal capability and collection cadence. |
| `tests/communication/diag/test_diag_watch.py`, `test_dia_runtime_readonly.py` | Read-only watching and frame freshness/reset transitions. |
| `README.md`, `docs/STATUS.md`, `CONTRIBUTING.md`, `docs/HANDOFF.md` | Forecast limits, breaking output, cadence, trace collection, consistent suite timing/xdist advice and canonical release links. |
| `CHANGELOG.md` (new; not created by this task), `peerhub/_version.py` | Selected 0.15.0 notes and package version; release body copied from that version section. |

Test filenames after the first path in each test row share that row's directory.
No new extension module is planned. Any later addition requires both extension
exit-gate and architecture-graph registration under the repository rules.
No frozen M1 specification edits are planned; any separately necessary edit
would require regenerating and including its seal.

### Projection contract and exact visible output

Each pool has one projection object with `pace_pt`, `projected_at_reset`,
`state`, `exhaustion_at`, `measured_at`, `anchor_basis`, `reason`.
States: `unknown`, `stale`, `expired`, `exhausted`, `insufficient`, `on_pace`,
`threat`. Unavailable numeric/forecast fields are null; `reason` explains suppression.
Old projection fields are removed rather than retained alongside replacements.

For used fraction u and measurement-time elapsed fraction e:
`pace_pt = 100*u - 100*e`; `projected_at_reset = 100*u/e`.
When supported, window-average exhaustion is start plus elapsed seconds / u.
Reject invalid/nonfinite measurements and impossible window ordering.
Freshness and reset expiry use one frame timestamp; PACE/rate use measurement
time, so idle redraws move countdowns without inventing new usage measurements.
Fresh measured exhaustion is shown even below the 10%/5pt support gates;
expired/stale data cannot be presented as current exhaustion or a current ETA.

Exact text examples (values depend on the shared projection):

```text
PACE +2.3pt  on pace
PACE +0.3pt  : not enough data
PACE +12.0pt  ! exhausts Tue 10-13 03:11 in 1d18h (cond)
exhausted
unknown
stale
expired
Threats: 2; earliest: cc/weekly Tue 10-13 03:11 +0900; pools: cc/weekly, cx/weekly
Probe health:
database not found: <path>
```

PACE uses signed one-decimal percentage points and the projection's state for
colour; insufficient/unknown/stale/expired states show no numeric forecast.
PACE is shown whenever `resets_at > measured_at` and
`resets_at - duration <= measured_at`, independently of forecast support gates.
Threat times include date when over 24 hours away or crossing local midnight,
and a timezone offset. Probe failures appear under `Probe health:` with reasons,
never as fabricated quota-pool rows. The threat header lists every threatened
pool, its count and the earliest ETA; the inline label applies to every ETA.
Missing-store plain output is one stderr line with the selected path substituted,
exit 5; JSON mode preserves an unavailable/error envelope carrying that message.

TTYs with VT enabled use cursor-home overwrite; redirected or unsupported output neither
clears nor prints raw control sequences or the literal ` [H [2J` artifact.
Cycle interval defaults to 60 seconds; collect on the first cycle and every Kth
cycle (`--collect-every K`). Terminal countdown redraw is at most once/second
from the same snapshot, with no extra probes; plain/NDJSON emits once/cycle.
Help distinguishes cycle, collection and redraw cadence.

Opt-in trace collection: `peerhub monitor --json >> trace.ndjson`.
Bound with `--cycles N`; sanitize before sharing and apply a size/retention limit.
Retain only needed usage, anchor, freshness, projection/version and outcome data.
This uses existing NDJSON, not a new history service or heavy backtest harness.

## Screen specification (monitor, A2)

The owner's final cc merge supersedes conflicting details in the independent
`screen_cx.out` and `screen_ag.out` designs in the evidence directory above.
This is an implementation specification, not a claim of implemented behaviour
or measured forecast accuracy.

### Zones and ownership

Zones are MECE: each datum has one owning zone. B contains alert references to
C/D decisions, without repeating their measurement columns. A owns collection
scheduling; F owns probe outcomes. Do not repeat those data in G.

| Zone | Contents |
| --- | --- |
| A Header | One line: screen name, date/time and offset, selected store path and selection source, cycle number, next collection countdown. |
| B Alerts | All threatened pool identifiers, count and earliest exhaustion; exhausted pool identifiers; credits expiring in less than 72 hours. No probe errors. |
| C Quota | One row per real pool; columns `PEER POOL WIN USAGE-bar USED PACE RESET FORECAST FRESH`. Forecast stays inline; no detached ETA rows. |
| D Credits | Available credit count and nearest expiry, absolute timestamp and countdown, with evidence freshness. Credits never become quota percentages. |
| E Asks | One line per peer with running and recent OK/uncertain/failed counts; omit when there is no activity. State the recent interval and count distinct asks from committed evidence. |
| F Collector/probe health | One line, per-peer `OK`, `ERR(reason)` or `TIMEOUT`. Probe failures never create pseudo-pool rows or erase retained pool observations. |
| G Legend/controls | One line: forecasts are conditional window-average estimates; state/freshness key as space permits; Ctrl-C quits. |

C order is stable: peer alphabetical, then window short to long (5H before 7D),
then pool identifier as tie-breaker. Risk appears in glyph/colour and B, never
by reordering rows. Thus ag's two 5H pools precede its two 7D pools.

### Decisions and visual grammar

PACE is signed, one-decimal `used% - elapsed%`, calculated at `measured_at`.
Show it whenever the anchor is consistent: reset is in the future relative to
measurement, and reset minus duration is no later than measurement. An invalid
anchor yields `--`; forecast gates do not hide otherwise computable PACE.
Elapsed support is at least 10% of the window and used support is at least 5
percentage points. Only forecast text uses those gates and the 105% projected
reset threshold. Compare unrounded values; rounding is presentation only.

Fresh measured exhaustion overrides support gates. Otherwise stale, expired,
unknown or invalid evidence is shown as such. Below either support gate show
`: not enough data`, never `on pace`. With support and projected reset usage
below 105%, show `= on pace`; at or above 105%, show a conditional exhaustion
ETA, for example `! exhausts Tue 10-13 03:11 in 1d18h (cond)`.
Show the date whenever ETA is more than 24 hours away or falls on another local
calendar day; A supplies the timezone offset used by all displayed times.

Inferred reset-minus-duration anchors remain eligible when consistent, with
`anchor_basis` identifying inference in JSON. They are not validated provider
window semantics. Bounded opt-in trace collection described above must record
that unvalidated status; it has not established predictive accuracy. Every
forecast is a conditional window-average estimate, not a recent-rate estimate.

Bars have 10 cells at 100+ columns and 8 at 80 columns: Unicode block glyphs
`█`/`░`, ASCII fallback `#`/`.`. Round filled cells to the nearest cell;
numeric USED remains authoritative. At most four colours: default,
cyan for on-pace/chrome, yellow for stale/warnings, red for
threat/exhaustion/error. Never rely on colour alone: `? unknown`, `~ stale`,
`/ expired`, `x exhausted`, `: not enough data`, `= on pace`, `! exhausts`.
PACE has no independent risk colour. Freshness is dot plus age (`●2s` or ASCII
`*2s` for fresh, `○45m`/`o45m` for stale, `?--` for unavailable age).
Respect presence of `NO_COLOR`, including an empty value; suppress colours
while retaining glyphs/words and supported TTY cursor controls.

### Arithmetic and ASCII mock-ups

Use the reset countdowns literally from the ag paste: `1d18m` means 1 day
18 minutes, not 1 day 18 hours. These inputs contradict the designs' illustrated
threats and PACE values; the table below recalculates them. G-5H usage and cx's
credit values follow the owner's final real-data correction. Measurement ages,
ask counts and historical collection counters were not supplied: do not invent
them. The mock-ups use unknown ages, omit E, and use illustrative cycle 1,
next collection 60s and explicit store selection. ag/cx OK are fixture probe
outcomes; the supplied cc result is `ERR(claude_usage_timeout)`. Retained cc
measurements can coexist with this latest probe error.

For arithmetic, treat each reset countdown as remaining at measurement time:
`elapsed% = 100 * (duration - remaining) / duration`,
`PACE = used% - elapsed%`, `projected% = used% / (elapsed% / 100)`.
Use the common fixture measurement/frame time `2026-10-12T08:52:46+0900`
(`2026-10-11T23:52:46Z`), obtained by subtracting the supplied credit countdown
17d19h from `2026-10-29T18:52:46Z`. This aligns that countdown and expiry;
it is an arithmetic fixture, not a recovered provider measurement timestamp.
Assume fresh quota evidence and consistent inferred anchors for this fixture;
unknown ages here mean age metadata was not provided, not unknown usage.

| Pool | Used | Reset remaining | Elapsed % | PACE pt | Projected reset % | Forecast |
| --- | ---: | --- | ---: | ---: | ---: | --- |
| ag/3P-5H | 0% | 4h59m | 0.3333 | -0.3 | 0.00 | not enough data |
| ag/G-5H | 0% | 4h59m | 0.3333 | -0.3 | 0.00 | not enough data |
| ag/3P-7D | 74% | 1d18m | 85.5357 | -11.5 | 86.51 | on pace |
| ag/G-7D | 46% | 3d20h | 45.2381 | +0.8 | 101.68 | on pace |
| cc/C-5H | 5% | 3h55m | 21.6667 | -16.7 | 23.08 | on pace |
| cc/C-7D | 80% | 1d08h | 80.9524 | -1.0 | 98.82 | on pace |
| cx/X-5H | 1% | 4h58m | 0.6667 | +0.3 | 150.00 | not enough data |
| cx/X-7D | 4% | 6d16h | 4.7619 | -0.8 | 84.00 | not enough data |

No supported row reaches 105%; cx/X-5H's mathematical extrapolation fails both
support gates, so no ETA or threat is displayed. ag/G-7D's +0.8pt PACE does
not itself establish a threat. Credit expiry exceeds 72h and creates no alert.

100 columns (ASCII fallback; PACE values are percentage points):

```text
PeerHub 2026-10-12 08:52:46+0900 ...\peerhub\.peerhub\core.db(explicit) c1 next60s
B Threats:0; earliest:--; exhausted:none; credit expiry<72h:none
PEER POOL  WIN USAGE-bar    USED   PACE RESET  FORECAST             FRESH
ag   3P-5H 5H  [..........]   0%   -0.3 4h59m  : not enough data    ?--
ag   G-5H  5H  [..........]   0%   -0.3 4h59m  : not enough data    ?--
ag   3P-7D 7D  [#######...]  74%  -11.5 1d18m  = on pace            ?--
ag   G-7D  7D  [#####.....]  46%   +0.8 3d20h  = on pace            ?--
cc   C-5H  5H  [#.........]   5%  -16.7 3h55m  = on pace            ?--
cc   C-7D  7D  [########..]  80%   -1.0 1d08h  = on pace            ?--
cx   X-5H  5H  [..........]   1%   +0.3 4h58m  : not enough data    ?--
cx   X-7D  7D  [..........]   4%   -0.8 6d16h  : not enough data    ?--
D Credits cx:2 available; expiry 2026-10-29T18:52:46Z in 17d19h; fresh ?--
F Probes ag:OK cc:ERR(claude_usage_timeout) cx:OK
G Forecasts: conditional window-average estimates; ?-- age unknown; Ctrl-C quit
```

80 columns (drop WIN, retain the window suffix in POOL, use eight bar cells;
shorten forecasts, e.g. `! exh Tue 10-13 03:11 +1d18h(c)`):

```text
PeerHub 2026-10-12 08:52+0900 ...\core.db(explicit) c1 next60s
B Threats:0; earliest:--; exhausted:none; credit expiry<72h:none
PEER POOL  USAGE-bar  USED   PACE RESET  FORECAST             FRESH
ag   3P-5H [........]   0%   -0.3 4h59m  : not enough data    ?--
ag   G-5H  [........]   0%   -0.3 4h59m  : not enough data    ?--
ag   3P-7D [######..]  74%  -11.5 1d18m  = on pace            ?--
ag   G-7D  [####....]  46%   +0.8 3d20h  = on pace            ?--
cc   C-5H  [........]   5%  -16.7 3h55m  = on pace            ?--
cc   C-7D  [######..]  80%   -1.0 1d08h  = on pace            ?--
cx   X-5H  [........]   1%   +0.3 4h58m  : not enough data    ?--
cx   X-7D  [........]   4%   -0.8 6d16h  : not enough data    ?--
D Credits cx:2 available; expiry 2026-10-29T18:52:46Z in 17d19h; fresh ?--
F Probes ag:OK cc:ERR(claude_usage_timeout) cx:OK
G Forecasts: conditional window-average estimates; ?-- age unknown; Ctrl-C quit
```

Both examples have 14 lines. At 120 columns this eight-pool fixture, including
up to three active-peer E lines, must fit within 20 lines. No rendered line
may exceed terminal visible width. A abbreviates a long path with `...`;
the full selected path is `D:\PkgDev\workspace\peerhub\.peerhub\core.db`
and remains available in JSON. Re-layout on resize, shorten optional wording
before essential decisions, and wrap B when needed so every threat is retained.
The 20-line fixture target must not silently drop real pools, alerts or asks
from larger inventories. At 80 columns preserve dated ETAs when required;
`(c)` abbreviates conditional, as defined by G.

### Refresh and output parity

Use VT cursor-home overwrite only when stdout is a TTY and VT is enabled.
On Windows enable VT through ctypes console mode calls; add no dependency.
Hide the cursor during interactive rendering and always restore it in finally,
including Ctrl-C and rendering errors. Erase leftover cells/lines after a
shrinking frame; re-layout when terminal size changes. Countdown redraws are
at most 1 Hz, with PACE and ETA frozen at measurement time. Freshness/expiry
can change at frame time and suppress an obsolete forecast.

Non-TTY or no-VT output appends one frame per collection cycle, with zero
escape sequences and no countdown frames. Restore the actual ESC byte only
in the supported VT path: its loss in commit `0c16ea4d` caused literal
` [H [2J` text. Plain emits the same decisions in ASCII; `--json` emits NDJSON
once per cycle, with the shared seven projection fields and zone data,
including full paths, timestamps, credits, asks and probe health. Output cadence
does not alter `--collect-every K`: collect initially, then on every Kth cycle;
countdown redraws never collect or emit extra NDJSON objects.

### Automated acceptance checks

- Snapshot 80/100/120-column layouts, measure visible width (excluding VT
  controls), assert width bounds and the 120-column fixture's <=20-line height.
  Cover long paths, all-threat alerts, exhausted pools and credit expiry <72h.
- Assert zero ESC bytes, cursor controls and literal ` [H [2J` artifacts off-TTY
  and with VT unavailable; exactly one appended frame/NDJSON object per cycle.
- Test `NO_COLOR` both nonempty and empty, preserving state glyphs and words.
- Test just below, exactly at and just above 10% elapsed, 5pt used and 105%
  projected usage. Numeric PACE persists below gates; 105% becomes threat only
  with support. The owner's 1% used / 79s case yields `not enough data`.
- Verify consistent inferred anchors remain eligible and labelled; invalid
  anchors, stale/expired/unknown evidence and fresh early exhaustion follow
  their precedence. Check dates across midnight and beyond 24h.
- Assert JSON/plain parity for state, reason, PACE, ETA and zone data; idle
  redraws preserve measurement-derived values and cause no extra collection.
- Simulate resize/shrink and normal/error/Ctrl-C exits; assert erased leftovers,
  cursor restoration and exact collection counts for bounded cycles and K.

## Tests and acceptance criteria

- Boundary fixtures cover just below/at 10% elapsed, 5pt used and 105% projected;
  quantised 1%, burst, idle, zero usage, reset crossing, invalid and consistent inferred anchors,
  stale data, and fresh exhaustion with very small elapsed time.
- Deterministic fixture traces establish policy and output consistency, not
  predictive accuracy. Every view/JSON path matches engine fields at a fixed frame
  timestamp; date/timezone, all threats and separate probe health are asserted.
- Missing selected stores leave database/parent paths absent, return 5 and emit
  the specified message; monitor calls no provider. Explicit paths never redirect.
- VT/unsupported/redirected cases and fixed collection counts are tested without
  providers; idle redraws never change measurement-derived PACE or create records.
- Run targeted tests, `python -m pytest -q`, `pyright` and the existing inventory/
  traceability checks. Serial CI/release gates remain canonical; optional xdist
  advice and timing statements agree across docs and distinguish measured context.
- No recent-rate accuracy claim, duplicate engine, duplicate compatibility JSON,
  new dependency or unsubstantiated PASS remains. 0.15.0 version, STATUS and
  CHANGELOG agree; release notes match the selected version section exactly.
- Provider tests require explicit opt-in. Candidate-bound G3 and package/matrix
  evidence follow CONTRIBUTING; missing/skipped catalog tests cannot satisfy PASS.

## Order and PeerHub Phase 2 audits

1. Documentation first: this plan and one index entry; no implementation/release.
2. Before PR merge, P2-6 commit-history audit plus targeted SSOT/test-regression
   review: inspect removed/changed tests, docs and options by release, especially
   #80/#83-#86; disposition every suspected loss as restore, intentional or follow-up.
3. Implement and review PR-A, validate it, then merge with all required CI green.
4. Complete remaining PeerHub Phase 2 audits. Recheck P2-6 against merged PR-A.
   Release requires closed blocking findings and the existing release gates.

Each audit records owner, revision, command/evidence, findings, disposition and
PASS/FAIL closure; no blanket approval or evidence-free completion.

| Audit | Owner | Evidence and measurable closure gate |
| --- | --- | --- |
| P2-1 feedback loop | Maintainer | Inventory issue templates, changelog, lesson/error paths; trace one representative error through issue, fix and regression check; all broken links/closure gaps resolved or explicitly held. |
| P2-2 SSOT/duplication/hardcoding | PR-A implementer, maintainer review | Inventory repeated formulas, literals, configuration and custom helpers; one projection engine; every duplicate and no-code candidate has a documented disposition and no unresolved blocking duplicate. |
| P2-3 learnability/folder MECE/rename | Documentation maintainer | Set a numeric quickstart token budget before measurement; record tokenizer/count; all quickstart tasks succeed within it. Every current doc has one primary category. Renamed-root CLI and deterministic-test smoke passes in an isolated copy. |
| P2-4 test-suite MECE/live | Test maintainer; live operator | Marker/catalog inventory has no unclassified or accidentally default-live test; gap list disposition complete. Run live suites only with opt-in/prerequisites; unavailable credentials are `SKIPPED_PREREQ`, never PASS; blocking live gaps hold release. |
| P2-5 platform warnings | Windows/platform maintainer | Verify existing path/Node.js limitations against reproducible evidence; document missing warnings and tests; all known limits have a linked warning/disposition. Warn without attempting to overcome platform limits. |
| P2-6 history | Maintainer | Release-by-release deletion/change ledger with commit references and regression evidence; every removed test/doc/option disposition reviewed; no unexplained loss before merge. |

Audit evidence belongs in repository review documentation or authorized CI
artifacts; later smoke copies/traces are audit work, not files created here.

## Non-goals, risks and rollback

Non-goals: Engram/SecondBrain changes; new store-init command; admission/routing
quota enforcement; ambient polling; hysteresis; heavy backtesting; new forecast
flag; rich/packaging/filelock adoption; legacy compatibility; winget publication.
Phase 3 research/dependency choices remain owner decisions in separate work.

Risks: inferred anchors may remain unvalidated, making forecasts conditional;
quantisation and bursty use can still produce poor window averages. Thresholds
are policy, not calibrated accuracy guarantees. JSON/exit changes break consumers;
0.15.0 notes must explain migration. Terminal capability detection and store
existence races require targeted tests. Trace output can expose local metadata
and consume quota, so collection remains bounded, sanitized and opt-in.

Before release, require closed blocking findings, green checks, clean tree,
version/changelog agreement, clean-venv package-install smoke, and candidate-bound
live/package evidence. After publication verify PyPI and pip installation of
0.15.0; winget/hash gates do not apply to PeerHub.

Rollback: abandon this uncommitted documentation change by removing its new plan
and sole index line. For PR-A regressions, hold publication and revert the focused
implementation PR without deleting stores/observations or historical evidence.
If already published, issue a corrective version and document/yank a broken
artifact as appropriate; never overwrite 0.15.0 or silently restore unsafe claims.
