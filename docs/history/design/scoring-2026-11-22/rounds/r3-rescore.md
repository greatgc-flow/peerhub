# Round 3: re-score after lanes A and B, in-flight per pool and README steps

Peers: cx.effort, ag.neon, ag.argon. Commit 2e443ae1 (branch before the round-3 fixes). Result: parallel 87.7, AI 89, human 86, overall 87.6; the 95 bar was not met, the gaps below were fixed in round 4 preparation.

## Prompt (instruction part)

```text
Review and SCORE the CURRENT state. Reply <=380 words. NO edits. Be strict and evidence-based; do not inflate, do not deflate for taste.
PeerHub = CLI for AI agents (cc/cx/ag + API peers) to ask each other with durable evidence and quota-aware routing. Principles: PeerHub gives facts, the AI decides; very simple, additive; `peerhub monitor` = human screen, AIs use guide/explain/diag at minimum tokens. Do-not-build list (agreed): cross-caller quota lock or scheduler, auto-stop on throttle, CLI-enforced workspace isolation (they would make the CLI decide for the AI) - do not penalize their absence.
Previous independent scores: parallel execution mean 68 then 76 (A info 82, B hints 87, C concurrency 59, D failure/recovery 69, E cost 87, F docs/tests 70); AI scenarios 79 then 83; human scenarios 76 then 79.
Changes since the last scoring: `diag --plan` rows (JSON) now have profiles, age_seconds, ttl_seconds, fast, family, parent, gates; load line has per-pool `in flight`, `?` when leases are unreadable (never 0), 500-row limit; `rest` only above PACE +20; `batch run --dry-run` prints workload warnings (workspace overlap, per-peer and total limits, active now), the final report prints `outcomes:` counts, `batch run --help` shows the manifest shape; README has a First ask section and an upgrade plan with `.pre-update.bak` restore + `diag health`; docs/batch.md H vs J say which ranking is for what; top-level --help has a start line; `explain` lists several matches as id: title.
Give: (1) axes A-F for parallel execution, 0-100 each + mean; (2) AI scenarios mean and human scenarios mean, each with the weakest scenario; (3) the overall mean of the three; (4) for any number under 95: the single concrete missing thing and a minimal additive fix (or "do not build: reason"). If an axis is capped by a do-not-build item, score the axis as if that item were out of scope.
```

## Replies (verbatim apart from local paths)

batch rescore2 max-parallel 3
r2-cx                    cx       COMPLETED
r2-neon                  ag       COMPLETED
r2-argon                 ag       COMPLETED
--- r2-cx reply ---
I’ll check the current code and tests against the supplied CLI output, then score the workflows within the agreed scope.
The new plan fields and batch reports close several previous gaps. The dry run still compares manifest totals with concurrency limits, so it can warn about tasks that would never run together.
**Overall 86.7/100.** Parallel execution **87**, AI scenarios **88**, human scenarios **85**. The agreed exclusions carry no penalty.

| Parallel axis | Score | Missing item and minimal fix |
|---|---:|---|
| A. Information | 89 | Plan JSON exposes freshness, profiles and gates. Pool counts omit lease-active asks whose profile cannot be mapped. Show an `unmapped` count alongside `by_pool`. [Load sampling](/<repo>/peerhub/extensions/diag_running.py:99) |
| B. Hints | 92 | The help manifest includes both mutually exclusive prompt fields. Show one runnable example and list `prompt_file` as the alternative. [Batch help](/<repo>/peerhub/extensions/batch.py:103) |
| C. Concurrency | 82 | Dry run compares the entire manifest with simultaneous limits. Compare prospective overlap under `--max-parallel` with sampled active slots, as advice. [Load warnings](/<repo>/peerhub/extensions/batch.py:202) |
| D. Failure and recovery | 82 | Outcome totals help triage, but task rows drop the child ask’s recovery hint. Carry its `explain_cmd` into failed or uncertain rows when available. [Batch report](/<repo>/peerhub/extensions/batch.py:447) |
| E. Cost | 91 | Full routing facts require verbose, indented JSON. Render each pool on one line while preserving the schema. [Plan output](/<repo>/peerhub/cli/app.py:623) |
| F. Docs and tests | 86 | README still directs AI sessions to a monitor snapshot. Replace that example with `diag --plan`. [README](/<repo>/README.md:132) |

