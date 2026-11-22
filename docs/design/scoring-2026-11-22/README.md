# Scoring parallel execution and usage scenarios (2026-11-22)

What was scored, by which criteria, by whom, what the scores were, and why they moved. The replies of every round are kept
verbatim in [`rounds/`](rounds/) so a score can be traced to its evidence. Local paths and session handles were removed.

Purpose: decide, with evidence, whether PeerHub is good enough at (1) spreading parallel AI work over the least quota and
(2) the day-to-day paths of AI agents and people. The release bar for 0.26.6 is **95 or more on the overall mean** (owner goal).

## Principles the scores are measured against

1. PeerHub gives the facts; the AI decides. A feature that makes the decision for the AI is a defect, not a gain.
2. Very simple, additive only; no JSON field is removed; no new store.
3. `peerhub monitor` is the human screen. AIs use `peerhub guide`, `peerhub explain` and `peerhub diag` at minimum tokens.
4. One named source per number, text and list; docs and tests derive from it (no hard-coding).

### Agreed "do not build" list (an absent item is not a penalty)

| Item | Reason |
|---|---|
| Cross-caller quota lock or scheduler | The CLI would decide for the AI and adds stale-lock failure modes. |
| Stop a batch automatically on a throttle | Same: the AI decides; the facts are the per-task refusal and the outcome counts. |
| CLI-enforced workspace isolation | Isolation belongs to the caller and the manifest; the CLI only warns (facts). |
| Keyword search flag for `explain` | The index is about 600 tokens; several matches already print as `id: title`. |

## Rubric 1: parallel execution (six axes, 0-100 each, mean of the six)

Anchors for every axis: **100** = nothing an AI needs is missing or wrong, proven by a test or a measured output;
**95** = one small, named, low-impact gap; **75** = works but an AI must guess or read code/docs outside the guide path;
**50** = a real hole an AI will hit in normal use; **25** = misleading or unsafe.

| Axis | Question | Evidence that earns 95+ |
|---|---|---|
| A. Information | Can an AI size a wave from one cheap call? | `diag --plan` gives per pool left %, reset, PACE, action, freshness, which profiles spend it, the sibling gates, plus load (active asks, per-peer slots, in flight per pool, free RAM); unreadable leases show `?`, never 0. |
| B. Hints | Does the guide path lead there at minimum tokens? | `peerhub guide` (about 400 tokens) names `diag --plan` and `explain parallel-quota`; the topic is facts first and a default plan the AI may change; `--help` has a start line. |
| C. Concurrency | Is a wave safe against overlap and overload, with the facts visible? | Writer overlap is rejected; `--dry-run` warns about workspace overlap, per-peer and total limits and active asks now; in-flight spend per pool is shown. |
| D. Failure and recovery | What happens on refusal, timeout, partial batch? | Per-task refusal does not stop the manifest (one wave per manifest); the final report counts outcomes (ok, failed, uncertain, quota refused); the same batch ID replays settled tasks; uncertain tasks are reconciled before retry. |
| E. Cost | Does the path avoid waste? | Plan call about 350 tokens, refresh costs no model tokens, fresh asks and different models for votes, no stream resume for new tasks, lowest tier that fits. |
| F. Docs and tests | Do docs, help and code agree and stay in agreement? | One source for limits and thresholds (`peerhub/core/load_limits.py`, `PLAN_*_PACE_PT`), tests derive docs from it, sections H and J of `docs/batch.md` say which ranking answers which question. |

## Rubric 2: usage scenarios (mean per audience)

Each scenario gets 0-100 with the exact commands, the number of calls and the approximate tokens or lines. Anchors as above.

| ID | AI scenario | ID | Human scenario |
|---|---|---|---|
| A1 | First contact (what to read, cost) | H1 | Glance at quota and what is running |
| A2 | One ask and reading the result | H2 | Live watch (refresh, no freezing) |
| A3 | Parallel wave over quota pools | H3 | Diagnose why an ask failed |
| A4 | Independent three-peer vote | H4 | First-time setup and sign-in |
| A5 | Uncertain or timeout recovery | H5 | Upgrade and restore safely |
| A6 | Learn a past lesson by keyword | H6 | Report a bug |

## Protocol

1. Three reviewers per round, independent and fresh (no stream reuse), run with `peerhub batch run ... --max-parallel 3`;
   the `ag` reviewers get the material inline in a private empty workspace with the line `NO TOOLS, do not open files`
   (a first attempt that allowed file reads returned empty replies and was rerun). The `cx` reviewer may read the repo.
