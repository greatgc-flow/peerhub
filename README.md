# PeerHub

A durable communication layer for collaborating AI peers. The Core owns only Peer, Stream, Record and Offset. Runtime delivery, observations, diagnostics, work continuity and collaboration capabilities are extensions.

## Install

Requires Python 3.11–3.14. Choose one:

```powershell
pipx install peerhub
uv tool install peerhub
pip install peerhub
```

A winget package is not provided.

## Develop from this checkout

Requires Python 3.11–3.14.

```powershell
python -m pip install -e ".[dev]"
peerhub --help
peerhub --version
```

This describes current source, not a newly published release. The public command is `peerhub`; the temporary milestone-specific entrypoint has been retired. `python -m peerhub` and `python -m peerhub.cli` use the same public CLI.

## Core recipe

The Core stores Peers, Streams, immutable Records, and per-peer Offsets.
These commands use a new `core-demo` Stream in the selected store and call no provider:

```powershell
peerhub peer register --peer reader --name "Demo reader"
peerhub stream create --stream core-demo --members reader
peerhub record append --stream core-demo --author-peer reader --kind message --body '"Hello"' --idempotency-key core-demo-001 --created-at 2026-10-10T00:00:00Z
peerhub record read --stream core-demo --after 0 --limit 10
peerhub offset get --peer reader --stream core-demo
peerhub offset advance --peer reader --stream core-demo --position 1 --revision 1
peerhub offset get --peer reader --stream core-demo
```

On an existing Stream, advance to the position you actually read and pass the
revision returned by `offset get`; the revision is a compare-and-swap guard.
Keep the same body, timestamp, and idempotency key when retrying an append.

## Everyday commands

```powershell
# Persist the prompt, execute one provider, and persist its response.
peerhub ask cx "Summarize this repository" --stream review --request-id review-001

# Explicit collection may contact provider CLIs.
peerhub observation refresh --peers cx cc ag
peerhub observation refresh --peers cc --system-dir D:\tools\portable  # explicit provider installation directory

# Read committed observations without probing or writing.
peerhub diag
peerhub diag quota --json
peerhub diag health
peerhub diag --live --interval-seconds 2
peerhub diag --view rich

# List every monitor group and see what each ID means; --json emits the same catalog.
peerhub monitor groups
peerhub monitor groups --json

# Show a read-only snapshot once, without provider refresh or writes.
peerhub monitor --snapshot
peerhub monitor --snapshot --group sessions --group execution

# Watch the eight default groups: overview, peers, execution, sessions, usage,
# context, capacity, and coverage. Collection may call provider CLIs
# (including agy /usage) and consume provider quota.
peerhub monitor --interval-seconds 60
peerhub monitor --view plain --cycles 1

# Keep the schema 1.1 quota/dashboard output for existing scripts.
peerhub monitor --json --schema-version 1.1 --cycles 1

# Customize grouped watch and snapshot defaults (workspace by default).
peerhub monitor defaults set --group overview peers execution sessions usage context capacity coverage
peerhub monitor defaults show

# Review with a second provider using a declared profile.
peerhub ask cc "Explain the Core Peer, Stream, Record and Offset flow" --stream learning --request-id learning-001 --profile cc.standard --json
```

The two `ask` examples require the corresponding vendor CLI to be installed and
authenticated, and spend provider quota. See the [adapter guide](docs/adapters/README.md).

Builtin provider names are `cx/codex`, `cc/claude` and `ag/agy`. Custom Peer identities can be registered with `peer register --peer worker --adapter cx`.

