# Round 1: parallel execution (axes A-F)

Peers: cx.effort (read the repo), ag.neon and ag.effort (material inline). Run 2026-11-22 on the branch before the load-line and audit fixes. The two ag replies are in this file; their first attempt returned empty (denied_empty) because the prompt allowed file reads, and was rerun with `NO TOOLS`.

## Prompt (instruction part; the attached material is the CLI output of the commit named above)

```text
READ-ONLY review and SCORE. No edits. Reply <=450 words.
Question: have we considered PARALLEL execution of AI tasks over quota pools sufficiently in PeerHub (CLI that routes asks to peers cc/cx/ag + API peers with durable evidence)? Owner principles: PeerHub gives facts, the AI decides; very simple, additive; monitor = human screen, AIs use `peerhub diag`; minimum tokens.
Score 0-100 per axis with one line of evidence each, then the total (mean) and the TOP 5 gaps ranked, each with a minimal additive fix (or "do not build: reason"):
A. Information an AI gets to size a wave (quota, pace, load, RAM, freshness)
B. Hints/discoverability at minimum tokens (guide, explain, AGENTS)
C. Correctness under concurrency (shared workspaces, overlapping writers, store/lease races, quota consumed by in-flight asks not yet visible)
D. Failure and recovery (uncertain results, timeouts, partial batch, retry rules, quota exhausted mid-wave)
E. Cost control (resumed contexts, fresh votes, tiers, duplicate work, model diversity)
F. Docs/tests that keep the above true (one source, derived tests)
Material below: the guide, the topic, the load line, docs/batch.md sections H-J, a `diag --plan` sample. You may read the repo (peerhub/extensions/batch.py, diag_running.py, core/load_limits.py) to verify claims.
```

## Replies (verbatim apart from local paths)

batch parscore max-parallel 3
ps-cx                    cx       COMPLETED
ps-neon                  ag       UNVERIFIED denied_empty
ps-effort                ag       UNVERIFIED denied_empty
--- ps-cx reply ---
I’ll check the batch, diagnostics, and store paths against the supplied guidance, then score the six axes and rank the gaps.
The runner treats `--max-parallel` as a worker cap and keeps pulling from the manifest. I’ll check how the docs handle that distinction and what evidence an AI gets if quota runs out mid-batch.
**76/100 mean.** PeerHub gives one AI enough evidence to plan a conservative wave. Coordination between callers and quota changes during a batch need more work. In the supplied snapshot, the default wave size is zero because five asks are already active against the default limit of four.

| Axis | Score | Evidence |
|---|---:|---|
| A. Wave sizing | 74 | `diag --plan` shows quota, pace, resets, lease load and RAM together. Freshness and `fast` are harder to judge, and API balances are unknown. |
| B. Discoverability | 88 | The guide, explain topic and workspace `AGENTS.md` give a short route to `diag`. README and batch §H still direct AIs to monitor. |
| C. Concurrency | 60 | SQLite serializes writes, fenced claims protect same-stream delivery, and validation rejects writer overlap within a manifest. Independent callers can still overlap or size waves from the same quota snapshot. |
| D. Recovery | 72 | Stable IDs replay settled asks, and timeout and cancellation preserve uncertainty. Queued tasks keep launching after quota refusal. |
| E. Cost | 86 | Fresh votes, tier guidance, distinct models and stable-ID replay curb context and duplicate spend. Distinct batch IDs can still duplicate work. |
| F. Docs/tests | 76 | Tests derive limit and ordering expectations and exercise claim races. The prose still duplicates decision rules, and tests mostly check fragments of the advice. |

**Top five gaps**

1. **A wave can silently become a queue.** Document one manifest per wave with at most `n` tasks. Retain provider refusal reason and reset time when supplied in batch JSON, so the caller can defer affected work. Replay settled tasks with the same IDs and reconcile uncertain effects before retrying.
2. **Load can look safer than it is.** Count all current-store leases with an aggregate query and show `unknown` on read failure. Label the scope and show in-flight counts by pool where bindings identify it. Treat those counts as pending spend of unknown size.
3. **Workspace protection ends at the manifest.** Put one rule in the topic and §J covering manual asks and other batches too. Each concurrent writer gets a separate worktree or private directory. Serialize writes when isolation is unavailable.
4. **Pool eligibility takes guesswork.** Expose observation age, `fast`, and existing profile-to-pool bindings in plan JSON. Keep API credits explicitly unknown and use them only within a caller-supplied budget.
5. **The advice can drift.** Make §H, README and `AGENTS.md` point to the explain recipe. Test emitted guidance against one fixture with stale quota and saturated load.

Static review only. No edits or test runs.

(ag replies, second run)

batch scenag max-parallel 2
psag-neon                ag       COMPLETED
psag-effort              ag       COMPLETED
scag-neon                ag       COMPLETED
scag-argon               ag       COMPLETED
--- psag-neon reply ---
**Verdict:** Solid facts-only base, but not yet sufficient for safe parallel waves—`diag --plan` omits key facts, partial-batch recovery is unspecified, and docs already drift.