2. Material: the real CLI output of the commit under review (`peerhub --help`, `guide`, `explain ...`, `diag --plan`,
   `batch run --help`, README and `docs/batch.md` sections). The instruction part of every prompt is in each round file.
3. Each reviewer returns axis scores and means, the weakest scenario per audience, and the missing item with a minimal
   additive fix or "do not build: reason". Replies are limited to 380-500 words.
4. Aggregation: per audience the mean of the three reviewers; overall = mean of the parallel mean, the AI mean and the human mean.
5. Caveats: scores are LLM judgments of the same artifacts by a small panel; the same models scored every round, so trends
   are more reliable than absolute values; `cx` reviews are static (some `tmp_path` tests could not run in its sandbox);
   scenario means are estimates from the supplied output, not measured sessions.

## Score history

| Round | Parallel (cx / neon / third) | AI scenarios (cx / neon / argon) | Human scenarios (cx / neon / argon) | Means: parallel, AI, human | Overall |
|---|---|---|---|---|---|
| 1 ([parallel](rounds/r1-parallel.md), [scenarios](rounds/r1-scenarios.md)) | 76 / 63.3 / 65 (third = ag.effort) | 84.3 / 75.7 / 76.7 | 77.5 / 72.5 / 78.3 | 68.1, 78.9, 76.1 | 74.4 |
| 2 ([rescore](rounds/r2-rescore.md)) | 72 / 74 / 81.7 (third = ag.argon) | 82 / 83 / 85 | 77 / 78 / 81 | 75.9, 83.3, 78.7 | 79.3 |
| 3 ([rescore](rounds/r3-rescore.md)) | 87 / 85.8 / 90.2 (third = ag.argon) | 88 / 88 / 91 | 85 / 85 / 88 | 87.7, 89, 86 | 87.6 |
| 4 ([rescore](rounds/r4-rescore.md)) | 92.2 / 92 / 93.7 (cx / neon / argon) | 93 / 92 / 94 | 92 / 89 / 92 | 92.6, 93.0, 91.0 | 92.2 |
| 5 ([rescore](rounds/r5-rescore.md)) | 94.8 / 94.8 / 94.8 (cx / neon / argon) | 95.5 / 94.5 / 95 | 93.7 / 92.5 / 93.5 | 94.8, 95.0, 93.2 | 94.3 |
| 6 | pending | | | | |

### Axis means for the parallel rubric

| Axis | Round 1 | Round 2 | Main evidence of the move |
|---|---:|---:|---|
| A. Information | 75 | 82 | `load:` line with active asks, per-peer slots and free RAM added to `diag --plan`. |
| B. Hints | 84 | 87 | guide names `diag --plan`; `explain parallel-quota` rewritten as facts first; `--help` start line. |
| C. Concurrency | 52 | 59 | live load facts; still no per-pool in-flight or dry-run warnings (added after round 2). |
| D. Failure and recovery | 57 | 69 | per-task refusal, one wave per manifest, batch-ID replay documented. |
| E. Cost | 85 | 87 | multi-match `explain` prints `id: title` (6 KB to 0.3 KB for `vote`); fresh votes; zero-token refresh. |
| F. Docs and tests | 55 | 70 | topic, guide and docs derive limits from one source; drift between H and J, README and CONTRIBUTING still open. |

## What each round found and what was done about it

Round 1 gaps (kept in issue 447): blind spots in `diag --plan` (age, fast, profile mapping, gates); no partial-batch
semantics; doc drift across guide, explain and `docs/batch.md`; stale readings between waves; no `ask` example; weak
upgrade and restore steps. Done before round 2: load line, `explain parallel-quota` (in-flight margin, per-task refusal,
one wave per manifest, replay, refresh between waves), multi-match `explain`, help start line, README and CONTRIBUTING fixes.

Round 2 gaps: the load line counted at most 20 lease rows and could show 0 when the read failed (fixed: limit 500, `?` on
failure, test first); `diag --plan` rows without profiles, age, fast, family and gates (added, JSON only); no dry-run
facts or outcome counts for `batch run` (added); no per-pool in-flight count (added); no first-ask and restore steps in
the README (added); `rest` started at PACE +10 although up to +20 is acceptable (owner decision, now `PLAN_REST_PACE_PT`);
sections H and J ranked differently without saying why (now explained).

## Re-running a round

Build the material with the commands listed in the "Material" step, put the instruction part of the latest round file in
front of it, write a batch manifest with one `cx` task (workspace = repo) and two `ag` tasks (each in its own private empty
directory), run `peerhub batch run manifest.json --batch-id ID --max-parallel 3 --dry-run`, then without `--dry-run` and with
`--responses`. Give each reviewer a different model. Save the replies as a new file in `rounds/` and add a row above.