**Parallel mean 87.** The most consequential gap is the dry run’s overlap calculation (C).

**AI mean 88.** Weakest is A5, timeout recovery. Add a short guide instruction to check `diag running` when the caller stops waiting, since the ask may still be running.

**Human mean 85.** Weakest is H5, upgrade and restore. The README proceeds from planning to installation without explicitly telling users to run the printed backup command. Make successful backup creation a numbered step before installation. That is the highest-priority fix for the overall score. [Upgrade steps](/<repo>/README.md:454)

114 targeted offline checks passed. The 56 `tmp_path` cases and live scenarios were **NOT RUN**. Scenario means are review estimates. No edits.
--- r2-neon reply ---
### 1. Parallel Execution (A–F)
- **A. Info: 90** — Text `diag --plan` omits `fast` and short-window `gates` flags that `explain parallel-quota` tells AIs to filter on (only JSON has them). **Fix:** append `(fast)` / `(gate: <pool>)` tags to text `REASON`.
- **B. Hints: 90** — `explain parallel-quota` omits `docs/batch.md` I's "lower concurrency, do not reroute on throttle" rule (and `docs/batch.md` omits `ag.neon`, listed as a top in `guide`). **Fix:** add that throttle line to `explain parallel-quota` and sync `ag.neon`.
- **C. Concurrency: 82** *(do-not-build cross-caller lock/scheduler, auto-stop, workspace isolation excluded)* — `batch run --dry-run` checks overlaps and peer/total limits but omits projected `by_pool` load and `ram_ok` warnings. **Fix:** add projected `by_pool` + RAM warnings to `--dry-run` (do not build cross-caller locking or auto-throttling: CLI must not decide).
- **D. Failure/Recovery: 79** *(auto-stop excluded)* — No read-only view of a batch's per-task state after partial failure/crash, and `batch-id` replay leaves reconciling uncertain tasks (vs `guide`'s "new stream" rule) unspecified. **Fix:** add `peerhub batch status --batch-id ID` showing per-task status + derived `stream`/`request_id`, plus reconcile-then-retry steps in `explain parallel-quota`.
- **E. Cost: 90** — `diag --plan --json` emits indented multi-line JSON with 16-digit floats, inflating tokens when AIs need `profiles`/`fast`. **Fix:** round floats to 1–2 decimals and emit compact JSON.
- **F. Docs/Tests: 84** — `batch run --help` shows an invalid manifest containing both `prompt` and `prompt_file` in one task (copy-paste fails validation). **Fix:** show two valid tasks (one `prompt`, one `prompt_file`) in `batch run --help`.
- **Mean: 85.8**

### 2. Scenarios
- **AI Scenarios Mean: 88** — *Weakest:* recovering uncertain/refused batch tasks (same gap as D). **Fix:** `batch status --batch-id` + reconcile recipe in `explain parallel-quota`.
- **Human Scenarios Mean: 85** — *Weakest:* authoring and recovering a manual batch (`batch run --help` invalid manifest + no partial-failure walkthrough). **Fix:** fix `batch run --help` example and add a 3-line dry-run/recovery snippet in `docs/batch.md`.

### 3. Overall Mean
- **86.3** (`(85.8 + 88 + 85) / 3`)

