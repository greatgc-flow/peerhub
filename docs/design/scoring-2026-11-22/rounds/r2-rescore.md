# Round 2: re-score after the load line, explain, help and CLI-audit fixes

Peers: cx.effort, ag.neon, ag.argon.

## Prompt (instruction part; the attached material is the CLI output of the commit named above)

```text
Review and SCORE the CURRENT state. Reply <=400 words. NO edits.
PeerHub = CLI for AI agents (cc/cx/ag + API peers) to ask each other with durable evidence and quota-aware routing. Principles: PeerHub gives facts, the AI decides; very simple, additive; `peerhub monitor` = human screen, AIs use guide/explain/diag at minimum tokens.
Previous independent scores (same rubric): parallel execution mean 68 (A info 72-80, B hints 80-88, C concurrency 40-60, D failure/recovery 50-72, E cost 78-90, F docs/tests drift 45-76); AI scenarios mean 79; human scenarios mean 76.
Since then we changed: guide Batch line (n at once as quota and load allow), `diag --plan` load line (active asks, per-peer slots, free RAM), `explain parallel-quota` (facts first, default plan, in-flight margin, per-task refusal, one wave per manifest, same batch-id replay, observation refresh between waves), docs/batch.md H/J pointers, top-level --help start line, `explain` lists id:title when several match, `profile stats/check --workspace`, `diag running --peer/--limit`.
Give: (1) axes A-F for parallel execution, 0-100 each + mean; (2) AI mean and human mean (one number each, name the weakest scenario of each); (3) delta vs previous; (4) the 3 highest-value remaining gaps, each with a minimal additive fix or "do not build: reason". Be strict; do not inflate.
```

## Replies (verbatim apart from local paths)

batch rescore max-parallel 3
rs-cx                    cx       COMPLETED
rs-neon                  ag       COMPLETED
rs-argon                 ag       COMPLETED
--- rs-cx reply ---
I’ll check the current implementation and tests against the supplied CLI output, then score it on the previous rubric.
I found a load-reporting issue that affects wave sizing. The plan counts rows from a capped lease list and can show zero active asks when that read fails. The first 19 targeted checks passed.
**Parallel execution: 72/100 (+4 from 68).** Discovery and planning improved. The batch runner still caps workers per invocation, while coordination across callers depends on a read-only snapshot.

| Axis | Score | Evidence |
|---|---:|---|
| A. Information | 80 | One compact quota and load view. Applicable pool gates still need interpretation. |
| B. Hints | 86 | Help and guide lead to a usable wave procedure with refresh, margin and replay guidance. |
| C. Concurrency | 54 | Local cap and workspace checks work. Load counts cover one store and can undercount. |
| D. Failure/recovery | 66 | Per-task refusal and durable replay help. An uncertain writer still needs manual reconciliation. |
| E. Cost | 84 | Cheap diagnostics, fresh streams and tier guidance. In-flight spend remains unknown. |
| F. Docs/tests drift | 62 | New checks mostly pin wording. README still sends AIs to monitor, and CONTRIBUTING still calls `--workspace` ask-only. |

**AI scenarios: 82 (+3).** Weakest: routing a mixed-profile wave when an applicable short window has expired and other asks are active.

**Human scenarios: 77 (+1).** Weakest: recovering a partially completed batch after a writer times out. Both scenario means are review estimates based on the supplied baselines.

**Three highest-value gaps**

1. **Make load counts trustworthy.** `diag --plan` counts at most 20 lease rows and can show zero when the lease read fails. Aggregate all active leases in the selected store, label the scope, and display unknown on failure. Apply `diag running --peer` before the row limit. [diag_running.py](/<repo>/peerhub/extensions/diag_running.py:74)
2. **Make profile gates visible.** The sample shows `G-7D hold` beside an expired `G-5H blocked`. Expose each profile’s existing pool bindings and window states in plan output so callers can resolve eligibility and choose the wave. [view.py](/<repo>/peerhub/cli/view.py:525)
3. **Verify mixed-outcome recovery.** Add one fake-peer test covering refusal, successful sibling delivery, and same-ID replay without duplicate execution. Point uncertain batch rows to the existing recovery guidance. [batch.md](/<repo>/docs/batch.md:119)

84 targeted checks passed. `tmp_path` cases and live scenarios were not run.
--- rs-neon reply ---
### 1. Parallel Execution (0–100)
- **A. Info — 82:** `diag --plan` (~350 tokens) now pairs quotas with live `load:` (active asks, per-peer slots, free RAM). Still lacks pool→profile mapping (`F-7D`/`A-7D`/`G-7D`), stale age, and consistent `5H` gate visibility (`C-5H`/`X-5H` omitted while `G-5H` shows `blocked`).
- **B. Hints — 85:** Crisp `guide`, clear `--help` start line, `explain` multi-match listing, solid `parallel-quota` policy. Hurt by wall-of-text prose in `explain parallel-quota` and `guide` omitting `observation refresh`.
- **C. Concurrency — 60:** Live slot/RAM facts and `diag running --peer/--limit` help, but `batch run` only has global `--max-parallel` (mixed manifests can exceed `ag 1`), `--dry-run` ignores live load/blocked pools, and concurrent callers still race.
- **D. Failure & Recovery — 66:** Clearer contract (per-task refusal, one wave/manifest, `--batch-id` replay, reconcile uncertain first) plus `diag running`, but partial/uncertain batch recovery remains manual.
- **E. Cost — 86:** Lean ~350-token plan+load call and zero-token `observation refresh`; minor overhead from verbose `explain` prose and extra `profile list` lookups to map pools.
- **F. Docs/Tests Drift — 64:** Visible drift: `C-5H`/`X-5H` omitted despite "5H only gates"; `explain parallel-quota` and `docs/batch.md J` duplicate and diverge (top-down allocation rule and internal `POOLS_USE_FIRST_ASCENDING` appear only in `batch.md`).
- **Mean: 74**

