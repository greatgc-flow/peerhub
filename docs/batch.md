# Batch runs

Choose a positive integer for `n` based on quota and available machine resources.
Validate the manifest with `--dry-run` before executing asks that spend provider quota.

```powershell
peerhub batch run tasks.json --batch-id review-001 --max-parallel n --dry-run --json
peerhub batch run tasks.json --batch-id review-001 --max-parallel n --json
```

`peerhub batch run --help` includes a JSON example for the manifest path argument.

Use global `--db PATH` before `batch` to select a store. The parent resolves one
absolute store path for every worker, including tasks with different workspaces.
Both dry runs and execution resolve every task's effective profile in its workspace before starting workers or creating the store.
Invalid local settings reject the entire manifest with `invalid_manifest` and exit 1. The error names the task.
This validation runs offline and checks local configuration. Provider installation, login and quota are checked separately.
`--batch-id` and `--max-parallel` are required. PeerHub does not cap the count.

Dry-run warnings cover equal or nested workspaces for writable pairs
(`workspace_overlap`), low free RAM (`free_ram`), and high CPU load (`cpu_busy`).
Dry runs show the same `load` object as `diag --plan`. The text line includes the
ownership split when other or unknown asks are active. JSON provides
`load.active_by_relation` with null counts for an unavailable sample. Pool
attribution is available through `diag --plan`. See
`peerhub explain caller-ownership` for session setup.
When the selected store has settled or uncertain tasks from the manifest for the same batch ID,
`replay: N settled, M uncertain` appears before the load line. JSON includes both
counts in `replay`. Workspace overlap can invalidate a manifest.

The execution report totals task outcomes as `ok`, `failed`, `uncertain`, and `quota_refused`.

The UTF-8 JSON manifest contains only `tasks`, with at least one entry.

```json
{"tasks":[{"task_id":"review","peer":"cx","prompt":"Review the code"}]}
```

Each task requires `task_id`, `peer`, and exactly one of `prompt` or `prompt_file`.
Optional keys: `workspace`, `profile`, `writable` (default false), `timeout_s`
(finite positive seconds, default 600). Unknown keys, including `stream`, fail.
Dry runs show the profile in the table and in JSON `profile_display`. Tasks without a
manifest profile retain `null` in JSON `profile`. They display `(default: PROFILE)`
when the effective default is a named profile, or `(peer default)` for a valid unnamed default.
API peers (`ca`, `oa`, `ga`) require a manifest `profile` to select a model.
IDs match `[A-Za-z0-9._-]{1,64}`. Task IDs must be unique and are case-sensitive on all platforms. Prompt files are UTF-8,
at most 1 MiB, and resolved relative to the manifest; workspaces must exist and
relative workspace paths use the current directory. Peers must be builtin aliases
or registered with a supported adapter. Prompts must contain non-whitespace text.

