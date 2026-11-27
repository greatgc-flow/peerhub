# PeerHub commands

Use `peerhub COMMAND --help` for the current flags and defaults. Put the global `--db PATH` before the command. See [store selection](config-hierarchy.md#store).

## Core primitives

The [Core contract](m1_spec/01_M1/CORE_CONTRACT.md) defines the data model. Try the [Core recipe](../README.md#core-recipe) without contacting a provider.

| Command | Purpose |
|---|---|
| `peer` | Manage peer identities. |
| `peer register` | Register `--peer ID` with an optional `--adapter ADAPTER`. |
| `peer get` | Read the identity named by `--peer ID`. |
| `peer show` | Alias of `peer get`. |
| `peer list` | Page through peers with `--limit` and `--cursor`. |
| `stream` | Manage ordered streams. |
| `stream create` | Create `--stream ID` with optional `--members PEER ...`. |
| `stream show` | Read the stream named by `--stream ID`. |
| `stream list` | Page through streams with `--limit` and `--cursor`. |
| `record` | Work with immutable entries. |
| `record append` | Append a record to a stream. |
| `record read` | Read from `--stream ID` using `--after POSITION` and `--limit N`. |
| `offset` | Inspect or advance a peer's progress. |
| `offset get` | Read the position and revision for `--peer ID --stream STREAM`. |
| `offset advance` | Set `--position N` for `--peer ID --stream STREAM` using `--revision REV`. |

Read from position 0 to start at the beginning. Advance to the position you actually read using the revision returned by `offset get`.

## Ask

| Command | Purpose |
|---|---|
| `ask` | Send a prompt to a peer or preview it with `--dry-run`. |
| `outcome` | Record whether an ask was accepted, rejected, or retried. |

`peerhub ask PEER "prompt"` accepts positional text or UTF-8 files supplied with `--query-file PATH` (alias `--prompt-file`). Repeat the option to join files in command-line order with one blank line between them. The monitor uses the last file for its prompt link when that file is eligible. Use `--dry-run` to inspect the resolved ask without writing or contacting a provider.

```powershell
peerhub ask ag --query-file NOW.md --query-file task.md --writable --stream handoff --request-id handoff-1
```

- **Profiles.** Use a qualified `--profile PEER.NAME` to choose a [configured profile](config-hierarchy.md#model-profiles). `--model` and `--effort` override the binding. See [batch planning](batch.md) for choosing a tier and [API peers](api-credits.md) for provider-specific constraints.
- **Streams and retries.** Choose a dedicated `--stream` for each new task and target one peer per stream. CLI peers default to `peer:<id>:chat`; API peers default to `peer:<id>:<request-id>`. Use a new `--request-id` for each new ask and reuse it for retries of the same ask. A changed prompt, binding or session selector can conflict. An uncertain execution blocks the stream pending reconciliation.
- **Sessions.** CLI peers reuse the latest compatible retained session for the peer and stream or start fresh with bounded catch-up. Use `--fresh` to force a new generation with catch-up, or `--session HANDLE` to select exactly one retained session. See the [retained session contract](implementation/RETAINED_SESSIONS.md) for compatibility, replay and receipts.
- **Execution.** `--workspace` (or `-w`) sets the provider's working directory. `--writable` is off by default. Native `ag` requires explicit write consent, including for review asks. See [adapter limits](adapters/peer-cli-reference.md#sandbox-and-event-limits). See [guidance](guidance-reference.md#ask_uncertain) before retrying after a timeout. `--timeout-seconds` defaults to 600. `--silence-timeout-seconds` is optional and `--max-output-bytes` defaults to 1,000,000. `--author-peer` sets the recorded prompt author.
- **Waiting.** An ask waits for the peer to finish and prints the reply and session line. For long implementation or review asks, set 1800–3600 seconds, or up to 7200 for ag. Agy can stay silent until completion. Leave the silence timeout unset or set it at least as high as the total timeout.
- **Receipt.** Text asks print a receipt to stderr on a terminal unless `PEERHUB_NO_RECEIPT=1`. Use `--json` for fields such as `requested_record_id`, `session` and `error`. Use the request record ID with `peerhub outcome ASK_ID accepted|rejected|retried` to annotate the [stored measurements](config-hierarchy.md#profile-measurements-and-stream-budgets).
- **Final result.** Add `--final-only` to print one compact JSON object when the ask finishes, with `response`, `status`, `requested_record_id` and the `session` handle. Unavailable values are null. This mode skips live reply output and the terminal receipt. Warnings and recovery hints still go to stderr. It takes precedence over `--json` and requires an executing ask.

CLI dispatch for default ag, cx and cc accounts also attempts to refresh quota when evidence is missing or older than 15 minutes. It waits at most one second for the refresh.

## Diagnostics and observation

`peerhub diag` reads the store and committed evidence once. AI agents can run `peerhub diag --plan` once per turn. For a continuously updated terminal dashboard, use [monitor](monitor.md).

| Command | Reads |
|---|---|
| `diag` | Read committed evidence. With `--plan`, show per-pool `spend` (PACE <= -10), `hold`, `rest` (PACE > +20 or forecast exhausts before reset) or `blocked`, with quota, pace, budget per hour, exhaustion/reset and live machine load. Repeat `--peer PEER` to filter. The plan supports plain text and JSON. |
| `diag quota` | Stored quota and rate-limit evidence, filtered by `--peer`, `--pool` or `--max-age-seconds`. Missing evidence is `UNKNOWN`. |
| `diag health` | Store selection and health, or a stream's health. |
| `diag readiness` | Cached profile readiness. `READY` requires fresh positive verified quota and a matching terminal delivery. A pool's remaining value expires at its reset even when its observation is still fresh. |
| `diag running` | Lease-active asks, with caller relation `mine`, `other` or `unknown`. The peer filter applies before the limit. A lease can outlast the process. See [caller ownership](lessons.md#identify-your-sessions-running-asks-caller-ownership). |
| `diag integrity` | Full SQLite integrity check, which may take time. |

Use `--observation-db PATH` with `diag --plan` or `diag quota` to read observations from another store. These commands exit 5 if the observation store is missing. Without an override, the selected store supplies observations. With an existing observation store and a missing selected store, `diag --plan` still reports the observations and shows the active ask count as unknown (`load.active: null` in JSON). See [parallel planning](batch.md#choosing-profiles-and-concurrency-manual) for pool ranking and launch sizing.

Use `diag --plan --json` for scripts. Each pool includes its identity, left percentage (`left_pct`), `PACE(pp)` (`pace_pt`), `BUDGET%/h` (`target_pct_per_hour`), effective `action`, limiting pool (`binding`), reason, its own reset and the effective reset after parent limits. In JSON, floats are rounded to two decimal places; `health` reports the pool's own state (after any scoped weekly parent cap) while `action` reports the effective gated action. In plan text, `—` (`?` in ASCII) marks an unknown value. The top-level `load` includes lease-active asks and current machine resources.

| Command | Purpose |
|---|---|
| `observation` | Collect provider evidence. |
| `observation refresh` | Write refreshed quota and account evidence. This may contact providers. |

Use `peerhub observation refresh --peers cx` to refresh a single provider's evidence. Before launching a wave, select the providers whose quota readings could change your launch decision. `--system-dir PATH` points to a portable provider installation. The probe timeout defaults to 15 seconds and doubles at 80% CPU load or higher. See [what each probe consumes](implementation/OBSERVATION_PROBES.md).

## Monitor

| Command | Purpose |
|---|---|
| `monitor` | Watch the dashboard or read one frame with `--snapshot`. See the [monitor guide](monitor.md). |
| `monitor open` | Read a stored prompt with the short ID from RUNNING, e.g. `peerhub monitor open ID`. |
| `monitor repair` | Release dead leases and rebuild monitor projections from durable evidence. |

## Accounts, profiles, and sessions

| Command | Purpose |
|---|---|
| `account` | Manage [account-bound peers](accounts.md). |
| `account bind` | Bind a new peer to a Claude Code or Codex account root. |
| `account list` | List account bindings. |
| `account show` | Inspect one account binding. |
| `profile` | Inspect [configured profiles and measurements](config-hierarchy.md#model-profiles). |
| `profile check` | Compare profiles with installed catalogs and check measurement age. |
| `profile list` | List effective profiles. |
| `profile show` | Display one profile's resolved values and their sources. |
| `profile stats` | Read stored ask measurements by profile. |
| `session` | Inspect retained sessions. |
| `session list` | List retained sessions, optionally filtered by peer or stream. |
| `session show` | Show the retained session for one handle. |

## Compatibility

PeerHub keeps these forms for existing callers. Use the canonical forms in new
commands, code, and documentation.

| Compatibility form | Canonical form | Why it stays |
|---|---|---|
| Short profile IDs such as `standard` | `PEER.NAME` for the selected provider, such as `cc.standard` with `ask cc` | Existing asks and batch manifests can keep their provider-relative profile names. |
| Profile alias `cx.deepthink` | `--profile cx.effort --effort xhigh` with packaged defaults | Existing Codex asks keep their model and effort defaults. A terminal text ask using the alias can print a deprecation notice. |
| Hidden `--format` on `peer list`, `profile list`, `profile show`, `session list`, `session show`, and `stream list` | `--json` for `--format json`, or the default human output for `--format auto` and `--format table` | Existing scripts can keep their output selection. |
| Python export `peerhub.cli.app.with_writable` | `peerhub.cli.ask_cmd.with_writable` | Existing Python callers can keep their import. The helper inserts `--writable` before the `--` separator or appends it when there is no separator. |

## Batch

| Command | Purpose |
|---|---|
| `batch` | Manage [parallel asks](batch.md). |
| `batch run` | Run a flat manifest or validate it with `--dry-run`. Execution spends quota. |

`batch run --responses` includes every completed reply in the report. Dry runs show no replies.

## Guide and explain

| Command | Purpose |
|---|---|
| `guide` | Print the offline quick reference. |
| `explain` | Look up lessons, profiles, or outcome codes offline. |

`peerhub guide` gives the quick start and safety decisions offline. Bare `peerhub explain`, `peerhub explain list`, and `peerhub explain lessons` list the current lesson IDs. Ask for one ID or alias, an outcome code, an exact profile ID such as `cc.effort`, or free words such as `peerhub explain ag silence`. Each old lesson name in a cluster opens the full entry, so `peerhub explain monitor-view` opens `monitor-states`. Both commands support `--json`. Run `peerhub explain exit-codes` for process exit codes and stdout/stderr behavior. See the [lookup order and lesson catalog](lessons.md).

## Update

| Command | Purpose |
|---|---|
| `update` | Check for a release or plan an upgrade. |

See [Upgrade](../README.md#upgrade) for `peerhub update --check`, `peerhub update --plan` and the installed-package upgrade procedure.
