# Round 7: regression check on main after 0.26.6

Peers: cx.effort, ag.neon, ag.argon. Commit 2922376d (main after 0.26.6: suggest --candidates, ask --dry-run, diag filters, explain exit-codes, help gaps, Windows probe jobs). A regression check before a possible 0.26.7. Result: parallel 96.1, AI 96.6, human 95.7, overall 96.1 (cx 95.9, neon 96.3, argon 96.2); no regression. Convergent small notes, fixed after the round: `suggest --candidates` printed a bare `not suggested` reason; the printed `update --plan` had no `diag health` before the monitor restart; README step 0 did not say to wait for running asks; the stale tag could be cut off at 80 columns.

## Prompt (instruction part)

```text
Review and SCORE the CURRENT state (round 7, a regression check before a release). Reply <=380 words. NO edits. Be strict and evidence-based; do not inflate, do not deflate for taste.
PeerHub = CLI for AI agents (cc/cx/ag + API peers) to ask each other with durable evidence and quota-aware routing. Principles: PeerHub gives facts, the AI decides; very simple, additive; `peerhub monitor` = human screen, AIs use guide/explain/diag at minimum tokens. Agreed do-not-build list (absence is not a penalty): cross-caller quota lock or scheduler, auto-stop on throttle, CLI-enforced workspace isolation, a keyword-search flag for explain, a separate `batch status` command.
Previous independent scores (same rubric): round 5 overall 94.3; round 6 overall 95.2 (parallel 95.3 with A 95.3, B 96.7, C 93.3, D 94.3, E 96.7, F 95.7; AI 95.9; human 94.5).
Changes since round 6 (all on main): `peerhub suggest --candidates`; `peerhub ask --dry-run`; `session list/show --json` context fields; `diag quota --max-age-seconds`, `diag --plan --peer/--observation-db`; `peerhub explain exit-codes`; help text with units and defaults for every option; the round-6 notes (profiles/in flight only name table pools, one active-ask warning, explain discovery-commands points AI to diag --plan, README upgrade order); the printed backup/restore of `update --plan` use the interpreter that runs PeerHub; Windows quota probes run in a Job Object.
Give: (1) axes A-F for parallel execution, 0-100 each + mean; (2) AI scenarios mean and human scenarios mean, each with the weakest scenario; (3) overall mean of the three; (4) delta vs round 6; (5) for every number under 95: the single concrete missing thing and a minimal additive fix (or "do not build: reason").
```

## Replies (verbatim apart from local paths)

batch rescore6 max-parallel 3
r6-cx                    cx       COMPLETED
r6-neon                  ag       COMPLETED
r6-argon                 ag       COMPLETED
outcomes: ok 3
--- r6-cx reply ---
I’ll review the current checkout against the round 6 scores and trace the release changes through the CLI and its tests. I’ll keep the review read-only.
The 172 targeted offline tests passed. I’ll check the two places where the command output itself may still affect a release decision: freshness at ordinary terminal widths and the upgrade plan’s verification step.
**Round 7 overall: 95.9/100, up 0.7 from round 6.** This clears the 95 scoring bar. Reviewed `main` at `2922376d` using the same six-axis and six-scenario rubric. The agreed exclusions carried no deductions.

| Parallel axis | Score | Evidence and minimal fix where needed |
|---|---:|---|
| A. Information | 94 | Reproduced an 80-column `diag --plan` row that hides `(stale)` on `X-30D`. Put a short stale tag before the truncatable reason. [Renderer](/<repo>/peerhub/cli/view.py:663) |
| B. Hints | 97 | Help, guide, and discovery now lead AI callers to `diag --plan`. |
| C. Concurrency | 94 | Batch dry-run load warnings include settled tasks that may replay. Label the projection as an upper bound when prior settled tasks exist. [Batch CLI](/<repo>/peerhub/cli/app.py:1349) |
| D. Failure and recovery | 95 | Per-task refusals, outcome totals, settled replay, and stream-specific recovery commands cover partial waves. |
| E. Cost | 97 | One compact planning call, zero-model-token refresh, and fresh independent votes keep the default path lean. |
| F. Docs and tests | 97 | Shared limits, matching batch guidance, and CLI output tests support the parallel workflow. |

**Parallel mean: 95.7 (+0.4).**

**AI mean: 96.3 (+0.4).** A1–A6 scored 97, 97, 94, 97, 96, 97. Weakest is **parallel wave planning (94)**. The one-call plan can hide the freshness cue that tells the caller to refresh. Apply the renderer fix in A.

**Human mean: 95.7 (+1.2).** H1–H6 scored 97, 97, 97, 95, 91, 97. Weakest is **upgrade and restore (91)**. The generated `update --plan` now uses the invoking Python for backup and restore, but still puts `peerhub --version` immediately before monitor restart. Add `peerhub --db PATH diag health` and make restart conditional on OK, matching the README. [Upgrade planner](/<repo>/peerhub/cli/update.py:305)

Scenario scores are review estimates. **294 targeted offline tests passed**, including the Windows child-process cleanup test. The 33 fixture-dependent cases and live provider tests were **NOT RUN**. No edits.
--- r6-neon reply ---
**Round 7 Review (Release Regression Check)**

No regressions detected across `guide`, `diag --plan`, `batch run --dry-run`, `ask --dry-run`, `explain`, or README. All round-6 notes landed cleanly; one new feature (`suggest --candidates`) has an output gap.

