# Round 5: re-score after lanes E and F

Peers: cx.effort, ag.neon, ag.argon. Commit 6f25bc8d. Result: parallel 94.8, AI 95.0, human 93.2, overall 94.3 (cx 94.7, neon 93.9, argon 94.4); the 95 bar was not met. The first run of the two ag reviewers failed with `not_started` and was rerun under the same batch ID; their replies below are the rerun. Real defects found: the printed backup command could not run twice (fixed `.bak` name with `exist_ok=False`), the printed restore did not rotate `workspace.generation`, a stale window could be hidden behind its sibling's reason, `active now` was silent when the sample failed.

## Prompt (instruction part)

```text
Review and SCORE the CURRENT state. Reply <=380 words. NO edits. Be strict and evidence-based; do not inflate, do not deflate for taste.
PeerHub = CLI for AI agents (cc/cx/ag + API peers) to ask each other with durable evidence and quota-aware routing. Principles: PeerHub gives facts, the AI decides; very simple, additive; `peerhub monitor` = human screen, AIs use guide/explain/diag at minimum tokens. Agreed do-not-build list (absence is not a penalty): cross-caller quota lock or scheduler, auto-stop on throttle, CLI-enforced workspace isolation, a keyword-search flag for explain, a separate `batch status` command.
Previous independent scores (same rubric): round 3 parallel 87.7, AI 89, human 86, overall 87.6; round 4 parallel 92.6 (A 93, B 95, C 91, D 90.3, E 94.3, F 92.3), AI 93, human 91, overall 92.2.
Round 4 gaps and what changed since: diag --plan text now ends with a `profiles:` line (which profiles spend each pool) before the load line; `batch run --dry-run` shows PROFILE and WORKSPACE columns, warns when active-now plus concurrent tasks exceed the default limit 4 and when free RAM is below 6 GB, ag tasks count as writable in the overlap check; `batch run --help` says a reused --batch-id replays settled tasks (reconcile uncertain rows first); the next-command lines in failed/uncertain rows carry the selected store (--db); README: AI sessions are pointed to diag --plan (the monitor snapshot example is labeled for people), the upgrade requires a backup command that exits 0 with a new modified time before install, `explain <topic or keywords>`; docs/batch.md no longer names internal constants; explain parallel-quota explains in flight and unmapped. The update --plan output below is the real output (shown in its blocked form when asks are running, with the restore line).
Give: (1) axes A-F for parallel execution, 0-100 each + mean; (2) AI scenarios mean and human scenarios mean, each with the weakest scenario; (3) overall mean of the three; (4) for every number under 95: the single concrete missing thing and a minimal additive fix (or "do not build: reason"). If an axis is capped only by a do-not-build item, score it as if that item were out of scope.
```

## Replies (verbatim apart from local paths)

batch rescore4 max-parallel 3
r4-cx                    cx       COMPLETED
r4-neon                  ag       COMPLETED
r4-argon                 ag       COMPLETED
--- r4-cx reply ---
I’ll check the current checkout against the supplied output and score the three areas using concrete workflow gaps.
The checkout confirms the new dry-run warnings and selected-store recovery commands. The first 53 offline checks passed. I’m checking what the plan shows when quota evidence is stale and what happens on a second upgrade.
**Overall 94.7/100** against the 95 release bar. The highest-priority fix is to make the printed restore command rotate `workspace.generation` as the README promises. The current command copies the database and runs a read-only health check. [Upgrade command](/<repo>/peerhub/cli/update.py:282)

| Parallel axis | Score | Evidence and minimal fix |
|---|---:|---|
| A. Information | 92 | Text now maps profiles to pools, but a sibling’s reason can hide a pool’s stale flag. I reproduced a stale `G-5H` displayed as `hold` with `G-7D: on pace`. Render each row’s freshness independently of its action reason. [Plan logic](/<repo>/peerhub/cli/view.py:607) |
| B. Hints | 97 | Help and guide lead directly to the compact plan and quota lesson. |
| C. Concurrency | 94 | Dry-run warns on overlap, projected load and low RAM. An unreadable active count produces no load notice. Print `active now ?` when sampling fails. [Batch logic](/<repo>/peerhub/extensions/batch.py:246) |
| D. Failure and recovery | 96 | Per-task outcomes, replay guidance and store-qualified diagnostic commands support partial-batch recovery. |
| E. Cost | 96 | Compact planning, refresh without model turns, fresh votes and opt-in full batch replies limit spend. |
| F. Docs and tests | 94 | The batch warning summary omits the new default-load and RAM checks. Update that paragraph to match dry-run help. [Batch docs](/<repo>/docs/batch.md:16) |

**Parallel mean 94.8.** Fix A first so the text plan reliably signals when to refresh.

**AI scenarios mean 95.5.** Weakest is **A3, parallel wave planning (92)**. The per-row freshness fix in A makes the compact text path sufficient in the reproduced case.

**Human scenarios mean 93.7.** Weakest is **H5, upgrade and restore (84)**. Generate a command that uses the existing validated, generation-rotating restore logic for the selected database. [Core restore](/<repo>/peerhub/core/workspace.py:120)

