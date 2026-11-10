# peerhub 0.24.5 - FROZEN SPEC v2 (R4; v1 APPROVED by cc, cx amendments merged). Reply: APPROVE, or DISAGREE + concrete change. <=150 words, English.

R1 (cc.deepthink, cx.effort) amendments merged below. ag reviews this for the first time: challenge anything.

## S1 Model refresh (data only)
- model_ping 2026-11-10: PASS cc.standard/effort/deepthink (stream modelUsage), cx x5 (session log model+effort).
  Pending: ag x4 (after G-5H reset), cc.fable (after F-7D reset ~12:59Z).
- toml: defaults_revision 2026-11-10.1, observed_at 2026-11-10, observed_versions ag 1.5.4 / cc 2.1.322 / cx 0.167.0.
  `checked` advances only for profiles whose ping PASSED; failed/pending stay old and are listed in CHANGELOG.
  Evidence strength stated in adapter docs: cx = model+effort; cc/ag = model only (effort not independently visible).
- Fable: switch to claude-fable-5-2 only if stream metadata names it AND the profiles-2026-11-08 task + grade.py
  scores 45/45. Result recorded either way.

## S2 Profile cards (AI-readable)
- Optional keys per profile in model-defaults.toml: `use_for` (<=80 chars), `avoid_for` (<=80), `traits` (list, <=3,
  each <=40), `evidence` (list; each = existing lesson id | existing repo path | "#<n>"), `card_checked` (YYYY-MM-DD).
  If any card key is set, `evidence` and `card_checked` are required. Cards never restate pools.
- Overlay rule ("changes" = differs from the effective value of the lower layers; repeating a value keeps the card): if a layer changes `selection_mode`, `model` or `reasoning_effort`, ALL card keys inherited from lower
  layers are dropped first, then that layer's own card keys apply (they need their own evidence + card_checked).
- Validation in profiles.py loader: format, types, caps, required pair only (wheel has no repo paths; overlays are
  format-checked only). Existence of every packaged evidence ref is checked only by the manifest test.
- Surface (no new subcommand): `profile list --json` carries `card` (effective, with per-key source layer);
  text `profile list` adds a truncated USE FOR column; `peerhub explain <exact profile id>` is matched BEFORE the
  lessons fallback and prints the effective card ("no card" vs "unknown profile" distinguished); `--json` -> {"profile": {...}}.
  Offline: reads config only, no store. guide profile table unchanged except where it already shows profiles.
- All 12 packaged profiles get a card (cx.terra is a personal overlay profile, not packaged), from evidence only (benchmarks, lessons, issues, measured quota behavior).

## S3 Quota efficiency report
- tools/quota_report.py, read-only (reuse quota_backtest.read_samples), `--since`, `--db`; groups by account+pool+stable
  reset; never sums across profiles or blames a peer (other owner sessions share the accounts).
- Closed 7D window verdict, exactly one, checked in this order:
  EXHAUSTED_EARLY = a 0% sample observed with >12h to reset (report first-zero time bound and observed exhausted span);
  POSSIBLY_UNUSED = last sample within 12h of reset shows >15% left (report bound; heuristic, not proof);
  USED = last sample within 12h of reset shows <=15% left; UNKNOWN = otherwise (coverage gap).
- Guards: PACE only from fresh MEASURED samples with a valid window anchor, else UNKNOWN + reason; exhaustion spans
  are sampled evidence (no continuity assumed between samples); a window cut by --since is marked incomplete.
- Open window: PACE pp = used% - elapsed%; slow < -10, fast > +10, else on-pace; separate section.
- 5H: "observed exhausted windows" count per pool. Thresholds printed in the report header.
- Output committed: docs/history/quota-efficiency-2026-11-10.md. Measures utilization, not task value.

## S4 Release
Branch (version bump 0.24.5, CHANGELOG, STATUS inside the single PR) -> serial pytest + pyright -> PR -> CI -> squash merge -> merged-HEAD three-stage live gate (tools/live_gate,
--yes, stamped assets) + CONTRIBUTING preflight -> tag/release 0.24.5 -> PyPI verify -> delete branch -> STATUS,
CHANGELOG, memory -> delete _inbox/tmp/peerhub-0245. #323/#324/#325 stay open (separate PRs; #325 next).

## Completeness matrix (frozen; score = passed / applicable x 100; missing evidence = fail; N/A needs a reason)
Rows x columns cc | cx | ag:
M1 CLI measured (list/ping)      M2 toml binding+checked current    M3 adapter doc current
M4 card present + evidence valid M5 card overlay rule tested        M6 card visible in list/--json/explain
M7 report covers every 7D pool   M8 report covers every 5H pool     M9 CHANGELOG/STATUS records it
Global rows (single cell): G1 pytest serial green  G2 pyright 0  G3 CI green  G4 live gate 3 stages
G5 PyPI 0.24.5 installs + `peerhub --version`  G6 branch/tmp cleaned  G7 issues/memory updated
Total applicable cells = 27 + 7 = 34. Gate A (publish): all pre-publication cells (34 minus G5, G6, G7) pass + unanimous
APPROVE. Gate B (complete): 34/34 after publication. M5 may be one parametrized test using each provider's real profile.
Post-publication memory/STATUS evidence never changes the released commit.
Roles: cc implements (C-7D expires ~4h, 19% left), cx reviews diffs, ag reviews cards + report text.

## Decision log (2026-11-10, UTC)

| Step | Decision | Votes |
|---|---|---|
| R1 plan | Cards in model-defaults.toml; one-off read-only quota report script; Fable 5.2 only with identity + benchmark | cc.deepthink, cx.effort (with amendments) |
| R2-R4 spec | Frozen spec v1 -> v2 (overlay "changes" = differs; format-only loader; release gates A/B) | cc.deepthink, cx.effort, ag.effort: unanimous |
| Scope | Add #325 (cc oversized-line gap resolved by a later intact result) | cc.deepthink (design), cx.effort, ag.effort: unanimous |
| Scope | #323 batch shared ag workspace: deferred (issue comment has the design) | cx APPROVE with conditions, ag DISAGREE: no unanimity |
| Follow-up | 0.24.6 (separate PR after 0.24.5): #327 items 1/5/9 + read-only `diag running` without progress fields | cx, ag: unanimous on the narrowed scope |

Completeness matrix (score = PASS / applicable non-pending cells):

| Round | cc.deepthink | cx.effort | ag.effort |
|---|---|---|---|
| 1 (audit) | 26/28 (93%) | - | spec + cards approved |
| 2 | 21/27 (78%) | 17/25 (68%) | - |
| 3 | 29/29 (100%) | 23/27 (85%) | - |
| 4 | - | 27/27 (100%) | 29/29 (100%) |

Pending at round 4 (not defects): cc.fable check after the F-7D reset, full serial pytest, CI, merged-HEAD live gate, publication (G3-G7).