`ask` supports explicit `--profile`, `--model`, `--effort`, `--writable`, `--timeout-seconds`, optional `--silence-timeout-seconds`, `--max-output-bytes`, `--author-peer` and `--json`. Profile policy resolves workspace → global → packaged defaults; it does not assert measured model availability. Retrying the same `--request-id` reuses a completed response. A different prompt, binding or session selector conflicts, and an uncertain execution requires explicit reconciliation before another execution. By default, each ask reuses the latest compatible retained session of this peer/stream; otherwise it starts a fresh session with bounded Stream catch-up. Mutually exclusive `--fresh` and `--session HANDLE` select a new generation or one exact retained session. Exact selection rejects incompatibility or context overflow without allocating a substitute. Fresh still receives Stream history; use a dedicated stream for an independent task. CLI peers (cc, cx, ag) retain native vendor continuity. Older native mappings with unproven context are invalidated on upgrade; existing request replay and uncertain-execution blocking remain intact. `--json` always includes `session` (accepted handle, generation, mode, reason and telemetry, or null before allocation) and `error` (null on success). Text output ends with one `session:` line after allocation, including on failure or replay. Existing delivery details remain available. Context occupancy is unknown: billing usage is not an occupancy measurement.

`--writable` is off by default. It maps to cc `--permission-mode acceptEdits`, cx `-s workspace-write` (default `-s read-only`), and ag `--mode accept-edits`. cx resume inherits the sandbox of the initial session and does not accept `-s`; writable mode is therefore part of the session binding, and changing it starts a fresh generation.

Quota is collected separately from diagnostics. `diag --fresh` has been replaced by `observation refresh`. Unmeasured values remain `UNKNOWN` or another explicit evidence state, and expired measurements become `STALE`.
`--workspace` is an `ask` option; for `monitor` and `observation refresh` use the global `--db`, e.g. `peerhub --db <workspace>/.peerhub/core.db monitor`.

`diag --live` repeatedly reads committed snapshots only. `--cycles N` bounds the number of frames; `--json` emits one JSON object per line in live mode. Watching never refreshes quota or changes stored data.

`monitor groups` lists all ten stable group IDs, their meaning, and whether each
is selected by the effective defaults. The default monitor includes quota
capacity (`capacity`), retained session state and configured binding (`sessions`),
per-peer dependency chains (`peers`), and Bridge delivery certainty (`execution`).
Delivery certainty describes whether a request was not started, may have
started, started, or reached a terminal delivery state; `terminal` does not
mean the model answered the task successfully. PeerHub does not currently
classify semantic task success/error. Context occupancy intentionally stays 'unknown' because it is not validated by PeerHub (the CLIs do not report it in a form PeerHub accepts; cx 'debug models' exposes a context window but runtime occupancy is not reported in an accepted form). `runtime` and `store` remain available with
`--group` or `--snapshot --group`.

The bare `monitor` command shows a grouped human dashboard on a TTY and aligned
plain sections when redirected. `--view rich` requests visual panels and meters;
`--view plain` selects ASCII-safe output. Add repeatable `--group` options to
watch one or more groups, or `--group all` to include all ten. `monitor groups`
lists group IDs and meanings; `monitor defaults show|set|reset` inspects and
changes the default selection. `monitor --snapshot` renders one read-only
dashboard without refreshing providers. For automation, `--json` emits schema
2.0 NDJSON frames for watch and one JSON object for snapshot. The frozen 0.16.1
machine contract remains available as `--json --schema-version 1.1`; the hidden
deprecated `--legacy` alias selects it through 0.18.x and is removed in 0.19.0.

The `diag` dashboard shows a threat summary, a stable pool
table with usage, PACE in percentage points, reset countdown, conditional
forecast and freshness, then credits, latest asks, probe health and one
legend/controls line. PACE is measured usage minus elapsed window percentage;
positive means faster than even. Forecasts require 5 percentage points used
and 10% elapsed (20% for windows of at least one day); projected reset use of at
least 105% is a threat. Below the support gates the screen says “not enough
data”. Inferred anchors remain conditional and predictive accuracy is not
established.

Monitor runs collection cycles every N seconds (`--interval-seconds`), collecting
initially and then every Kth cycle (`--collect-every`). The legacy dashboard and
live diagnostics update countdowns at most 1 Hz; PACE and forecast ETA remain
anchored to the measurement. Non-TTY or unavailable VT output appends one frame
per cycle without escapes; JSON emits one object per cycle. `NO_COLOR` suppresses
colour while retaining supported cursor controls.

## Store selection and existing data