Every ag task is writable, even if the manifest says otherwise (#140).
If either task writes, equal or nested workspaces reject the entire batch.
Streams and request IDs derive from the batch/task IDs; no stream override exists.

Reports contain identities, sessions, elapsed seconds and statuses. A task whose ask reports denied tool actions
(for example `reason: denied_empty` from an ag task) adds `denied_actions`, the denied tool names only, to its JSON
row and a `denied: NAME, NAME` line under its row in text. The names tell an out-of-workspace read from a denied shell
command. Use
`--responses` to include replies.

| Status | Meaning |
| --- | --- |
| COMPLETED | Terminal delivery with a nonempty response and no error. |
| UNVERIFIED | Terminal delivery with an empty response. |
| FAILED | A worker spawn failure, an unusable reply, or evidence of no execution. Quota refusals have reason `not_executed:quota_exhausted` or `not_executed:usage_limit` and count under `outcomes.quota_refused`. |
| UNCERTAIN | Launched worker without proof of a terminal or unexecuted result. |
| BLOCKED | Unlaunched cancellation, or earlier work/claims block this invocation. |

Exit 0 requires every task COMPLETED; exit 1 covers other statuses and invalid
manifests/options. Argparse syntax errors exit 2. Omit `--json` for a compact table.

`--responses` adds every COMPLETED task's reply. JSON adds a `response` field to each included task. Text
adds a `--- TASK reply ---` block after the table, in manifest order. Replies can
contain separator lines themselves, so use `--json` for machine parsing. Dry runs
and tasks with other outcomes have no reply text. To read a stored reply later,
use `peerhub record read --stream <stream> --json`.

**Votes and reviews in one command**

- cc and cx tasks: `workspace` = the repository, no `writable` (read-only).
- ag task: `workspace` = a private EMPTY directory. ag tasks are always writable; an empty
  directory passes the overlapping-writers check and keeps ag away from the repo.
- Put every fact in each prompt; the empty-directory ag peer cannot read the repo.
- `peerhub batch run votes.json --batch-id vote-001 --max-parallel n --responses`
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
The runner supports flat tasks and explicit parallelism. Dependencies, retries, budgets
and failover are outside the manifest contract. The caller sizes the wave from quota
evidence. Each ask applies its usual quota checks and a quota refusal affects only that task.
COMPLETED describes delivery.

## Choosing profiles and concurrency (manual)

The caller selects the profile and decides how many tasks to run based on quota and machine resources.
Tiers: `standard` = eco, `effort` = normal (default for implementation), `deepthink` = quality,
`<peer>.<top>` (`cx.astra`, `ag.neon`, `ag.argon`, `cc.fable`) = top: opt-in, never a default.

**H. Balancing rule**

1. Pick the lowest tier that meets the task class: bulk/mechanical = `standard`; docs = `standard`
   (formatting), `effort` (writing) or `deepthink` (technical claims); code = `effort`, escalate to
   `deepthink`; review = `deepthink` from an independent reviewer; design/debate = `deepthink` or an explicit top.
2. Drop candidates with any applicable pool that has stale evidence (regardless of `STATE` shown, since a stale pool can display `low` or `fast`; check `AGE` and the `PEERS` `(aged)` tag) or is `?`, `aged`, `reset`, or under 5% LEFT, and prefer candidates without a `fast` pool (see `peerhub monitor`).
3. Break ties across pool families by the highest `LEFT% / max(days_to_reset, 0.2)`. This rule ranks the candidates for a single ask (the `suggest:` line); a wave of several tasks uses the [parallel wave procedure](#parallel-work-over-the-least-quota) (lowest 7D PACE first, then size n), so the two answer different questions.

The monitor's `suggest:` line lists the best eligible profile per tier. Among
eligible candidates, those without an applicable `fast` pool rank first, then
the existing worst-pool score, then profile name. A `|` separates
the best eligible alternative from a different peer in the same tier, for
example `eco=cc.standard|ag.standard`. With no eligible alternative, only the
primary appears. These suggestions do not execute asks or reroute work.

`ag.argon` and Flash draw from the same Gemini windows, `G-5H` and `G-7D`,
shown in `peerhub diag --plan`. The legacy `A-7D` window shows `n/a` when omitted from a complete report.
See [Antigravity quota pools](adapters/ag.md#quota-pools) and the
[0.26.11 release notes](../CHANGELOG.md#02611---2026-11-25) for when Antigravity stopped reporting it.

For an AI planning a parallel wave, `peerhub explain parallel-quota` summarizes the [parallel wave procedure](#parallel-work-over-the-least-quota).

## Suggest an ask

`peerhub suggest` is deprecated, removed in the next release. Use `peerhub diag --plan` for planning. Until removal, it prints an ask and a reason, with optional candidate facts. Its behavior is otherwise unchanged.

`--candidates` shows the quota evidence behind eligibility. Among enabled profiles, a measured 5-hour window must be under 90% used. A fresh provider-reported absence skips that gate. An exhausted 7-day pool excludes the profile. The lowest 7-day PACE wins among profiles with usable evidence, with ties in ag, cx, cc order. Missing or over-15-minute evidence can trigger one refresh attempt and spend quota. Candidates without usable evidence remain fallback choices marked `quota unknown`.

**I. Concurrency**

PeerHub does not cap the caller's `--max-parallel`. Before each launch, the runner checks available RAM and CPU load.
While batch asks are running, it holds further launches and polls every few seconds when either
reading reaches its warning condition. With no batch asks running, it starts the next task and prints
the warning. Already running tasks continue. Unavailable readings show `?` and do not block launches.

| Resource | Warning condition |
| --- | --- |
| Free RAM | Below 6 GB |
| CPU load | At or above 80% |

The JSON `cpu_busy_pct` reports system-wide CPU utilization sampled over a short interval.
Read the load line before launching lanes, count other sessions' asks, and hold new work
at 80% CPU or higher. A standalone ask warns with the sampled load facts and proceeds
under CPU pressure. Low RAM keeps bounded protection when other asks are active.

If a provider throttles, reduce concurrency; do not reroute the same work to another peer.

### Parallel work over the least quota

Rule H ranks candidates for a single ask. Use the stored quota facts to order the pools and the load line to size the wave.

1. `peerhub diag --plan` shows pools from stored evidence and the `load:` line with active asks by peer and quota pool, free RAM, and CPU load and logical cores, sampled now. With `--json`, read the `load` object. `peerhub monitor` is the human screen. Use `spend` pools first, then `hold`. If old or `(stale)` readings could change your choice, refresh their providers with `peerhub observation refresh --peers PEER ...`, then read again. See [probe costs](implementation/OBSERVATION_PROBES.md) for when agy can consume model tokens.
   A parent-limited child uses effective quota and time to reset for its displayed `BUDGET%/h`. Advice ranks the pool's own action, its effective parent action, and applicable gates for the same profile on the same account in order of `spend`, `hold`, `rest`, and `blocked`. The most restrictive action wins. A fresh zero for a shared pool can gate other configured pools on the same account even when another reporter supplied the displayed reading. When a gate wins, the reason and JSON `binding` name its pool, and an exhausted gate reports `gate: <pool> out`. A stale zero keeps its refresh reason. In JSON, `target_pct_per_hour`, `left_pct`, `pace_pt` and `resets_in_seconds` describe the child's own window, and `health` carries its effective state, pace and reset.
2. Order usable pools by 7D PACE, lowest first; rank a family by its 7D parent; 5H only gates; skip pools under 5% and `fast` pools. A weekly JSON row can show `hold` while its `gates` list reports a short window that has reset. JSON rows also include profiles, age_seconds, ttl_seconds, fast, family, and parent. A profile listed under several pools spends each of them.
3. Split into independent tasks (no shared files, one workspace per writer, ag a private empty one), the lowest tier that fits, one fresh ask each with facts inline, a different model per vote.
4. Start with the usable pools in order. Choose `n` based on their quota and the available machine resources, then run `peerhub batch run tasks.json --batch-id ID --max-parallel n`.
5. After each wave, refresh only the providers whose pools you used or whose freshness could change the next launch. For a wave that used only Codex, run `peerhub observation refresh --peers cx` if you need a fresh reading. Read `peerhub diag --plan` again to check the next wave. Keep a margin for in-flight spend the readings do not show yet. A task refused for quota is reported per task and the rest of that manifest still runs. The same batch ID replays settled tasks. Drop pools that turn `rest` or `blocked` and defer if no usable pools remain.
   Parallel ag asks were seen to spike G-5H, so watch G-5H after each wave.
   The load line lists in-flight counts for pools with applicable table rows. Its
   `n/a pools` clause lists other mapped in-flight pools, with `+K` for any names
   beyond the display limit. Leave margin for in-flight and unmapped asks. JSON `load.by_pool` includes all mapped pools.
6. After an uncertain ask, check `peerhub diag running` and `peerhub diag health --stream STREAM`, then reconcile the workspace before retrying.
