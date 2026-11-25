# PeerHub commands

Use `peerhub COMMAND --help` for the current flags and defaults. Put the global `--db PATH` before the command. See [store selection](config-hierarchy.md#store).

## Core primitives

The [Core contract](m1_spec/01_M1/CORE_CONTRACT.md) defines the data model. Try the [Core recipe](../README.md#core-recipe) without contacting a provider.

| Primitive | Inspect or change it |
|---|---|
| Peer, an identity | `peer register --peer ID [--adapter ADAPTER]`, `peer get --peer ID` (alias `peer show`), `peer list [--limit N] [--cursor CURSOR]` |
| Stream, an ordered sequence | `stream create --stream ID [--members PEER ...]`, `stream show --stream ID`, `stream list [--limit N] [--cursor CURSOR]` |
| Record, an immutable entry | `record append --help`, `record read --stream ID --after POSITION --limit N` |
| Offset, one peer's progress in a stream | `offset get --peer ID --stream STREAM`, `offset advance --peer ID --stream STREAM --position N --revision REV` |

Read from position 0 to start at the beginning. Advance to the position you actually read using the revision returned by `offset get`.

## Ask

`peerhub ask PEER "prompt"` accepts inline text or `--query-file PATH` (alias `--prompt-file`) for a UTF-8 file. Use `--dry-run` to inspect the resolved ask without writing or contacting a provider.

- **Profiles.** Use `--profile ID` to choose a [configured profile](config-hierarchy.md#model-profiles). `--model` and `--effort` override the binding. See [batch planning](batch.md) for choosing a tier and [API peers](api-credits.md) for provider-specific constraints.
- **Streams and retries.** Choose a dedicated `--stream` for each new task and target one peer per stream. CLI peers default to `peer:<id>:chat`; API peers default to `peer:<id>:<request-id>`. Use a new `--request-id` for each new ask and reuse it for retries of the same ask. A changed prompt, binding or session selector can conflict. An uncertain execution blocks the stream pending reconciliation.
- **Sessions.** CLI peers reuse the latest compatible retained session for the peer and stream or start fresh with bounded catch-up. Use `--fresh` to force a new generation with catch-up, or `--session HANDLE` to select exactly one retained session. See the [retained session contract](implementation/RETAINED_SESSIONS.md) for compatibility, replay and receipts.
- **Execution.** `--workspace` (or `-w`) sets the provider's working directory. `--writable` is off by default. Native `ag` requires explicit write consent, including for review asks. See [adapter limits](adapters/peer-cli-reference.md#sandbox-and-event-limits). See [guidance](guidance-reference.md#ask_uncertain) before retrying after a timeout. `--timeout-seconds` defaults to 600. `--silence-timeout-seconds` is optional and `--max-output-bytes` defaults to 1,000,000. `--author-peer` sets the recorded prompt author.
- **Waiting.** An ask waits for the peer to finish and prints the reply and session line. For long implementation or review asks, set 1800–3600 seconds, or up to 7200 for ag. Agy can stay silent until completion. Leave the silence timeout unset or set it at least as high as the total timeout.
- **Receipt.** Text asks print a receipt to stderr on a terminal unless `PEERHUB_NO_RECEIPT=1`. Use `--json` for fields such as `requested_record_id`, `session` and `error`. Use the request record ID with `peerhub outcome ASK_ID accepted|rejected|retried` to annotate the [stored measurements](config-hierarchy.md#profile-measurements-and-stream-budgets).

CLI dispatch for default ag, cx and cc accounts also attempts to refresh quota when evidence is missing or older than 15 minutes. It waits at most one second for the refresh.

## Diagnostics and observation

`peerhub diag` reads the store and committed evidence. Use `--live` for repeated reads, `--interval-seconds` to set the interval and `--cycles N` to limit the watch (default 0, until interrupted). Live JSON is one object per line. For the human dashboard, use [monitor](monitor.md).

| Command | Reads |
|---|---|
| `diag --plan [--json]` | Per-pool `spend` (PACE <= -10), `hold`, `rest` (PACE > +20 or forecast exhausts before reset) or `blocked`, with quota, pace, budget per hour, exhaustion/reset and live machine load. Repeat `--peer PEER` to filter. The plan supports plain text and JSON. Passing `--view rich` is a usage error. |
| `diag quota [--json]` | Stored quota and rate-limit evidence, filtered by `--peer`, `--pool` or `--max-age-seconds`. Missing evidence is `UNKNOWN`. |
| `diag health [--stream ID]` | Store selection and health, or a stream's health. |
| `diag readiness [--json]` | Cached profile readiness. `READY` requires fresh positive verified quota and a matching terminal delivery. |
| `diag running [--peer PEER] [--limit N] [--json]` | Lease-active asks, with caller relation `mine`, `other` or `unknown`. The peer filter applies before the limit. A lease can outlast the process. See [caller ownership](lessons.md#identify-your-sessions-running-asks-caller-ownership). |
| `diag integrity` | Full SQLite integrity check, which may take time. |

Use `--observation-db PATH` with `diag --plan` or `diag quota` to read observations from another store. Plan load still comes from the selected store. See [parallel planning](batch.md#choosing-profiles-and-concurrency-manual) for pool ranking and launch sizing. A missing selected store exits 5.

`peerhub observation refresh --peers cx cc ag` explicitly collects and writes quota evidence and may contact providers. It replaces `diag --fresh`. `--system-dir PATH` points to a portable provider installation. The probe timeout defaults to 15 seconds and doubles at 80% CPU load or higher. See [what each probe consumes](implementation/OBSERVATION_PROBES.md).

## Guide and explain

`peerhub guide` gives the quick start and safety decisions offline. Bare `peerhub explain`, `peerhub explain list`, and `peerhub explain lessons` list the lesson IDs. Ask for one ID, an outcome code, an exact profile ID such as `cc.effort`, or free words such as `peerhub explain ag silence`. Both commands support `--json`. Run `peerhub explain exit-codes` for process exit codes and stdout/stderr behavior. See the [lookup order and lesson catalog](lessons.md).

## Update

See [Upgrade](../README.md#upgrade) for `peerhub update --check`, `peerhub update --plan` and the installed-package upgrade procedure.
