# PeerHub

A durable communication layer for collaborating AI peers. The Core owns only Peer, Stream, Record and Offset. Runtime delivery, observations, diagnostics, work continuity and collaboration capabilities are extensions.

## Install

Requires Python 3.11–3.15. Choose one:

```powershell
pipx install peerhub
uv tool install peerhub
pip install peerhub
```

A winget package is not provided.

### What each peer needs

PeerHub talks to each peer through that vendor's own tool or API key. It installs none of them and never downloads or runs an installer: a vendor's installer is a script from the network that differs per system and changes over time. Install only what the peers you use need, from the official page, and sign in as that page describes. `peerhub explain setup` helps diagnose missing prerequisites.

| Peer | Needs | Official page | Check |
|---|---|---|---|
| `cc` | the `claude` CLI, signed in | https://code.claude.com/docs/en/setup | `claude --version` |
| `cx` | the `codex` CLI, signed in | https://developers.openai.com/codex/cli | `codex --version` |
| `ag` | the `agy` CLI, signed in | https://antigravity.google/docs/cli/install | `agy --version` |
| `ca` | the `ant` CLI, signed in with `ant auth login` | https://platform.claude.com/docs/en/cli-sdks-libraries/cli/quickstart and, for Windows binaries, https://github.com/anthropics/anthropic-cli/releases | `ant --version` |
| `oa` | the environment variable `OPENAI_API_KEY` | a key is made at https://platform.openai.com/api-keys | the variable is set in the environment of the process that runs PeerHub |
| `ga` | the environment variable `GEMINI_API_KEY` | a key is made at https://aistudio.google.com/apikey | the variable is set in the environment of the process that runs PeerHub |

A key is never given to PeerHub on a command line or in a file. A tool must be on the PATH of the process that runs PeerHub. `--version` shows that a tool is installed, not that it is signed in: for `cc`, `cx` and `ag` the ACCOUNTS rows of `peerhub monitor --expand` show the login state.

When something is missing, the ask ends before anything starts (`failed_not_started`) and the message names the next step: `claude executable not found: install it and sign in (peerhub explain setup)` (the same for `codex` and `ant`; for `agy` it follows `cannot determine agy stdin support:`), `set OPENAI_API_KEY (peerhub explain setup)` or `set GEMINI_API_KEY (peerhub explain setup)`.

## Develop from this checkout

Requires Python 3.11–3.15.

```powershell
python -m pip install -e ".[dev]"
peerhub --help
peerhub --version
```

See [STATUS](docs/STATUS.md) for current source and release status. The public command is `peerhub`; the temporary milestone-specific entrypoint has been retired. `python -m peerhub` and `python -m peerhub.cli` use the same public CLI.

## First ask

After installing and signing in to one peer's tool (table above), pick the store explicitly and ask:

```powershell
peerhub --db .peerhub/core.db ask cc "Reply OK" --profile cc.standard --stream smoke --request-id smoke-1
```

The reply prints on stdout. Use a new `--request-id` for each new ask and a new `--stream` for every new task.

- AI agents: `peerhub guide` (decisions, about 400 tokens), then `peerhub diag --plan` (quota pools, active asks, free RAM and CPU, about 350 tokens). `peerhub explain <topic or keywords>` finds a lesson.
- Shared stores: follow `peerhub explain caller-ownership` to export a unique `PEERHUB_OWNER` per lead session, a readable `PEERHUB_LEAD` and batch lane identifiers.
- People: `peerhub monitor` is the human screen (quota, running asks, recent asks).
- Something wrong: `peerhub explain report-issues` lists what to include before filing an issue.

## Core recipe

The Core stores Peers, Streams, immutable Records, and per-peer Offsets.
These commands use a new `core-demo` Stream in the selected store and call no provider:

