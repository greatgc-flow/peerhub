# Round 6: re-score after lanes G and H (release bar met)

Peers: cx.effort, ag.neon, ag.argon. Commit 2757b42a. Result: parallel 95.3, AI 95.9, human 94.5, overall 95.2 (cx 95.1, neon 94.8, argon 95.8): the 95 bar is met. All three reviewers flagged the same defect, fixed after the round: the printed backup and restore commands called a bare `python` (a pipx or uv install may not have peerhub on that interpreter); they now print the interpreter that runs PeerHub (`sys.executable`, with `&` in PowerShell). Remaining notes (kept in issue 447): `profiles:`/`in flight` rows versus the plan table for omitted windows, the resolved default profile in the dry-run table, an 80-column truncation can hide `(stale)`, `explain discovery-commands` still points to the monitor, replay counts are by task ID.

## Prompt (instruction part)

```text
Review and SCORE the CURRENT state. Reply <=380 words. NO edits. Be strict and evidence-based; do not inflate, do not deflate for taste.
PeerHub = CLI for AI agents (cc/cx/ag + API peers) to ask each other with durable evidence and quota-aware routing. Principles: PeerHub gives facts, the AI decides; very simple, additive; `peerhub monitor` = human screen, AIs use guide/explain/diag at minimum tokens. Agreed do-not-build list (absence is not a penalty): cross-caller quota lock or scheduler, auto-stop on throttle, CLI-enforced workspace isolation, a keyword-search flag for explain, a separate `batch status` command.
Previous independent scores (same rubric): round 4 overall 92.2; round 5 parallel 94.8 (A 94, B 96, C 94, D 94, E 95.7, F 94), AI 95.0, human 93.2, overall 94.3.
Round 5 defects and what changed since: the printed backup command can run twice (UTC-stamped file name, never overwrites) and the printed restore uses the validated generation-rotating restore (Workspace.restore_snapshot, then diag health); README Upgrade names the stamped pattern; a stale window keeps its own (stale) tag when a sibling decides the reason; dry-run prints `active now ?` when the sample fails, shows `(peer default)` for an omitted profile, and prints a read-only `replay: N settled, M uncertain` line for a reused batch ID; docs/batch.md lists every dry-run warning kind; explain parallel-quota says a profile listed under several pools spends each of them. The update --plan output below is the real output for an idle store (with asks running it prints BLOCKED and lists them).
Give: (1) axes A-F for parallel execution, 0-100 each + mean; (2) AI scenarios mean and human scenarios mean, each with the weakest scenario; (3) overall mean of the three; (4) for every number under 95: the single concrete missing thing and a minimal additive fix (or "do not build: reason"). If an axis is capped only by a do-not-build item, score it as if that item were out of scope.
```

## Replies (verbatim apart from local paths)

batch rescore5 max-parallel 3
r5-cx                    cx       COMPLETED
r5-neon                  ag       COMPLETED
r5-argon                 ag       COMPLETED
--- r5-cx reply ---
I’ll read the repository guidance and trace the current CLI paths before scoring the supplied examples.
The first 36 targeted offline checks passed. I’ll check where the compact plan, replay preview, and printed restore command still leave gaps in the workflow.
**Overall 95.1/100** at `2757b42a`. Parallel **94.7**, AI scenarios **95.8**, human scenarios **94.8**. The agreed exclusions carried zero deductions. The component means use the same six-axis and six-scenario rubric as round 5. [Rubric](/<repo>/docs/design/scoring-2026-11-22/README.md)

| Parallel axis | Score | Evidence and minimal fix |
|---|---:|---|
| A. Information | 92 | I reproduced an 80-column `diag --plan` row where truncation hides `(stale)`. Preserve freshness in a short tag before the truncatable reason. [Renderer](/<repo>/peerhub/cli/view.py:661) |
| B. Hints | 97 | Help and guide lead directly to `diag --plan` and the wave-planning lesson. |
| C. Concurrency | 94 | Dry-run load warnings count settled replay rows as new asks. Label the projection as an upper bound when prior rows exist. [Batch CLI](/<repo>/peerhub/cli/app.py:1247) |
| D. Failure and recovery | 94 | A changed prompt can still count as `settled` in dry-run, then fail the execution identity check. Label the tally as prior state by task ID and say execution checks identity. [Replay reader](/<repo>/peerhub/extensions/ask.py:71) |
| E. Cost | 97 | Compact planning, fresh votes, and opt-in full replies keep the default path lean. |
| F. Docs and tests | 94 | `explain discovery-commands` still directs AI sessions to `monitor`. Point it to `diag --plan` and extend the existing guidance test. [Lesson](/<repo>/peerhub/config_data/lessons.toml:298) |

The **parallel mean’s** first fix is preserving freshness at ordinary terminal widths.

**AI mean 95.8** (A1–A6: 97, 97, 92, 97, 95, 97). Weakest is **A3, parallel wave planning (92)**. The freshness fix in A keeps the one-call text plan usable at 80 columns.

**Human mean 94.8** (H1–H6: 97, 97, 96, 95, 88, 96). Weakest is **H5, upgrade and restore (88)**. The generated restore uses bare `python`, which can miss the PeerHub environment for a pipx or uv install. Print the invoking installation’s interpreter path with shell-appropriate quoting. [Upgrade planner](/<repo>/peerhub/cli/update.py:280)

