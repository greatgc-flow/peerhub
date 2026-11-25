# Round 1: AI and human usage scenarios (A1-A6, H1-H6)

Peers: cx.deepthink, ag.neon, ag.argon. Same date and commit state as round 1 parallel.

## Prompt (instruction part; the attached material is the CLI output of the commit named above)

```text
READ-ONLY review and SCORE. No edits. Reply <=500 words.
PeerHub = CLI that lets AI agents (cc/cx/ag + API peers) ask each other, with durable evidence and quota-aware routing. Owner principles: PeerHub gives facts, the AI decides; very simple, additive; `peerhub monitor` = HUMAN screen, AIs use `peerhub diag` and `peerhub guide`/`explain` at minimum tokens.
Task: score usage SCENARIOS 0-100 each (one line of evidence: the exact commands, how many calls, approx tokens/lines, what is unclear), then give the mean per audience, and the TOP 5 friction points with a minimal additive fix (or "do not build: reason").
AI scenarios: A1 first contact (what to read, cost); A2 one ask to a peer and reading the result; A3 parallel wave over quota pools; A4 independent 3-peer vote; A5 uncertain/timeout recovery; A6 learning a past lesson by keyword.
HUMAN scenarios: H1 glance at quota/what is running now; H2 live watch (refresh, no freezing); H3 diagnose why an ask failed; H4 first-time setup and sign-in; H5 upgrade/restore safely; H6 report a bug.
Material: `peerhub --help`, `peerhub guide`, `peerhub explain lessons` (index), `peerhub diag --plan`, `peerhub monitor` compact sample, README top. You may read the repo to verify.
```

## Replies (verbatim apart from local paths)

batch scencx max-parallel 1
sc-cx                    cx       COMPLETED
--- sc-cx reply ---
I’ll use the supplied outputs as the baseline and check the repo for exact commands, output costs, and monitor refresh behavior.
The checkout confirms that continuous terminal watch refreshes in the background. It also shows a token-saving shortcut for agents: `peerhub explain` accepts keywords directly. I’ll check how the recovery and upgrade paths handle their less obvious branches.
**Mean scores — AI 84.3/100 · Human 77.5/100.** Counts are shell calls and output sizes are approximate. 46 offline checks passed, including simulated slow refresh.

### AI

- **A1 First contact · 88** — `peerhub guide` (1 call, 11 lines/~400 tokens, offline). `peerhub --help` adds 31 lines. The guide lacks a first-ask example.
- **A2 Ask and read · 92** — `peerhub ask cx "Reply OK" --profile cx.standard --stream smoke --request-id smoke1` (1 call, reply + 1–2 lines). Omitting the stream can reuse context.
- **A3 Quota wave · 74** — `peerhub diag --plan` (1 call, 15 lines). Sample is full at the 6-ask ceiling despite “plan OK.” With room, run `peerhub batch run tasks.json --batch-id wave1 --max-parallel 3` first with `--dry-run`, then without, then repeat the plan (4 calls, ~40 lines).
- **A4 Independent votes · 80** — `peerhub explain independent-peer-votes` → `peerhub batch run votes.json --batch-id vote1 --max-parallel 3 --responses` (2 calls, 4 lesson lines plus report, three replies requested at ≤300 tokens each). Manifest and ag workspace setup are manual.
- **A5 Timeout recovery · 78** — `peerhub explain ask_uncertain` → `peerhub diag health --stream review` → `git status --short` (3 calls, 13 guidance lines plus diagnostics). Stream health omits live claims. A caller timeout can leave the ask running.
- **A6 Keyword lesson · 94** — `peerhub explain ag silence` (1 call, 4 lines/~100 tokens). Broad matches need a second call. The 40-line index obscures direct keyword lookup.

### Human

- **H1 Glance · 89** — `peerhub monitor --snapshot` (1 call, ~24 sample lines). Quota and six running asks are visible, with one running row hidden.
- **H2 Live watch · 91** — `peerhub monitor` (1 call, ~24 lines/frame). Terminal heartbeat ticks during background probes. The default 60-second snapshot cadence can make activity look stale.
- **H3 Failed ask · 72** — `peerhub monitor --snapshot --expand --group activity` → `peerhub explain ask_denied_empty` (2 calls, variable rows + 10 lines). The user must map “empty” and “denied” to the guidance code.
- **H4 Setup/sign-in · 67** — `pipx install peerhub` → vendor sign-in → `claude auth status` → `peerhub --db .peerhub/core.db ask cc "Reply OK"` (3 calls plus vendor setup, variable output). Explicit first-store selection is buried.
- **H5 Upgrade/restore · 64** — `peerhub update --plan` → `peerhub explain store-and-upgrade` (2 calls, ~16 lines, then four generated upgrade commands). The generated backup lacks a concrete restore recipe.
- **H6 Report bug · 82** — `peerhub explain report-issues` → `peerhub --version` → `gh issue list -R greatgc-flow/peerhub --search timeout` (3 calls plus form, 5 lines plus matches). Diagnostic redaction is manual.