```powershell
peerhub --db .peerhub/core.db peer register --peer reader --name "Demo reader"
peerhub --db .peerhub/core.db stream create --stream core-demo --members reader
peerhub --db .peerhub/core.db record append --stream core-demo --author-peer reader --kind message --body '"Hello"' --idempotency-key core-demo-001 --created-at 2026-10-10T00:00:00Z
peerhub --db .peerhub/core.db record read --stream core-demo --after 0 --limit 10
peerhub --db .peerhub/core.db offset get --peer reader --stream core-demo
peerhub --db .peerhub/core.db offset advance --peer reader --stream core-demo --position 1 --revision 1
peerhub --db .peerhub/core.db offset get --peer reader --stream core-demo
```

On an existing Stream, advance to the position you actually read and pass the
revision returned by `offset get`; the revision is a compare-and-swap guard.
Keep the same body, timestamp, and idempotency key when retrying an append. See the [Core command reference](docs/commands.md#core-primitives) for inspection commands.

## Quick reference & guide

Run `peerhub guide` (or bare `peerhub`) for the offline quick start and its safety decisions in at most 1600 UTF-8 bytes with LF line endings. Use [`peerhub explain`](docs/commands.md#guide-and-explain) to look up a topic or an outcome code such as `ask_blocked_uncertain`.

## Everyday commands

| Command | Use |
|---|---|
| `peerhub ask --help` | [Send a prompt](docs/commands.md#ask), select a profile or reuse a session. |
| `peerhub suggest --tier effort --task review` | [Recommend a profile](docs/batch.md#suggest-an-ask) and print an ask command. |
| `peerhub diag --plan` | [Plan parallel work](docs/commands.md#diagnostics-and-observation) from stored quota and current load. |
| `peerhub observation refresh --peers cx cc ag` | [Collect quota evidence](docs/commands.md#diagnostics-and-observation). This may contact providers. |
| `peerhub batch run tasks.json --batch-id review-001 --max-parallel n --dry-run` | [Validate a batch](docs/batch.md). Choose a positive integer for `n`. |
| `peerhub account list` | [Inspect account-bound peers](docs/accounts.md). |
| `peerhub profile list` | [Inspect effective profiles](docs/config-hierarchy.md#model-profiles). |
| `peerhub profile stats` | [Inspect stored measurements](docs/config-hierarchy.md#profile-measurements-and-stream-budgets). |

Run these three commands to watch, take a read-only snapshot, or list available groups. See the [monitor guide](docs/monitor.md).

```powershell
peerhub monitor
peerhub monitor --snapshot
peerhub monitor groups
```

## API peers: ca, oa and ga

See [API peers and credits](docs/api-credits.md) for setup, text-only asks and outcomes.

## Store selection and existing data

See [Store selection](docs/config-hierarchy.md#store) for precedence, worktree discovery, bootstrapping and restore behavior.

## Common tasks

### Continue across two turns

To continue an active multi-turn conversation with the same vendor session:

```powershell
# Turn 1: Initial prompt on a dedicated stream
peerhub ask cx "Explain the store schema" --stream convo-01 --request-id turn-001

# Turn 2: Continue using the native session
peerhub ask cx "Now show an append example" --stream convo-01 --request-id turn-002
```

The Bridge retains up to 20 non-tombstoned sessions per peer/Stream and expires
sessions after more than 30 days of inactivity. Tombstones remain audit rows;
generations never rewind. Compatible history can be reused after a binding
changes back. Request-id replay returns its original session snapshot even if
the handle later expires. Historical asks lacking complete selector identity metadata are
inspectable as Records but cannot be replayed through the new interface; do not
retry uncertain work. Use `peerhub session list` and `peerhub session show`
for bounded, read-only session inspection.
Both commands show the monitor's stored context estimate and its age when available, with `ctx_est_pct`, `ctx_est_tokens`, and `ctx_age_seconds` in JSON (null when unknown).
See the [registry contract](docs/implementation/RETAINED_SESSIONS.md).

To create another generation on the same Stream, then return to the first
session, copy its full handle from the first result:

```powershell
peerhub ask cx "Start another conversation with Stream context" --stream convo-01 --request-id turn-003 --fresh
peerhub ask cx "Continue the first conversation" --stream convo-01 --request-id turn-004 --session <handle-from-turn-001>
```

Exact reuse includes intervening eligible Records, including the other
session's responses. Python uses `ask(..., fresh=True)` or
`ask(..., session="sess_0123456789abcdef")`. Handles belong to one store,
Stream, Peer and workspace lineage; full binding and continuity must match.

### Recover from a blocked stream

When a turn encounters a timeout, silence abort, or disconnect, the stream may enter `blocked_uncertain`. Retrying the same `--request-id` cannot replay an uncertain execution because it may have partially executed remotely. To recover safely, start subsequent turns on a **new** stream:

```powershell
# Inspect the current health and failure state
peerhub diag health --stream convo-01

# Recover: include a short summary of the committed context in the new prompt
peerhub ask cx "Context: We reviewed the Core schema; Peer, Stream, Record and Offset are the four primitives. Next, show an append example." --stream convo-02 --request-id rec-001
```

A fresh stream cannot see the old stream's records. Replace the example context
with a short summary of your committed work and the next safe action. Inspect
the old records with `peerhub record read --stream convo-01 --after 0 --limit 10`
if needed; an uncertain remote action may already have occurred, so do not
assume it failed or replay its request ID.

### Offline read-only checks

See [diagnostics](docs/commands.md#diagnostics-and-observation) for store, quota, readiness and running asks.

### Upgrade

Use `peerhub update --check` to inspect local versions, the bundled Codex catalog and public release metadata. Use `peerhub update --plan` for the selected store's blockers and upgrade steps. Both accept `--json` and leave the installation and store untouched.

To upgrade PeerHub safely:

0. **Plan and back up**. `peerhub update --plan` lists blocking asks and processes and prints the backup, restore and upgrade commands. See the [schema upgrade notes](docs/migrations.md). Wait until `peerhub update --plan` exits 0 (it exits 1 while asks are running), then back up by running the printed backup command. It writes `.peerhub/core.db.pre-update-<UTC time>.bak` for the default store. Copy the exact path the plan prints. The command must exit 0 and the file must show a new modified time before step 2.
1. **Stop `peerhub monitor`**: Stop any active monitor process before upgrading. A running monitor locks `peerhub.exe` on Windows and keeps old code running. See [Windows executable locks](docs/windows.md#upgrades-and-executable-locks). Also wait for running `peerhub ask` calls to finish: they keep dependency files such as `pydantic_core` loaded, so an in-place upgrade can fail and leave a half-deleted install. If peerhub crashes with `ModuleNotFoundError` after an upgrade, reinstall it and check the result records before re-sending an ask.
2. **Run upgrade**. Use the installer you originally used. The plan lists `uv tool upgrade peerhub` and `python -m pip install -U peerhub` as alternatives to the pipx example below.
   ```powershell
   pipx upgrade peerhub
   ```
   > [!NOTE]
   > If pip says no matching version right after a release, retry after a few minutes or install the file URL directly (PyPI index lag).
3. **Check, or restore**. `peerhub diag health` must report OK for the store you upgraded. If the new version cannot open the store, reinstall the old version and stop every client. Run the printed restore command; it names the interpreter that runs PeerHub, so it also works for pipx and uv installs. The command validates the backup, fences old clients by rotating `workspace.generation`, and runs `peerhub diag health` after the restore succeeds.
4. **Restart monitor** after the health check reports OK. Restart any other clients you stopped.
   ```powershell
   peerhub monitor
   ```

### Uninstall

```powershell
pipx uninstall peerhub
# or
uv tool uninstall peerhub
# or
pip uninstall peerhub
```

> **Note**: Uninstalling the package removes executable entrypoints but retains the local database and artifacts in `.peerhub/`. To delete store data, remove the `.peerhub/` directory manually.

## Package structure

```text
peerhub/
  core/          Peer, Stream, Record, Offset; SQLite storage
  extensions/    Bridge, Observation, Diag, Artifact, Work, Search, Routing, …
  cli/           public parser and command handlers
  config_data/   packaged provider model defaults
tests/
  communication/ Core, delivery, observations, migration and package gates
  continuity/    extension host, artifacts, work, skills, backup and eval
  collaboration/ federation, memory, search, routing, orchestration and approval
tools/           maintainer scripts
  a2a_sdk_echo_server.py  a2a_sdk_interop.py      ci_platform.py
  command_inventory.py    lint_changelog.py       live_gate.py
  model_ping.py           quota_report.py         release_evidence.py
  release_notes.py        traceability.py
```

Milestones describe development progress, not runtime ownership. Frozen specifications, historical evidence and existing durable wire/storage identifiers retain their original names. See [the cleanup decisions and AG review](docs/history/implementation/STRUCTURE_CLEANUP.md).

## Where model facts come from and how to refresh them

See [model profiles](docs/model-profiles/README.md) for the packaged defaults and refresh recipe.

## Doc map

See the [documentation map](docs/index.md) for all documentation.

## Limitations

- On Windows, cx `--writable` cannot write into directories with owner-only ACLs (e.g. pytest tmp dirs, `tempfile.mkdtemp`); use a normally created directory.
- Paths containing spaces or special characters can expose quoting problems in
  external Windows launchers. The recorded path hazards are in the removed review
  `git show 465bfe98de7d6931864da4991e45cc1993e342e6:docs/history/reviews/mece-audit-2026-09-20-cross-repo.md`
  (see the [archival ledger](docs/history/ledger-2026-10-17.md)).
- Node.js based CLIs depend on their runtime and launcher installation; portable
  layouts may resolve differently. See the [CLI observations](docs/history/compatibility/peer-cli-observations.md)
  and [adapter reference](docs/adapters/peer-cli-reference.md).
- Windows console or pipe encoding can affect non-ASCII output. See the
  [recorded encoding failure](docs/history/m1_impl/wave7_report.md) and
  [adapter reference](docs/adapters/peer-cli-reference.md#external-limitations).

These are environment warnings; historical observations do not establish a
current failure on every installation.

## Validation

```powershell
python -m pytest -q
pyright
python tools/command_inventory.py --check
python tools/traceability.py --upto 9

# Real provider canaries spend quota: local only, never in CI (refuses when CI is set).
python -m tools.live_gate --yes
```

Real providers are opt-in: set `PEERHUB_LIVE=1` and select `live`, `slow`, `e2e` or `canary` under `tests/communication/live`; live fixtures refuse CI. `-m live` selects exactly the six M1 live-tier tests; slow/e2e release cases use their own markers. The gate partitions those stages. Optional `canary` tests are outside the release catalog and measure usage, native resume recall, writable sentinel creation and cancellation using standard profiles. Use `python -m tools.live_gate --yes --only canary --peer cx` for lower-cost measurements; these results are not release evidence. See the [local live gate guidance](CONTRIBUTING.md#local-live-gate) for call budgets and skip behavior. Soak is separately opt-in with `PEERHUB_SOAK=1`. Local checks do not promote a milestone or authorize a release; candidate-matched CI, live and package evidence still apply.

Historical evidence is retained separately: the 2026-10-04 archived snapshot has [VERIFIED-LOCAL records](docs/history/m1_impl/matrix_evidence/windows-py3.11-3.13.json), and the recorded CI head has [VERIFIED-CI records](docs/history/m1_impl/matrix_evidence/ci-run-37189754726.json). Those records preserve their original paths and apply only to the recorded snapshot/head, not this changed worktree.
