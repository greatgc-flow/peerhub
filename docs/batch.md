# Batch runs

Validate a flat manifest before executing asks (execution spends provider quota):

```powershell
peerhub batch run tasks.json --batch-id review-001 --max-parallel 2 --dry-run --json
peerhub batch run tasks.json --batch-id review-001 --max-parallel 2 --json
```

Use global `--db PATH` before `batch` to select a store. The parent resolves one
absolute store path for every worker, including tasks with different workspaces.
Dry runs validate without spawning workers or creating the store.
`--batch-id` and `--max-parallel` are required; parallelism is 1..16.

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

Reports contain identities, sessions, elapsed seconds and statuses, without responses:

| Status | Meaning |
| --- | --- |
| COMPLETED | Terminal delivery with a nonempty response and no error. |
| UNVERIFIED | Terminal delivery with an empty response. |
| FAILED | Positive evidence of no execution, or worker spawn failure. |
| UNCERTAIN | Launched worker without proof of a terminal or unexecuted result. |
| BLOCKED | Unlaunched cancellation, or earlier work/claims block this invocation. |

Exit 0 requires every task COMPLETED; exit 1 covers other statuses and invalid
manifests/options. Argparse syntax errors exit 2. Omit `--json` for a compact table.
Read responses with `peerhub record read` using the reported stream.

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

**I. Concurrency** (counted across batches and manual asks together)

| Limit | Value |
| --- | --- |
| Active asks, default | 4 (`--max-parallel 4`; the option stays required) |
| Active asks, ceiling | 6 |
| Per peer | cc 3, cx 3, ag 1 (use 2 for ag only after seeing no G-5H spike) |
| pytest | one run at a time, never alongside more than 3 asks |
| Free RAM | keep at least 6 GB |

If a provider throttles, reduce concurrency; do not reroute the same work to another peer.