### Top five fixes

1. **H5** Link the upgrade plan from README and document offline restore and verification.
2. **H4** Put an explicit `--db` first-ask example under setup.
3. **H3** Show the guidance code beside the expanded failure reason.
4. **A3** Show free slots at the default limit and ceiling on the load line.
5. **A5** Distinguish caller timeout from uncertain execution. For caller timeout, point to `peerhub diag running` and same-ID replay after settlement.

--- scag-neon reply ---
### AI Scenarios (mean: 74.2)
- **A1 First contact (82):** `--help` + `guide` + `profile list` (3 calls, ~45 lines/~700 tok); `guide` is dense, but `--help` doesn't direct AIs to `guide` + `diag --plan`.
- **A2 One ask & read result (66):** `ask --help`/`explain ask-basics` + `profile list` + `ask` + read receipt (3–4 calls, ~50+ lines); `guide` omits `ask` flags and reply-fetch command.
- **A3 Parallel wave (74):** `diag --plan` (15 lines/~280 tok) + `explain parallel-quota` + `batch --help` + `batch run --dry-run` + run (4–5 calls); `diag --plan` lacks pool→profile map (`monitor` has it) and `guide` omits `tasks.json` schema.
- **A4 3-peer vote (76):** `guide` + `explain independent-peer-votes` + `diag --plan` + `batch`/`ask` (4–6 calls, ~60+ lines); rules are clear, but flag for forcing FRESH runs isn't in `guide`.
- **A5 Uncertain/timeout recovery (75):** `guide` + `explain writes-and-uncertain` + `diag`/`outcome` (2–4 calls, ~35+ lines); no-retry rule is clear, but exact inspect-stream and `outcome` commands are missing.
- **A6 Lesson by keyword (72):** `explain lessons` (41 lines/~400 tok) + `explain <topic>` (2 calls); no keyword filter, so index cost scales linearly across overlapping topics (`ag-*`, `batch-*`).

### Human Scenarios (mean: 74.5)
- **H1 Glance quota/running (88):** `peerhub monitor` (1 call, ~25 lines); high signal density, minor jargon (`v-7`/`^+22`, `F u1 c0%`, `a1`/`a3`).
- **H2 Live watch (65):** `monitor` + `monitor --help`/`explain monitor-view` (1–2 calls, ~30 lines); `quota age 45s` hints non-blocking reads, but watch/refresh flags and keys aren't in `--help` or footer.
- **H3 Diagnose failed ask (74):** `monitor` + `explain <code>` + inspect (2–3 calls, ~40 lines); inline `(denied: read_file)` helps, but no drill-down command for `parscore/ps-effort` or `a1`.
- **H4 Setup & sign-in (78):** README/`explain setup` + vendor CLIs + `monitor --expand` (3+ calls, ~40 lines); clear table/errors, but README jumps to "Core recipe" over first `ask`, and login status hides behind `--expand`.
- **H5 Upgrade/restore (70):** `update` + `explain store-and-upgrade` (2 calls, ~30 lines); read-only `update` is safe, but `DB:env`/restore hazards aren't linked in README or `--help`.
- **H6 Report a bug (72):** `explain report-issues` + `--version` (2 calls, ~20 lines); buried in `guide`/`explain lessons`, absent from `--help` and README top.

### Top 5 Friction Points & Minimal Fixes
1. **No `ask`/inspect example in `guide` & README (A2, A4, H4):** Add 2 lines to `guide` (minimal `ask` + inspect-reply) and a 1-command `ask` quickstart above README's Core recipe.
2. **No pool→profile map in `diag --plan` (A3, A4):** Add a `PROFILES` column or 1-line `suggest:` (like `monitor`) to save a `profile list` call.
3. **Opaque `monitor` codes & watch/drill-down gaps (H1–H3):** Add a 1-line footer listing refresh/expand flags and the inspect command for alerts/failures.
4. **Unfiltered lesson index (A6):** Add `peerhub explain --search <kw>` matching topic/title lines (do not build semantic search: unnecessary complexity/tokens).
5. **Unlinked entrypoints in `--help` (A1, H5, H6):** Add a 2-line epilog: `AI: guide, diag --plan | Human: monitor | Upgrade/bugs: update, explain store-and-upgrade, report-issues` (do not build auto-install/upgrade: violates vendor/safety principles).

