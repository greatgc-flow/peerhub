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
