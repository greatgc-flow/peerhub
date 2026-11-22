# Round 4: re-score after lanes C and D

Peers: cx.effort, ag.neon, ag.argon. Commit 08de1354. Result: parallel 92.6, AI 93.0, human 91.0, overall 92.2 (cx 92.4, neon 91.0, argon 93.2); the 95 bar was not met. Note: the `update --plan` section of the material was empty in this packet (other asks were running, so the plan printed its blocked form); two reviewers flagged it as unverified.

## Prompt (instruction part)

```text
Review and SCORE the CURRENT state. Reply <=380 words. NO edits. Be strict and evidence-based; do not inflate, do not deflate for taste.
PeerHub = CLI for AI agents (cc/cx/ag + API peers) to ask each other with durable evidence and quota-aware routing. Principles: PeerHub gives facts, the AI decides; very simple, additive; `peerhub monitor` = human screen, AIs use guide/explain/diag at minimum tokens. Agreed do-not-build list (absence is not a penalty): cross-caller quota lock or scheduler, auto-stop on throttle, CLI-enforced workspace isolation, a keyword-search flag for explain, a separate `batch status` command (the batch report already prints the facts and next commands).
Previous independent scores (same rubric): round 1 parallel 68, AI 79, human 76; round 2 parallel 76, AI 83, human 79; round 3 parallel 87.7 (A 90.7, B 92, C 83, D 81.7, E 91.7, F 86.7), AI 89, human 86, overall 87.6.
Round 3 gaps and what changed since: batch run --help example now valid (one prompt, or prompt_file alternative, warning kinds, --responses note); dry-run warnings compare what runs together (min(tasks, max-parallel)) plus sampled active asks; failed/uncertain batch rows print next commands (diag running, diag health --stream, the child explain command); diag --plan text shows (fast) and (gate: POOL) tags, compact JSON, `unmapped` count beside in-flight; update --plan prints the restore one-liner; README upgrade has Back up first (file exists check) before install; guide Quota line says stale: observation refresh and Uncertain says check diag running; explain parallel-quota has the throttle rule and the reconcile steps; docs/batch.md tops match the guide.
Give: (1) axes A-F for parallel execution, 0-100 each + mean; (2) AI scenarios mean and human scenarios mean, each with the weakest scenario; (3) overall mean of the three; (4) for every number under 95: the single concrete missing thing and a minimal additive fix (or "do not build: reason"). If an axis is capped only by a do-not-build item, score it as if that item were out of scope.
```

## Replies (verbatim apart from local paths)

batch rescore3 max-parallel 3
r3-cx                    cx       COMPLETED
r3-neon                  ag       COMPLETED
r3-argon                 ag       COMPLETED
--- r3-cx reply ---
I’ll check the current checkout against the supplied CLI output and look for the previous scoring rubric so the scores are comparable.
I found the original rubric and confirmed the new recovery hints are wired into the batch report. I’ll check whether those hints preserve the selected store and run the offline tests that fit this read-only review.
**Overall 92.4/100.** The three component means are parallel execution **92.2**, AI scenarios **93.0**, and human scenarios **92.0**. The agreed exclusions carry no penalty.

| Parallel axis | Score | Evidence and minimal fix |
|---|---:|---|
| A. Information | 92 | Text shows fast and gate tags plus in-flight load. Profile-to-pool mapping still requires JSON. Add a compact mapping footer to the text plan. |
| B. Hints | 96 | Help points to the guide and plan. The guide covers stale evidence, and the quota lesson gives throttle and reconciliation steps. |
| C. Concurrency | 91 | Dry-run correctly caps projected overlap. A split-peer wave taking load from three to five only prints `active now 3`. Add an advisory warning above the default of four. |
| D. Failure and recovery | 87 | Batch rows print diagnostic commands, but both omit `--db`. An explicitly selected batch store can therefore be missed during reconciliation. Include the resolved store in both commands. |
| E. Cost | 95 | Compact plan JSON, refresh without model tokens, fresh asks, and opt-in full replies limit unnecessary spend. |
| F. Docs and tests | 92 | The README still labels a monitor snapshot as an AI recipe. Replace that example with `peerhub diag --plan` and extend the existing guide-path test to cover it. |

**AI mean 93.0.** Weakest is **A5, uncertain or timeout recovery (86)**. The selected-store fix in D is also the highest-priority improvement for the parallel and overall means.

**Human mean 92.0.** Weakest is **H5, upgrade and restore (86)**. The restore one-liner names the selected store, but the README’s existence check can accept an old backup after the new backup command fails. Require a successful backup exit before installation.

Reviewed checkout `08de1354`. **97 targeted offline tests passed**. Six `tmp_path` cases and live workflows were **not run**. Scenario scores are review estimates. No edits.
--- r3-neon reply ---
### 1. Parallel Execution (A–F)
- **A (Quota & load facts):** 93
- **B (Guidance & consistency):** 94
- **C (Batch CLI & dry-run):** 89
- **D (Failure recovery & replay):** 91
- **E (Isolation & guardrails):** 93
- **F (Token & wave efficiency):** 92
- **Parallel mean:** **92.0** (R3: 87.7; do-not-build items excluded)

### 2. Scenario Means
- **AI scenarios mean:** **92.0** (R3: 89) — weakest: **parallel wave planning & dry-run verification**
- **Human scenarios mean:** **89.0** (R3: 86) — weakest: **safe upgrade & rollback**