Reviewed `6f25bc8d`. Scenario scores are estimates. 153 targeted offline tests passed. `tmp_path` and live tests were not run. Agreed exclusions carried no penalty. No edits.
--- r4-neon reply ---
### 1. Parallel Execution (A–F)
- **A. Quota & pool visibility — 96**: `diag --plan` adds `profiles:` before `load:`; `in flight`/`unmapped` documented. (Cross-caller quota lock: do-not-build, excluded.)
- **B. Token efficiency & AI/human split — 96**: `guide` (~400t) + `diag --plan` (~350t) stay lean; README/`--help` separate AI from `monitor`; internal constants removed from `docs/batch.md`.
- **C. Dry-run & preflight safety — 94**: Adds `PROFILE`, `WORKSPACE`, `active_asks_default`, `free_ram`, and `ag` writable overlap, but omits resolved default profiles.
- **D. Failure, replay & uncertain recovery — 93**: `--help` notes replay/reconcile and next-commands carry `--db`, but dry-run hides prior batch state.
- **E. Guidance & multi-wave alignment — 95**: `guide`, `explain parallel-quota`, and `docs/batch.md` H–J align on wave sizing, refresh, and recovery.
- **F. Runtime load & concurrent safety — 95**: Active asks, per-peer slots, RAM, in-flight pools, and `--db` propagation are solid; quota lock, auto-stop on throttle, and enforced isolation are do-not-build (excluded).
- **Parallel mean: 94.8**

### 2. Scenarios
- **AI mean: 94.5** — Weakest: **Replaying a partial batch with uncertain rows** (~92).
- **Human mean: 92.5** — Weakest: **Repeat upgrade & restore (`update --plan` / README Upgrade)** (~89).

### 3. Overall Mean
- **Overall: 93.9** (mean of 94.8, 94.5, 92.5; R4 was 92.2).

### 4. Missing Items & Minimal Additive Fixes (< 95)
- **C (94)**: `batch run --dry-run` prints `-` when `profile` is omitted, hiding which profile/pool will spend. **Fix**: Resolve and print the effective default profile in `PROFILE` (e.g. `cx.effort (default)`).
- **D (93) & AI mean (94.5)**: `--dry-run` on a reused `--batch-id` does not surface existing settled/uncertain rows (needed since `batch status` is do-not-build). **Fix**: Add an `uncertain_replay` warning and prior settled/uncertain count to `--dry-run`.
- **Human mean (92.5)**: `update --plan` runs `dest.touch(exist_ok=False)` on a fixed `.pre-update.bak` filename, crashing on any repeat upgrade (while README explicitly expects prior backups). **Fix**: Use a timestamped backup filename (e.g. `.pre-update-YYYYMMDD-HHMMSS.bak`) in `update --plan`.
- **Parallel mean (94.8) & Overall (93.9)**: Aggregate of C, D, and Human above; applying those three additive fixes clears 95 across the board.

--- r4-argon reply ---
### 1. Parallel Execution Axes (A–F)
* **A (Quota & Pool Discovery): 95** — `diag --plan` adds the `profiles:` mapping line directly above `load:`, closing the profile-to-pool gap in ~350 tokens.
* **B (Batch Dry-Run & Validation): 96** — `--dry-run` now displays `PROFILE` and `WORKSPACE`, warns on `active_asks_default` (>4) and `free_ram` (<6 GB), and treats `ag` as writable in overlap checks.
* **C (Concurrency & Wave Sizing): 94** — In-flight and `unmapped` spend are explained well, and default/ceiling checks work.
* **D (Workspace & Peer Safety): 93** — `ag` overlap detection in `--dry-run` is a strong fix; isolation remains advisory by design.
* **E (Failure, Replay & Uncertainty): 96** — `--batch-id` replay semantics and `--db` propagation in failed/uncertain next-command lines are clear and self-contained.
* **F (Token Efficiency & AI Ergonomics): 95** — AI vs. human separation (`diag --plan` vs. `monitor`) and `explain <topic or keywords>` are clean and tight.
* **Parallel Mean: 94.8**

### 2. Scenario Means
* **AI Scenarios Mean: 95.0** — *Weakest scenario:* Multi-pool profile spend during wave planning (**94**). In `diag --plan`, `cc.fable` appears under both `C-7D` and `F-7D` in the `profiles:` line, which doesn't indicate whether a profile drains both pools simultaneously or has a binding sub-pool without reading `--json`.
* **Human Scenarios Mean: 93.5** — *Weakest scenario:* Safe store backup on second upgrade (**92**). `update --plan` prints a backup snippet with `dest.touch(exist_ok=False)` onto a static `.pre-update.bak` filename; if a previous backup exists, the command fails with `FileExistsError`, conflicting with the README note that an old backup left from an earlier upgrade must get a new modified time.

### 3. Overall Mean
* **Overall Mean: 94.4**

### 4. Items Under 95: Missing Thing & Minimal Additive Fix
* **Axis C (94) & AI Weakest Scenario (94):**
  * *Missing:* `diag --plan` text doesn't clarify when a profile listed in multiple pools (e.g., `cc.fable` in `C-7D` and `F-7D`, or `ag.standard` in `G-5H` and `G-7D`) spends from both vs. is gated by the stricter one.
  * *Minimal fix:* Add a 5-word note to `explain parallel-quota` (and `docs/batch.md` J.1): *"A profile listed under multiple pools spends all of them (use the worst ACTION/PACE)."*
* **Axis D (93):**
  * *Missing:* When `batch run --dry-run` prints `PROFILE` as `-` (omitted in manifest), the caller cannot see which default profile/pool will actually be spent or if it will be `unmapped`.
  * *Minimal fix:* In `batch run --dry-run`, display the resolved default profile (or `(default)`) instead of `-`, or note in `batch run --help` that omitting `profile` leaves spend `unmapped` in `diag --plan`.
* **Human Mean (93.5) & Weakest Scenario (92):**
  * *Missing:* `update --plan` backup one-liner uses `dest.touch(exist_ok=False)` on a fixed `.pre-update.bak` path, which crashes on any second upgrade unless the user manually deletes the old `.bak` file first.
  * *Minimal fix:* In the `update --plan` backup command, replace `dest.touch(exist_ok=False)` with `dest.unlink(missing_ok=True)` before `sqlite3.connect(dest)` so re-running the backup overwrites the old `.bak` with a fresh mtime and exits 0.
