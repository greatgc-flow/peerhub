# Batch runs

Validate a flat manifest before executing asks (execution spends provider quota):

```powershell
peerhub batch run tasks.json --batch-id review-001 --max-parallel 3 --dry-run --json
peerhub batch run tasks.json --batch-id review-001 --max-parallel 3 --json
```

Use global `--db PATH` before `batch` to select a store. The parent resolves one
absolute store path for every worker, including tasks with different workspaces.
Dry runs validate without spawning workers or creating the store.
`--batch-id` and `--max-parallel` are required; parallelism is 1..16. The guide and every example
recommend 3 (`RECOMMENDED_MAX_PARALLEL` in `peerhub/extensions/batch.py`, the one constant).

The UTF-8 JSON manifest contains only `tasks` (1..256 entries):

```json
{"tasks":[{"task_id":"review","peer":"cx","prompt":"Review the code"}]}
```

Each task requires `task_id`, `peer`, and exactly one of `prompt` or `prompt_file`.
Optional keys: `workspace`, `profile`, `writable` (default false), `timeout_s`
(finite positive seconds, default 600). Unknown keys, including `stream`, fail.
IDs match `[A-Za-z0-9._-]{1,64}`; task IDs must be unique. Prompt files are UTF-8,
at most 1 MiB, and resolved relative to the manifest; workspaces must exist and
relative workspace paths use the current directory. Peers must be builtin aliases
or registered with a supported adapter. Prompts must contain non-whitespace text.

