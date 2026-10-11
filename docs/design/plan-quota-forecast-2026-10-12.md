Plan document, 2026-10-12. Reference, not an instruction file.

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
| D4 | One engine and timestamp policy; fresh, validated anchors; elapsed >=10% AND used >=5 percentage points; threat only at projected reset usage >=105%. Below support gates: `not enough data`, never `on pace`. |
| D5 | Missing selected store: exit 5, `database not found: <path>`, no creation and no monitor probes. |
| D9 | Create root `CHANGELOG.md` in implementation PR-A; its selected version section is the SSOT for the GitHub Release body. PeerHub has no winget package. |
| D10 | Release 0.15.0, reflecting breaking projection JSON and exit behaviour; no legacy aliases or duplicate compatibility fields. |

Fresh measured exhaustion precedes support thresholds. Stale/expired evidence
and unknown or invalid anchors suppress forecasts. ETA remains inline and is
labelled `conditional, window-average`; predictive accuracy is not demonstrated.
No new third-party dependency in PR-A; minimal Windows VT enablement uses ctypes.

## Implementation PR-A file and behaviour scope

All paths below are future PR-A work, not changes made by this documentation task.

| Files | Resulting responsibility |
| --- | --- |
| `peerhub/extensions/quota_projection.py` | `project_exhaustion` is the sole derivation engine; remove recent-rate/hybrid logic. |
| `peerhub/extensions/quota_probes.py` | Record anchor basis; validate each provider's window assumptions. Unsupported rolling/inferred semantics cannot silently become validated anchors. |
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
PACE --  not enough data
PACE +12.0pt  threat: exhaust ~Tue 10-14 03:11 +0900 (1d18h) [conditional, window-average]
exhausted
unknown
stale
expired
Threats: 2; earliest: cc/weekly ~Tue 10-14 03:11 +0900; pools: cc/weekly, cx/weekly
Probe health:
database not found: <path>
```

PACE uses signed one-decimal percentage points and the projection's state for
colour; insufficient/unknown/stale/expired states show no numeric forecast.
Threat times include date when over 24 hours away or crossing local midnight,
and a timezone offset. Probe failures appear under `Probe health:` with reasons,
never as fabricated quota-pool rows. The threat header lists every threatened
pool, its count and the earliest ETA; the inline label applies to every ETA.
Missing-store plain output is one stderr line with the selected path substituted,
exit 5; JSON mode preserves an unavailable/error envelope carrying that message.

VT-capable terminals may clear/redraw; redirected or unsupported output neither
clears nor prints raw control sequences or the literal ` [H [2J` artifact.
Cycle interval defaults to 60 seconds; collect on the first cycle and every Kth
cycle (`--collect-every K`). Terminal countdown redraw is at most once/second
from the same snapshot, with no extra probes; plain/NDJSON emits once/cycle.
Help distinguishes cycle, collection and redraw cadence.

Opt-in trace collection: `peerhub monitor --json >> trace.ndjson`.
Bound with `--cycles N`; sanitize before sharing and apply a size/retention limit.
Retain only needed usage, anchor, freshness, projection/version and outcome data.
This uses existing NDJSON, not a new history service or heavy backtest harness.

## Tests and acceptance criteria

- Boundary fixtures cover just below/at 10% elapsed, 5pt used and 105% projected;
  quantised 1%, burst, idle, zero usage, reset crossing, invalid/unvalidated anchors,
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

Risks: inferred anchors may remain unvalidated and suppress most forecasts;
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