Scenario scores are review estimates. **119 targeted offline tests passed.** Eight `tmp_path` cases and live tests were **NOT RUN**. No edits.
--- r5-neon reply ---
**(1) Parallel execution axes (0–100)**
- **A. Quota & load observability — 94**: Strong `diag --plan`, sibling `(stale)` tag, and JSON parity; `X-30D` is missing from `profiles:`/`in flight` while `n/a` 5H pools (`C-5H`, `X-5H`) appear in `in flight`.
- **B. Guidance & token efficiency — 97**: Crisp `guide`, thorough `explain parallel-quota` (multi-pool spend), and aligned `docs/batch.md` H–J.
- **C. Dry-run & pre-flight safety — 94**: `active now ?`, replay counts, and warning docs landed; `(peer default)` hides the resolved profile and exceeding ceiling 6 duplicates the default-4 warning.
- **D. Execution & concurrency — 96**: Solid wave limits and per-task quota refusal; do-not-build items treated as out of scope.
- **E. Idempotency & recovery — 96**: Deterministic IDs, read-only replay preview, and explicit `diag running`/`diag health --stream` recovery.
- **F. Store safety & ops — 93**: Non-clobbering UTC backup and validated restore landed; `update --plan` restore uses bare `python` beside `pipx upgrade` and omits `diag health` in step 3.
- **Parallel mean: 95.0**

**(2) Scenario means**
- **AI mean: 95.5** — Weakest: dry-run pool verification when `profile` is omitted (`(peer default)`) and `X-30D` footer mismatch.
- **Human mean: 93.8** — Weakest: `pipx` upgrade/rollback (bare `python` restore fails outside the venv; `README` restarts `monitor` before `diag health`; step 2 says "Choose one" with one option).

**(3) Overall mean: 94.8** (`(95.0 + 95.5 + 93.8) / 3`)

**(4) Every number under 95 — single missing thing & minimal additive fix**
- **A (94)**: `diag --plan` footer mismatches the pool table (`X-30D` absent from `profiles:`/`in flight`; omitted 5H pools shown in `in flight`). *Fix*: align `profiles:` and `in flight` with active table pools (or tag parent-only/omitted rows).
- **C (94)**: `--dry-run` prints `(peer default)` without the resolved profile name. *Fix*: print `(default: <profile>)` and suppress the `default 4` warning when `ceiling 6` triggers.
- **F (93), Human (93.8)**: `update --plan` restore invokes bare `python` (fails under `pipx`/`uv tool`) and omits post-upgrade `diag health` before restarting `monitor`. *Fix*: emit `sys.executable` in restore, add `peerhub --db <path> diag health` to step 3, and move `diag health` before `monitor` restart in `README`.
- **Overall (94.8)**: Pulled below 95 by A, C, and F/Human; applying those three fixes clears 95.

--- r5-argon reply ---
### (1) Parallel Execution Axes (A–F)

* **A (Quota & pool visibility): 96/100** — `diag --plan` keeps the `(stale)` tag on sibling-bound rows (`X-7D` shows `X-30D: on pace (stale)`), maps profiles per pool, and `explain parallel-quota` notes multi-pool profiles spend each pool (worst action applies).
* **B (Concurrency & load awareness): 96/100** — `load:` line reports active vs. default/ceiling, per-peer slots, per-pool in-flight (and unmapped), plus RAM.
* **C (Batch pre-flight & dry-run): 97/100** — `--dry-run` now prints `(peer default)` for omitted profiles, `active now ?` on sample failure, a read-only `replay: N settled, M uncertain` line on reused batch IDs, and documents all six warning kinds in `--help` and `docs/batch.md`.
* **D (Wave sizing & pool ordering): 96/100** — Clear 7D PACE ordering, family parent rules, 5H gating, and dynamic wave sizing (`n`) aligned between `explain parallel-quota` and `docs/batch.md` J.
* **E (In-flight & wave transitions): 96/100** — Explicit refresh-then-plan cycle, per-pool in-flight visibility, and per-task quota refusal behavior within a wave.
* **F (Failure recovery & idempotency): 96/100** — Reused `--batch-id` replays settled tasks, dry-run previews replay counts, and timeout/uncertain flows point to `diag running` and `diag health --stream`.
* **Parallel Execution Mean: 96.2**

---

### (2) AI & Human Scenarios

* **AI Scenarios Mean: 96.3**
  * *Weakest AI Scenario (95/100 — Picking default profile vs. pool mapping during dry-run):* `batch run --dry-run` shows `(peer default)` when `profile` is omitted, without naming which concrete profile/pool that default resolves to (though `guide` and `batch --help` advise passing explicit `--profile`).
* **Human Scenarios Mean: 94.8**
  * *Weakest Human Scenario (94/100 — Upgrading across Python virtual environments on Windows):* See item (4) below.

---

### (3) Overall Score

* **Overall Mean: 95.8** *(mean of 96.2, 96.3, 94.8)*

---

### (4) Items Under 95

* **Human Scenario — Upgrade / Restore across isolated installers (94/100):**
  * **Single concrete missing thing:** `update --plan` prints bare `python -c ...` for both backup and `Workspace.restore_snapshot(...)`. When PeerHub was installed via `pipx` or `uv tool` (the recommended options right below it), bare `python` on PATH frequently lacks the `peerhub` package (failing restore with `ModuleNotFoundError: No module named 'peerhub'`) or can differ in SQLite build from the tool venv.
  * **Minimal additive fix:** In `update --plan`, emit `sys.executable` (the actual interpreter running `peerhub`) instead of bare `python` in the printed backup and restore commands.