### 3. Overall Mean
- **Overall:** **91.0** (mean of 92.0, 92.0, 89.0; R3: 87.6)

### 4. Gaps (<95) & Minimal Additive Fixes
- **A (93):** `diag --plan` text omits which `--profile` spends each pool (`F-7D`, `A-7D`), forcing `--json` or `profile list`. **Fix:** Add compact profile tags to `diag --plan` text.
- **B (94):** `README` says `peerhub explain <words>`, clashing with `guide` (`<topic>`) and the no-search-flag rule. **Fix:** Change README to `peerhub explain <topic>`.
- **C (89):** `batch run --dry-run` omits `PROFILE` and ignores default active-ask limit 4 (`active now 3` + `max_parallel=2` = 5 unwarned). **Fix:** Add `PROFILE` column and a >4 advisory warning.
- **D (91):** `batch run --help` omits `--batch-id` replay/reconcile rules. **Fix:** Add one line under `--batch-id`: reusing an ID replays settled tasks; reconcile uncertain rows first.
- **E (93):** Dry-run skips the free-RAM (<6 GB) limit and `ag` always-writable `workspace_overlap` check. **Fix:** Warn when free RAM < 6 GB and treat `ag` as writable.
- **F (92):** Pool-to-profile mapping is absent from `diag --plan` text, forcing an extra command/tokens. **Fix:** Same compact profile tag in `diag --plan` text (keeps planning to one ~350-token call).
- **AI (92.0):** Weakest scenario hits A+C (extra lookup for profiles; no `PROFILE`/default-4 dry-run check). **Fix:** Apply A and C fixes.
- **Human (89.0):** `=== update --plan (restore lines)` is empty in evidence, leaving CLI rollback unverified. **Fix:** Ensure `update --plan` always prints the restore one-liner (and drop internal `POOLS_USE_FIRST_ASCENDING` from `docs/batch.md`).

--- r3-argon reply ---
**(1) Parallel execution axes (0–100)**
- **A (Quota & pool facts): 94**
- **B (Sizing & load limits): 95** — dry-run checks `min(tasks, max-parallel)` + sampled active asks; JSON and text expose in-flight + `unmapped`.
- **C (Isolation & safety): 92** — CLI-enforced workspace isolation is do-not-build; dry-run flags `workspace_overlap`.
- **D (Failure, throttle & uncertain recovery): 93** — auto-stop on throttle and `batch status` are do-not-build; failed/uncertain rows print next commands (`diag running`, `diag health --stream`, child `explain`), and `explain parallel-quota` includes throttle/reconcile rules.
- **E (Token economy & AI ergonomics): 95** — ~350-token `diag --plan`, valid manifest in `--help`, aligned tops across docs.
- **F (Multi-wave & multi-caller coordination): 93** — cross-caller lock/scheduler is do-not-build.
- **Parallel mean: 93.7**

**(2) Scenario means & weakest scenarios**
- **AI scenarios mean: 94.0** (Weakest: **92** — *selecting a profile from `diag --plan` text without `--json` or extra `profile list` lookup*).
- **Human scenarios mean: 92.0** (Weakest: **90** — *verifying `update --plan` output before running an upgrade*).

**(3) Overall mean: 93.2**

**(4) Items under 95: single missing thing & minimal additive fix**
- **Axis A (94) & AI mean (94.0, weakest 92):** `diag --plan` text shows pool IDs (`3P-7D`, `A-7D`, `G-7D`, `C-7D`, `F-7D`, `X-7D`), while `--json` includes `profiles` per pool. In text mode, an AI must already know or call `peerhub profile list` to map pool codes (`A`, `G`, `3P`, `C`, `F`) to profile names (`ag.neon`, `ag.argon`, `cc.fable`, etc.).
  - *Fix:* Append compact profile names or short tags to `diag --plan` text pool rows (or a 1-line pool-to-profile footer legend).
- **Axis C (92):** `batch run --dry-run` prints `WRITABLE` (`True`/`False`) and warns on `workspace_overlap`, but the dry-run table omits the resolved `WORKSPACE` column, hiding which directory is targeted when no warning fires.
  - *Fix:* Add `WORKSPACE` (or basename/relative path) to the `batch run --dry-run` text table.
- **Axis D (93):** `batch run --help` documents manifest fields and warning kinds, but does not state that re-running the same `--batch-id` replays settled tasks (a critical fact after reconciling uncertain tasks).
  - *Fix:* Add one line to `batch run --help` footer: `Re-running an existing --batch-id replays settled tasks and retries only unfinished ones.`
- **Axis F (93):** `diag --plan` sample output omits `unmapped N` when 0 (`in flight G-5H 2...`), leaving the format undocumented in `explain parallel-quota` when unmapped asks appear.
  - *Fix:* Mention `unmapped` beside `in-flight spend` in `explain parallel-quota` (e.g., `(plus unmapped if pool unknown)`).
- **Human mean (92.0, weakest 90):** The `=== update --plan (restore lines)` evidence section in the prompt is empty, leaving the exact printed restore/backup one-liner unverified in this review packet.
  - *Fix:* Include the literal `peerhub update --plan` stdout snippet in the verification packet (or echo the exact `Copy-Item` / `cp` one-liner in `README.md` step 0).