--- scag-argon reply ---
### AI Scenarios (Mean: 76.7)
- **A1 First contact: 92** — `peerhub guide` + `peerhub diag --plan`; 2 calls, ~480 tokens / 29 lines; unclear: exact `peerhub ask` flag syntax isn't in `guide`.
- **A2 One ask & reading result: 55** — `peerhub ask ...` (or `peerhub ask -h` / `peerhub explain ask-basics` first); 2–3 calls, ~250 tokens / 20 lines; unclear: `guide` omits the `ask` command template (how target peer/profile, prompt, `--stream`, and `--writable` are passed, and whether `ask` prints the reply directly).
- **A3 Parallel wave over quota pools: 72** — `peerhub diag --plan` + `peerhub batch run tasks.json --batch-id <id> --max-parallel 3 [--dry-run]`; 2–3 calls, ~400 tokens / 25 lines; unclear: `tasks.json` schema requires calling `explain parallel-quota` or `batch -h`.
- **A4 Independent 3-peer vote: 78** — `peerhub diag --plan` + `peerhub batch run ...` (or 3× `peerhub ask`); 2–4 calls, ~350 tokens / 20 lines; unclear: `guide` gives the rubric (`VERDICT / BLOCKERS / NITS`, 3 models, fresh), but not the exact flag/schema to force fresh runs when a stream name is given.
- **A5 Uncertain/timeout recovery: 75** — `git status`/`diff` + new `peerhub ask --stream <new>`; 2 calls, ~150 tokens / 10 lines; unclear: how to inspect a timed-out ask's stored record or append an `outcome` correction without guessing subcommands.
- **A6 Learning a past lesson by keyword: 88** — `peerhub explain lessons` + `peerhub explain <topic>`; 2 calls, ~550 tokens / 55 lines; unclear: whether `explain` supports keyword search or requires scanning all 38 index lines first.

### HUMAN Scenarios (Mean: 82.0)
- **H1 Glance at quota / running now: 96** — `peerhub monitor`; 1 call, ~300 tokens / 23 lines; unclear: minor codes (`F u1 - c0%`, `ROUND25`) need `explain monitor-view`.
- **H2 Live watch (refresh, no freezing): 72** — `peerhub monitor` (or `peerhub monitor -h`); 1–2 calls, ~300 tokens / 23 lines; unclear: top-level help/README don't show whether `monitor` loops continuously by default or requires `--watch`/`--interval`.
- **H3 Diagnose why an ask failed: 86** — `peerhub monitor` + `peerhub explain ag-recipe`; 2 calls, ~450 tokens / 40 lines; unclear: how to drill into the full error/session log of a specific failed ask from the `RECENT` row.
- **H4 First-time setup and sign-in: 95** — `pipx install peerhub`, vendor CLIs/env vars, `peerhub monitor --expand`; 2–3 calls, ~350 tokens / 35 lines (README / `explain setup`); unclear: nothing major, crisp prerequisites and failure messages.
- **H5 Upgrade/restore safely: 73** — `peerhub update` + `peerhub explain store-and-upgrade`; 2 calls, ~300 tokens / 25 lines; unclear: exact subcommands/flags of `peerhub update` and backup steps aren't in README top.
- **H6 Report a bug: 70** — `peerhub explain report-issues`; 1 call, ~150 tokens / 15 lines; unclear: invisible from README and `--help` unless the human checks `guide` or `explain lessons`.

### Top 5 Friction Points & Minimal Additive Fixes
1. **`guide` lacks a canonical `ask` syntax example (A2/A4):** Add 1 line to `peerhub guide`: `Ask: peerhub ask --peer cc.effort --stream task-1 --timeout-seconds 1800 "prompt" (ag adds --writable)`.
2. **`tasks.json` schema missing from `guide` (A3/A4):** Add 1 line to `peerhub guide` under `Batch:` showing a minimal inline single-item JSON example.
3. ** README jumps from Install to low-level Core plumbing (A1/H1/H2):** Replace or precede `## Core recipe` in README with a 4-line `## Quickstart` (`diag --plan`, `ask`, `monitor`).
4. **Unclear how to inspect a failed/uncertain ask's full record (A5/H3):** Add 1 recipe phrase to `guide` (`Uncertain:`) and `monitor` footer showing the exact `session` or `diag` command to inspect an ask by stream/id.
5. **Keyword search on `explain` (A6):** Do not build: `peerhub explain lessons` is 38 lines (~250 tokens); an AI scanning the full index in one call is cheaper and more reliable than guessing search keywords.

 top leads with low-level plumbing instead of `ask`/`monitor` (hurts A2/H1):** Replace or precede "Core recipe" (`record append`/`offset advance`) in README top with a 3-line "Quickstart" (`peerhub diag --plan`, `peerhub ask`, `peerhub monitor`).
4. **No keyword filter on `peerhub explain` (hurts A6):** *Do not build:* 39 one-line slugs (~350 tokens) fit easily in one AI read; adding search flags adds CLI surface for negligible token savings.
5. **Hidden watch/expand flags on `monitor` and `update` flow (hurts H2/H5):** Add a 1-line tip at the bottom of `peerhub monitor` default output (`tip: --expand for all running/accounts | --watch for live refresh`).