--- r2-argon reply ---
### (1) Parallel Execution (Axes A–F)
* **A. Info (93)**: `diag --plan` (text + JSON) now exposes `profiles`, `age_seconds`, `ttl_seconds`, `fast`, `family`, `parent`, `gates`, and `by_pool` in-flight counts (with `?` on unreadable leases).
* **B. Hints (94)**: `rest` tightened to `PACE > +20`; H vs. J ranking roles (`LEFT%/days` single ask vs. `7D PACE` wave) and `gates` semantics are explicit.
* **C. Concurrency (86)** *(scored with cross-caller lock/scheduler & workspace isolation out of scope)*: `--dry-run` now surfaces workspace overlaps, per-peer/total limits, and active counts alongside `in flight` pool load.
* **D. Failure/recovery (84)** *(auto-stop out of scope)*: `outcomes:` summary, per-task refusal isolation, idempotent `--batch-id` replay, and `.pre-update.bak` restore + `diag health` flow are solid, though batch reconciliation remains prose-driven.
* **E. Cost (94)**: ~350-token `diag --plan`, zero-token `observation refresh`, fresh streams per task, and explicit in-flight margin rules.
* **F. Docs/tests (90)**: Manifest shape in `batch run --help`, top-level `--help` start line, aligned `explain parallel-quota` and `docs/batch.md` H–J.
* **Parallel Execution Mean**: **90.2** (up from 76)

### (2) Scenario Means & Weakest Scenarios
* **AI Scenarios Mean: 91.0** (up from 83)
  * *Weakest AI Scenario (84)*: **Post-batch uncertain-task reconciliation**. When a batch finishes with mixed `outcomes:` (e.g., 1 timeout/uncertain, 2 settled), the AI knows `--batch-id` replays settled tasks, but has no single copy-pasteable inspection command in the batch report to reconcile the uncertain task before re-running.
* **Human Scenarios Mean: 88.0** (up from 79)
  * *Weakest Human Scenario (83)*: **Windows upgrade recovery under active locks**. `update --plan` and README steps 0–4 are clear, but a human still manually sequences stopping monitor/asks, copying `.pre-update.bak`, and running `diag health`.

### (3) Overall Mean
* **Overall Mean**: **89.7** (`(90.2 + 91.0 + 88.0) / 3`)

### (4) Under-95 Gaps & Minimal Additive Fixes
* **A. Info (93)**: Text `diag --plan` omits which profiles map to each pool and whether `fast` is true (only in `--json`). *Fix*: Add a compact `FAST` flag or `PROFILES` column/footer in text `diag --plan`.
* **B. Hints (94)**: `guide` doesn't mention `observation refresh` when `diag --plan` shows `(stale)`. *Fix*: Add 4 words to `guide` Quota line: `(stale: peerhub observation refresh)`.
* **C. Concurrency (86)**: `batch run --dry-run` JSON output schema for workload warnings (`workspace_overlap`, `peer_over_limit`, `pool_in_flight`) isn't shown in `batch run --help`. *Fix*: Add a 1-line `--dry-run --json` warning keys note to `batch run --help`.
* **D. Failure/recovery (84) & AI Scenarios (91.0)**: Batch failure report totals `outcomes:` but doesn't print the exact follow-up command for uncertain tasks. *Fix*: Print `reconcile: peerhub record <stream> --request-id <id>` per uncertain task in the `batch run` summary.
* **E. Cost (94)**: No token-cost hint on `batch run --responses` (can dump large replies into AI context). *Fix*: Note `(omits large reply text by default to save tokens)` in `batch run --help` for `--responses`.
* **F. Docs/tests (90)**: `batch run --help` manifest example sets both `"prompt"` and `"prompt_file"` in the same task object while stating "give exactly one". *Fix*: Comment out or split `"prompt_file"` in the help example so copy-pasting validates cleanly.
* **Human Scenarios (88.0)**: Manual `.pre-update.bak` copy commands on restore. *Fix*: Have `peerhub update --plan` also print the exact `Copy-Item` restore + `peerhub diag health` rollback one-liner.