The store is `.peerhub/core.db`. Discovery picks the nearest one (current directory, `<cwd>/peerhub`, then parents); `--db PATH` or `PEERHUB_DB` choose explicitly. `peerhub diag health` reports `store_selection` (the path and how it was chosen: `env`, `explicit`, `discovered`, `workspace-default`, `default`). Developer scratch (live-gate JUnit etc.) goes under `.peerhub/work/`; the top of `.peerhub/` holds the database and its `restore.epoch` metadata.

Use global `--db PATH` before the command, or `PEERHUB_DB`, to select a store explicitly. An explicit missing path is never redirected to another store. `ask --workspace DIR` uses that directory's store unless a database was explicitly selected. Read-only commands do not create a missing store.

Monitor and diagnostics refuse a missing selected database with exit 5 before
any probes. Create it explicitly with `peerhub --db <path> peer register --peer
<id> --name <name>`. Observation refresh also refuses a missing automatically
selected store, including in a non-repository directory; an explicit `--db` or
`PEERHUB_DB` retains its existing collection bootstrap behaviour.

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

`diag` inspects local database state and committed snapshots without contacting provider networks or consuming quota:

```powershell
# Read-only store and stream diagnostics
peerhub diag
peerhub diag health
peerhub diag quota --json
```

### Upgrade

```powershell
pipx upgrade peerhub
# or
uv tool upgrade peerhub
# or
pip install --upgrade peerhub
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
tools/
  command_inventory.py
  traceability.py
  release_evidence.py
```

Milestones describe development progress, not runtime ownership. Frozen specifications, historical evidence and existing durable wire/storage identifiers retain their original names. See [the cleanup decisions and AG review](docs/implementation/STRUCTURE_CLEANUP.md).

## Where model facts come from and how to refresh them

Model facts shown by `monitor` come from `docs/model-profiles/model-profiles.json` and `peerhub/config_data/model-defaults.toml`.
Run the `refresh-model-profiles` skill (or query installed CLIs per manifest instructions) to verify models, update `observed_at`/`observed_versions`, and bump the defaults revision.
Observations are evidence, not policy: profile model and effort definitions require explicit review and approval.

## Doc map

- [Documentation map](docs/index.md): Single documentation map and navigation index.
- [CONTRIBUTING.md](CONTRIBUTING.md): maintainer setup, tests, feedback, and release gates.
- [Adapter guide](docs/adapters/README.md) and [CLI reference](docs/adapters/peer-cli-reference.md): provider invocation and troubleshooting.
- [Configuration](docs/config-hierarchy.md) and [model profiles](docs/model-profiles/model-profiles.json): store selection and profile defaults.
- [Usage/evolution guide](docs/m1_spec/USAGE_GUIDE.md): Core flow and milestone scope.
- [Structure cleanup](docs/implementation/STRUCTURE_CLEANUP.md): current ownership and retired surfaces.
- [M2 contracts](docs/m2/README.md): extension host and durable continuity contracts.

## Limitations

- On Windows, cx `--writable` cannot write into directories with owner-only ACLs (e.g. pytest tmp dirs, `tempfile.mkdtemp`); use a normally created directory.
- Paths containing spaces or special characters can expose quoting problems in
  external Windows launchers. See the [recorded path hazards](docs/history/reviews/mece-audit-2026-09-20-cross-repo.md).
- Node.js based CLIs depend on their runtime and launcher installation; portable
  layouts may resolve differently. See the [CLI observations](docs/compatibility/peer-cli-observations.md)
  and [adapter reference](docs/adapters/peer-cli-reference.md).
- Windows console or pipe encoding can affect non-ASCII output. See the
  [recorded encoding failure](docs/m1_impl/wave7_report.md) and
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

Historical evidence is retained separately: the 2026-10-04 archived snapshot has [VERIFIED-LOCAL records](docs/m1_impl/matrix_evidence/windows-py3.11-3.13.json), and the recorded CI head has [VERIFIED-CI records](docs/m1_impl/matrix_evidence/ci-run-37189754726.json). Those records preserve their original paths and apply only to the recorded snapshot/head, not this changed worktree.