### 2. Scenarios
- **AI Mean: 83** — *Weakest:* Multi-peer wave planning and partial-batch recovery (pool→profile lookup, omitted `5H` gates, reconciling uncertain tasks before replay).
- **Human Mean: 78** — *Weakest:* Diagnosing a stalled or partially refused batch alongside active AI waves without checking internal docs/code.

### 3. Delta vs Previous
- **Parallel mean:** **+6** (68 → 74; largest gains in C, A, D; modest in B, E, F)
- **AI mean:** **+4** (79 → 83)
- **Human mean:** **+2** (76 → 78)

### 4. Top 3 Remaining Gaps
1. **Pool→profile opacity & `5H`/staleness gap in `diag --plan`:** Add compact pool→profile hints (e.g., `F-7D: cc.fable`), stale age, and print `5H` for any peer whenever gating or non-nominal.
2. **Mixed-peer load blindness in `batch run`:** Add a read-only `--dry-run` summary comparing manifest peer counts against live `load:` and `blocked`/`rest` pools. **Do not build** hard slot locks or an auto-router: violates "facts only, AI decides" and adds stale-lock failure modes.
3. **Duplicated, drifting guidance (`explain` vs `docs/batch.md`):** Tighten `explain parallel-quota` into terse bullets, drop `POOLS_USE_FIRST_ASCENDING` from `docs/batch.md`, and add a contract test keeping `guide`/`explain`/`diag --plan` terms aligned.

--- rs-argon reply ---
### 1. Parallel Execution Scores (Axes A–F)

* **A. Information completeness & clarity — 85/100**: `diag --plan` now combines pool state and a live `load:` line (active asks, per-peer slots, free RAM) in one call, plus `diag running --peer/--limit` and `profile stats/check --workspace`. Held back because `diag --plan` requires cross-referencing `G-5H` (`blocked ... binding G-7D`) against `G-7D` (`ACTION=hold`), and pool IDs (`A-7D`, `F-7D`) do not show which `--profile` names map to them without calling `profile list`.
* **B. Hints & decision guidance — 90/100**: Clear "facts first, AI decides" boundary, explicit 7D PACE ordering, in-flight margin warning, and explicit peer/RAM ceilings. Minor deduction: `explain parallel-quota` is one dense paragraph (~280 words) rather than scannable bullets.
* **C. Concurrency & isolation mechanics — 64/100**: Live slot/RAM telemetry solves blindness, and per-task quota refusal lets the rest of a one-wave manifest run. However, `batch run` only takes a global `--max-parallel n` (no per-peer cap or workspace-collision check in `--dry-run`), and limits remain unenforced advice.
* **D. Failure & recovery — 76/100**: Same `--batch-id` replay for settled tasks, per-task refusal isolation, and inter-wave `observation refresh` are well specified. Reconciling `uncertain` writer tasks across isolated workspaces before replay remains manual.
* **E. Cost & token efficiency — 91/100**: `diag --plan` (~350 tokens) + zero-model-token `observation refresh` + `explain` `id:title` multi-match disambiguation + `--help` AI routing keep discovery overhead near minimal.
* **F. Docs & surface coherence — 84/100**: `guide`, `explain parallel-quota`, `--help`, and `docs/batch.md` J now agree on dynamic `n`, `monitor` vs `diag --plan`, and wave lifecycle.
* **Parallel Execution Mean**: **81.7 / 100**

---

### 2. Scenario Means & Weakest Scenarios

* **AI Scenarios Mean: 85** — *Weakest*: **Multi-wave writer recovery with gated/stale pools** (avoiding `G-7D` when `G-5H` blocks it, mapping pools to profiles, and reconciling an `uncertain` writer workspace before replaying `--batch-id`).
* **Human Scenarios Mean: 81** — *Weakest*: **Triage of partial-failure / uncertain batch waves** (`monitor` and `diag running` show quota and active asks, but humans lack a single batch-status summary showing settled vs. refused vs. uncertain tasks for a `--batch-id`).

---

### 3. Delta vs. Previous

* **Parallel Execution Mean**: **+13.7** (68 → 81.7; A +9, B +6, C +14, D +15, E +7, F +23.5 vs. prior midpoints)
* **AI Mean**: **+6** (79 → 85)
* **Human Mean**: **+5** (76 → 81)

---

### 4. Three Highest-Value Remaining Gaps

1. **5H-gated 7D pools still show `ACTION=hold` (`G-7D` vs `G-5H`) and hide profile names**
   * *Minimal additive fix*: In `diag --plan`, propagate a blocking 5H gate to the 7D parent's `ACTION`/`REASON` (e.g., `blocked | by G-5H`) and add a compact pool→profile legend line (or column) so AIs never schedule onto a 5H-blocked family or need an extra `profile list` call.
2. **Concurrent writer collision risk in `batch run`**
   * *Minimal additive fix*: Have `batch run --dry-run` flag a warning/error fact if two tasks in the same manifest share a `--stream`, or if two writable tasks share a `--workspace` (especially `ag`).
3. **Unenforced cross-process concurrency/RAM scheduler (Axis C ceiling)**
   * *Do not build*: A daemon or cross-process lock manager to enforce active-ask/RAM limits violates *"PeerHub gives facts, the AI decides; very simple, additive"* and adds stale-lock recovery failure modes.