Every ag task is writable, even if the manifest says otherwise (#140).
If either task writes, equal or nested workspaces reject the entire batch.
Streams and request IDs derive from the batch/task IDs; no stream override exists.

Reports contain identities, sessions, elapsed seconds and statuses. Replies are
included only with `--responses` (see below):

| Status | Meaning |
| --- | --- |
| COMPLETED | Terminal delivery with a nonempty response and no error. |
| UNVERIFIED | Terminal delivery with an empty response. |
| FAILED | Positive evidence of no execution, or worker spawn failure. |
| UNCERTAIN | Launched worker without proof of a terminal or unexecuted result. |
| BLOCKED | Unlaunched cancellation, or earlier work/claims block this invocation. |

Exit 0 requires every task COMPLETED; exit 1 covers other statuses and invalid
manifests/options. Argparse syntax errors exit 2. Omit `--json` for a compact table.

`--responses` (opt-in, since 0.24.13) adds each COMPLETED task's reply: a `response`
field with `--json`, or a `--- TASK reply ---` block after the table. Failed, uncertain
and other non-COMPLETED tasks get no reply text; `--dry-run --responses` shows none.
A reply may itself contain a `--- ... reply ---` line, so use `--json` for machine parsing.
Without the flag the output is unchanged; read a reply with
`peerhub record read --stream <stream> --json`.

**Votes and reviews in one command**

- cc and cx tasks: `workspace` = the repository, no `writable` (read-only).
- ag task: `workspace` = a private EMPTY directory. ag tasks are always writable; an empty
  directory passes the overlapping-writers check and keeps ag away from the repo.
- Put every fact in each prompt; the empty-directory ag peer cannot read the repo.
- `peerhub batch run votes.json --batch-id vote-001 --max-parallel 3 --responses`
- Replies are in the report (before 0.24.13: `peerhub record read --stream <stream> --json`).
- Measured 2026-11-14: three peers answered in 11 s.

Use the following as prompt files for `prompt_file` in a batch manifest or
`--query-file` in an ask. Replace `<facts>` with the task and its supporting
material. Keep repository paths, excerpts, decisions and other task facts after
the fixed instructions.

### Inline ag recipe prompt

```text
Use only the inline facts below. Do not use tools. Complete the requested task and return the result.

<facts>
```

### ag file-read recipe prompt

```text
Use only your file read tool. If a file cannot be read, say so. Complete the task below.

<facts>
```

### Fresh-stream handoff prompt

```text
Continue the work using the decisions, open work, and file references below.

<facts>
```

### Batch task prompt

```text
Complete the task below. Report the result and any blockers.

<facts>
```

### Batch vote prompt

```text
Review the proposal and evidence below. Reply in at most 300 tokens.
VERDICT: AGREE or DISAGREE
BLOCKERS: file:line and the exact fix for each, or none
NITS: optional, one line each

<facts>
```

Ctrl+C, SIGTERM, or Windows Ctrl+Break stops launches and terminates worker trees.
Windows tree termination is guaranteed when confirmed; unconfirmed termination
reports `UNCERTAIN` with `kill_unconfirmed`. POSIX termination is best-effort;
detached providers may survive. Started cancelled tasks are UNCERTAIN; queued
tasks are BLOCKED. Inspect `peerhub diag health` and effects before continuing.

Resume by rerunning the same manifest and batch ID: settled asks replay their
durable results. Keep their prompt, peer, profile and workspace/write binding.
Never change batch ID to bypass uncertainty or a live claim; wait or reconcile.
Only flat tasks are supported: no dependencies, retries, quota gating, budgets or
failover. Parallelism is explicit because the caller controls resource use;
check available quota before execution. COMPLETED proves delivery, not task success.

## Choosing profiles and concurrency (manual)

Both rules are advice for the caller; PeerHub never selects a profile or throttles for you.
Tiers: `standard` = eco, `effort` = normal (default for implementation), `deepthink` = quality,
`<peer>.<top>` (`cc.fable`, `cx.astra`, `ag.argon`) = top: opt-in, never a default.

**H. Balancing rule**

1. Pick the lowest tier that meets the task class: bulk/mechanical = `standard`; docs = `standard`
   (formatting), `effort` (writing) or `deepthink` (technical claims); code = `effort`, escalate to
   `deepthink`; review = `deepthink` from an independent reviewer; design/debate = `deepthink` or an explicit top.
2. Drop candidates with any applicable pool that has stale evidence (regardless of `STATE` shown, since a stale pool can display `low` or `fast`; check `AGE` and the `PEERS` `(aged)` tag) or is `?`, `aged`, `reset`, or under 5% LEFT, and prefer candidates without a `fast` pool (see `peerhub monitor`).
3. Break ties across pool families by the highest `LEFT% / max(days_to_reset, 0.2)`.

The monitor's `suggest:` line lists the best eligible profile per tier. Among
eligible candidates, those without an applicable `fast` pool rank first, then
the existing worst-pool score, then profile name. A `|` separates
the best eligible alternative from a different peer in the same tier, for
example `eco=cc.standard|ag.standard`. With no eligible alternative, only the
primary appears. These suggestions do not execute asks or reroute work.

While 3P is low, explicitly use `ag.argon` only for tasks its measured capabilities support; otherwise defer.

For an AI planning a parallel wave, `peerhub explain parallel-quota` is the recipe (order by 7D PACE, size n by quota and load); this section is the manual reference.

**I. Concurrency** (counted across batches and manual asks together)

| Limit | Value |
| --- | --- |
| Active asks, default | 4 (a batch takes `--max-parallel 3`, one slot stays free for a manual ask; the option stays required) |
| Active asks, ceiling | 6 |
| Per peer | cc 3, cx 3, ag 1 (use 2 for ag only after seeing no G-5H spike) |
| pytest | one run at a time, never alongside more than 3 asks |
| Free RAM | keep at least 6 GB |

If a provider throttles, reduce concurrency; do not reroute the same work to another peer.

**J. Parallel work over the least quota** (advice only; PeerHub gives the facts and the AI decides; `peerhub explain parallel-quota` has the same default plan)

1. `peerhub diag --plan` (about 350 tokens; the AI query: pools from stored evidence, then the `load:` line with active asks, per-peer slots and free RAM, sampled now; `--json` has a `load` object). `peerhub monitor` is the human screen; AIs do not read it. Usable: `spend`, then `hold`; not usable: `rest`, `blocked`, `-` (n/a). Old or `(stale)` evidence: `peerhub observation refresh` (no model tokens), then read again.
2. Order usable pools by 7D PACE, lowest first (`POOLS_USE_FIRST_ASCENDING` in `peerhub/cli/monitor_view.py` flips it); rank a family by its 7D parent; 5H only gates; skip pools under 5% and `fast` pools. A weekly JSON row can show `hold` while its `gates` list reports a short window that has reset. See the lesson for the other JSON row fields.
3. Split into independent tasks (no shared files, one workspace per writer, ag a private empty one), the lowest tier that fits, one fresh ask each with facts inline, a different model per vote.
4. Wave size n = as many tasks as quota and load allow (not a fixed 3): one per usable pool top-down, then more on the same pool while the peer stays under its limit (cc 3 / cx 3 / ag 1) and the total under the active-ask limit (4, ceiling 6, manual asks count; 3 keeps one slot free): `peerhub batch run tasks.json --batch-id ID --max-parallel n`.
5. After each wave run `peerhub observation refresh`, then `peerhub diag --plan` again; asks still running are in-flight spend the readings do not show yet, so keep a margin; a task refused for quota is reported per task and the rest of that manifest still runs (one wave per manifest); the same batch ID replays settled tasks; a pool that turned `rest` or `blocked` drops out. No usable pool: defer.