### 1) Parallel Execution Axes (0–100)
- **A (Quota visibility & freshness):** **96.5** (+1.2) — `--max-age-seconds`, `--peer`/`--observation-db`, table-scoped `profiles`, Windows Job Object probes. Minor: footer `n/a pools 1` is slightly ambiguous beside 3 `n/a` rows (`disabled` vs `plan omits`).
- **B (Load & concurrency safety):** **97.5** (+0.8) — scoped `in flight`, single active-ask warning in `batch run --dry-run`, Job Object probe isolation.
- **C (Routing & pre-flight checks):** **94.5** (+1.2) — `ask --dry-run` is clear and complete; however, `suggest --candidates` prints tautological `not suggested` in `REASON` and omits `(stale)` on `cx.effort`, while `batch run --dry-run` prints `(peer default)` instead of the resolved profile.
- **D (Batch reliability & error semantics):** **95.8** (+1.5) — `explain exit-codes` and `session --json` close scripting and recovery ambiguity (including the documented `ask` vs `batch` empty-reply exit difference).
- **E (AI token efficiency & discovery):** **97.2** (+0.5) — `discovery-commands` points to `diag --plan`, `--plan --peer` trims tokens, options document units/defaults.
- **F (Human UX & lifecycle safety):** **96.5** (+0.8) — README upgrade order, interpreter-pinned `update --plan` commands, complete option help.
- **Parallel mean:** **96.3** (exact 96.33)

### 2) Scenario Means
- **AI scenarios:** **96.8** (+0.9) — *Weakest:* automated multi-candidate fallback via `suggest --candidates` (opaque `not suggested` reasons and missing stale indicator still require falling back to `diag --plan`).
- **Human scenarios:** **95.7** (+1.2) — *Weakest:* upgrade rollback on `pipx`/`uv` installs (README Upgrade step 3 still instructs running restore "in a shell where `python` has that PeerHub version installed," lagging `update --plan`'s interpreter-pinned command).

### 3) Overall Mean
- **96.3** — mean of Parallel (96.33), AI (96.8), and Human (95.7).

### 4) Delta vs Round 6
- **Overall:** **+1.1** (95.2 → 96.3)
- **Breakdown:** Parallel **+1.0** (95.3 → 96.3), AI **+0.9** (95.9 → 96.8), Human **+1.2** (94.5 → 95.7).

### 5) Numbers Under 95
- **C (94.5):**
  - *Missing:* Concrete disqualifier reasons in `suggest --candidates` (`REASON` outputs `not suggested` for both `ag.effort` in `rest (fast)` and API peers with no quota pool).
  - *Minimal additive fix:* Populate `REASON` with the actual cause (e.g., `rest (fast)`, `blocked`, `no quota pool`) and mark stale rows with `(stale)`.

--- r6-argon reply ---
### Round 7 Pre-Release Score & Regression Review

**(1) Parallel Execution Axes (0–100)**
- **A (Quota & Pool Visibility): 96.3** (+1.0) — `diag --plan` now scopes `--peer`/`--observation-db` and restricts `profiles:` / `in flight` strictly to table pools; `diag quota --max-age-seconds` adds explicit freshness gating.
- **B (Load, Concurrency & In-Flight Accounting): 97.3** (+0.6) — Single consolidated `active now 2` footer and combined batch + active warning (`2 concurrent batch tasks + 2 active asks exceed per-peer limit 3`) remove round-6 duplication.
- **C (Routing & Candidate Selection): 94.7** (+1.4) — `suggest --candidates` exposes all profiles per tier with `ELIGIBLE`, `WORST POOL`, `PACE`, `FAST`, and `REASON`.
- **D (Batch Pre-Flight, Wave Execution & Replay): 95.3** (+1.0) — `ask --dry-run` (showing `session_mode`, `session_handle`, `replay`, `writable`, timeouts) and consolidated `batch run --dry-run` warnings close single- and multi-ask pre-flight gaps.
- **E (Failure, Uncertainty & Recovery): 97.3** (+0.6) — `explain exit-codes` gives an exact contract (`0/1/2/3/4/5/6` and `ask` vs `batch` empty-reply semantics); Windows quota probes run in a Job Object.
- **F (Token Efficiency & AI Ergonomics): 96.3** (+0.6) — `guide` + `diag --plan` stays ~750 tokens total; `explain discovery-commands` routes AIs to `diag --plan`; `--help` includes units and defaults.
- **Parallel Execution Mean: 96.2** (+0.9 vs 95.3)

**(2) Scenario Means & Weakest Scenarios**
- **AI Scenarios Mean: 96.6** (+0.7 vs 95.9). *Weakest AI scenario (94.5):* Disambiguating `REASON: not suggested` in `suggest --candidates` (e.g., `ag.effort` at `77.2%` / `+1.2` pace with `FAST=yes` shows `not suggested` instead of the specific blocking cause like `rest/fast` vs `unconfigured`).
- **Human Scenarios Mean: 95.8** (+1.3 vs 94.5). *Weakest human scenario (94.5):* Backing up during active asks—README step 0 runs backup before step 1 stops `monitor` and running `ask` calls, while `update --plan` exits `1` if an ask is running.

**(3) Overall Mean: 96.2** (`(96.2 + 96.6 + 95.8) / 3`)

**(4) Delta vs Round 6: +1.0** (`95.2` → `96.2`; Parallel `+0.9`, AI `+0.7`, Human `+1.3`). No regressions found.

**(5) Items Under 95 (Missing Thing & Minimal Additive Fix)**
- **Axis C (94.7) & Weakest AI Scenario (94.5):** In `suggest --candidates`, `REASON` prints generic `not suggested` for every ineligible row. *Minimal additive fix:* print the specific reason token already computed (`rest (fast)`, `blocked`, `no quota data`, or `unconfigured`).
- **Weakest Human Scenario (94.5):** README Upgrade step 0 runs the backup command before step 1 stops active asks, even though `update --plan` exits `1` when asks are running. *Minimal additive fix:* note in step 0 to wait for running asks (`update --plan` exit 0) before running the final pre-upgrade backup.