### Scores
- **A. Wave-sizing facts — 72/100:** `diag --plan` + `load:` (~350t) gives pace, slots, and RAM, but omits `C-5H`/`X-5H` ("5H gates"), age, `fast`, pool→profile mapping, and per-pool active burn.
- **B. Hints & discoverability — 80/100:** `guide` and `explain parallel-quota` are terse, but leak monitor-only terms (`fast`, `AGE`) and `batch run --help` omits `diag --plan`.
- **C. Concurrency correctness — 55/100:** Live `load:` and workspace advice help, but `--dry-run` lacks shared-writer/`ag`-cwd/duplicate-`--batch-id` checks; unreflected in-flight burn and concurrent DB writes go unaddressed.
- **D. Failure & recovery — 52/100:** Single-ask uncertain/timeout rules are crisp, but mid-wave 429/exhaustion, partial-batch states, and safe non-blind re-runs are unspecified.
- **E. Cost control — 84/100:** Strong tier, fresh-context, 300t-vote, diversity, and 0-token-refresh rules; minor gaps on `ag.neon` drift and excluding paid `ca/oa/ga` from wave overflow.
- **F. Docs & derived tests — 48/100:** Live drift (H `LEFT%/days` vs J `PACE`; `fast`/5H absent in `diag --plan`; `ag.neon` and review-tier conflicts) with no single-source contract tests.
- **Total (mean): 65.2/100**

### Top 5 Gaps (Ranked)
1. **Missing wave facts in `diag --plan` (A, C):** No `C-5H`/`X-5H` gate status, observation age, pool→profile mapping (`F-7D` vs `C-7D`), or per-pool active counts (`cx 5/3`).
   - *Fix:* Add `AGE`, print 5H when low/gating, append per-pool counts to `load:` (`X-7D:5`), and add a one-line pool→profiles footer (~+40t).
2. **Undefined partial-batch & mid-wave exhaustion recovery (D):** No contract for mid-wave 429s or resuming without blind-retrying uncertain tasks.
   - *Fix:* Emit per-task status (`ok|failed|uncertain|unstarted`) in `batch run --json`, halt unstarted tasks on a quota-exhausted peer, and document "new `--batch-id` for failed/unstarted only." *(Do not build auto-retry/reroute: violates AI-decides.)*
3. **No concurrency checks in `--dry-run` (C):** Shared writable `cwd`, non-empty `ag` dirs, or colliding `--batch-id`s can corrupt state.
   - *Fix:* Flag duplicate writable `cwd`, non-empty `ag` `cwd`, and existing `--batch-id` in `--dry-run`, plus tasks-per-pool beside `load:`. *(Do not build a throttler.)*
4. **Doc/output drift across `guide`, `explain`, `batch.md`, `diag` (F, B):** Conflicting ranking rules (H vs J), phantom `fast`, and mismatched `ag.neon`/review tiers.
   - *Fix:* Mark H human/`monitor`-only, drop unprinted terms from AI docs, align tables, and add one contract test binding constants/docs to `diag --plan`.
5. **Unbounded API-peer overflow (E):** `parallel-quota` ignores credit-billed `ca/oa/ga`.
   - *Fix:* Add one line in `explain parallel-quota`: "Never overflow waves to `ca/oa/ga` without budget." *(Do not build API credit metering yet.)*

--- psag-effort reply ---
### Scores by Axis

* **A. Wave Sizing Info: 85/100**  
  `diag --plan` delivers usable pools, 7D PACE, action advice, and a live sampled load line (active slots per peer, RAM) within ~350 tokens.
* **B. Hints & Discoverability: 80/100**  
  Pointers in the guide and `explain parallel-quota` clearly redirect AIs away from human screens to `diag --plan` at minimal token cost.
* **C. Concurrency Correctness: 50/100**  
  Limits and workspace separation are purely advisory; in-flight asks do not reserve quota, leaving concurrent workers blind to pending burn.
* **D. Failure & Recovery: 55/100**  
  Strong single-stream timeout/uncertain heuristics exist, but `batch run` defines no mid-wave quota tripwire or partial-batch resumption mechanics.
* **E. Cost Control: 90/100**  
  Exemplary heuristics enforce fresh contexts over stream reuse, lowest-tier mapping, and mandatory cross-model diversity on multi-peer votes.
* **F. Docs & Derived Tests: 60/100**  
  Rules are manually duplicated across `docs/batch.md` and `explain parallel-quota`, risking drift without automated contract checks shown.

**Total (Mean): 70 / 100**

---

### Top 5 Gaps (Ranked)

1. **In-flight quota invisibility (Axis C)**  
   *Gap:* Launching concurrent tasks relies on stale pool snapshots; in-flight asks burn quota without updating `LEFT` or `ACTION`.  
   *Minimal additive fix:* In `diag --plan`, show an `IN_FLIGHT` count per pool and temporarily adjust `ACTION` to `hold` if active asks match peer slot caps.

2. **Mid-wave quota exhaustion & batch resume (Axis D)**  
   *Gap:* A batch executing parallel asks cannot cleanly stop or resume if a pool drops to `blocked` mid-run.  
   *Minimal additive fix:* Make `peerhub batch run` record task execution status to `.peerhub/core.db`, enabling safe `--resume` of unstarted tasks.

3. **Workspace collisions across concurrent writers (Axis C)**  
   *Gap:* Advice mandates separate workspaces, but overlapping paths are not checked.  
   *Fix (Do not build engine locks):* Do not build complex file leases. Add a minimal validation step in `batch run --dry-run` that rejects manifests with duplicate workspace directories.

4. **Stale evidence handling in automated planning (Axis A & B)**  
   *Gap:* AIs must inspect `(stale)` flags manually and remember to execute a separate refresh.  
   *Minimal additive fix:* Add `--refresh` flag to `peerhub diag --plan` (e.g., `peerhub diag --plan --refresh`) so an agent can sample fresh data in one roundtrip.

5. **Doc duplication and drift (Axis F)**  
   *Gap:* `docs/batch.md` section J and `explain parallel-quota` duplicate default plan rules.  
   *Minimal additive fix:* Source the `parallel-quota` topic directly from `docs/batch.md` (or add a pytest asserting text equivalence).


