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

# Collect quota evidence and watch the dashboard in one loop.
peerhub monitor --interval-seconds 60
peerhub monitor --view plain --cycles 1

# Review with a second provider using a declared profile.
peerhub ask cc "Explain the Core Peer, Stream, Record and Offset flow" --stream learning --request-id learning-001 --profile cc.standard --json
```

The two `ask` examples require the corresponding vendor CLI to be installed and
authenticated, and spend provider quota. See the [adapter guide](docs/adapters/README.md).

Builtin provider names are `cx/codex`, `cc/claude` and `ag/agy`. Custom Peer identities can be registered with `peer register --peer worker --adapter cx`.

`ask` supports explicit `--profile`, `--model`, `--effort`, `--writable`, `--resume`, `--timeout-seconds`, optional `--silence-timeout-seconds`, `--max-output-bytes`, `--author-peer` and `--json`. Profile policy resolves workspace → global → packaged defaults; it does not assert measured model availability. Retrying the same `--request-id` reuses a completed response. A different prompt or binding conflicts, and an uncertain execution requires explicit reconciliation before another execution. By default, each ask starts a fresh session with bounded Stream catch-up. For CLI peers (cc, cx, ag), `--resume` reuses the native vendor session of this peer/stream when compatible; otherwise it starts a fresh session with bounded catch-up. Repeat the flag on each continuing ask. `--json` includes `effective_mode`, `fallback_reason`, and `injected_record_ids` when the Bridge exposes delivery details.

`--writable` is off by default. It maps to cc `--permission-mode acceptEdits`, cx `-s workspace-write` (default `-s read-only`), and ag `--mode accept-edits`. cx resume inherits the sandbox of the initial session and does not accept `-s`; writable mode is therefore part of the session binding, and changing it starts a fresh generation.

Quota is collected separately from diagnostics. `diag --fresh` has been replaced by `observation refresh`. Unmeasured values remain `UNKNOWN` or another explicit evidence state, and expired measurements become `STALE`.

`diag --live` repeatedly reads committed snapshots only. `--cycles N` bounds the number of frames; `--json` emits one JSON object per line in live mode. Watching never refreshes quota or changes stored data.

## Store selection and existing data

The store is `.peerhub/core.db`. Discovery picks the nearest one (current directory, `<cwd>/peerhub`, then parents); `--db PATH` or `PEERHUB_DB` choose explicitly. `peerhub diag health` reports `store_selection` (the path and how it was chosen: `env`, `explicit`, `discovered`, `workspace-default`, `default`). Developer scratch (live-gate JUnit etc.) goes under `.peerhub/work/`; the top of `.peerhub/` holds the database and its `restore.epoch` metadata.

Use global `--db PATH` before the command, or `PEERHUB_DB`, to select a store explicitly. An explicit missing path is never redirected to another store. `ask --workspace DIR` uses that directory's store unless a database was explicitly selected. Read-only commands do not create a missing store.

The v0 runtime, its importer, the `PEERHUB_CLI` selector and the 109-command table were removed (2026-10-08); the v0 source is kept only on the Git branch `legacy/v0-main-final`.

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

## Doc map

- [CONTRIBUTING.md](CONTRIBUTING.md): maintainer setup, tests, feedback, and release gates.
- [Adapter guide](docs/adapters/README.md) and [CLI reference](docs/adapters/peer-cli-reference.md): provider invocation and troubleshooting.
- [Configuration](docs/config-hierarchy.md) and [model profiles](docs/model-profiles/model-profiles.json): store selection and profile defaults.
- [Usage/evolution guide](docs/m1_spec/USAGE_GUIDE.md): Core flow and milestone scope.
- [Structure cleanup](docs/implementation/STRUCTURE_CLEANUP.md): current ownership and retired surfaces.
- [M2 contracts](docs/m2/README.md): extension host and durable continuity contracts.

## Limitations

- Paths containing spaces or special characters can expose quoting problems in
  external Windows launchers. See the [recorded path hazards](docs/reviews/mece-audit-2026-09-20-cross-repo.md).
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
