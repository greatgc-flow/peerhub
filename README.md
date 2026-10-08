# PeerHub

A durable communication layer for collaborating AI peers. The Core owns only Peer, Stream, Record and Offset. Runtime delivery, observations, diagnostics, work continuity and collaboration capabilities are extensions.

## Develop from this checkout

Requires Python 3.11–3.14.

```powershell
python -m pip install -e ".[dev]"
peerhub --help
peerhub --version
```

This describes current source, not a newly published release. The public command is `peerhub`; the temporary milestone-specific entrypoint has been retired. `python -m peerhub` and `python -m peerhub.cli` use the same public CLI.

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

# Prompt files, provider working directory, and explicit model binding.
peerhub ask ag --query-file prompt.txt --workspace . --model MODEL --json
peerhub ask cx "Reply briefly" --profile cx.standard --silence-timeout-seconds 30
```

Builtin provider names are `cx/codex`, `cc/claude` and `ag/agy`. Custom Peer identities can be registered with `peer register --peer worker --adapter cx`.

`ask` supports explicit `--profile`, `--model`, `--effort`, `--timeout-seconds`, optional `--silence-timeout-seconds`, `--max-output-bytes`, `--author-peer` and `--json`. Profile policy resolves workspace → global → packaged defaults; it does not assert measured model availability. Retrying the same `--request-id` reuses a completed response. A different prompt or binding conflicts, and an uncertain execution requires explicit reconciliation before another execution. Conversation continuity currently uses bounded Stream history; native provider-session resume is not claimed.

Quota is collected separately from diagnostics. `diag --fresh` has been replaced by `observation refresh`. Unmeasured values remain `UNKNOWN` or another explicit evidence state, and expired measurements become `STALE`.

`diag --live` repeatedly reads committed snapshots only. `--cycles N` bounds the number of frames; `--json` emits one JSON object per line in live mode. Watching never refreshes quota or changes stored data.

## Store selection and existing data

The store is `.peerhub/core.db`. Discovery picks the nearest one (current directory, `<cwd>/peerhub`, then parents); `--db PATH` or `PEERHUB_DB` choose explicitly. `peerhub diag health` reports `store_selection` (the path and how it was chosen: `env`, `explicit`, `discovered`, `workspace-default`, `default`). Developer scratch (live-gate JUnit etc.) goes under `.peerhub/work/`; the top of `.peerhub/` holds the database and its `restore.epoch` metadata.

Use global `--db PATH` before the command, or `PEERHUB_DB`, to select a store explicitly. An explicit missing path is never redirected to another store. `ask --workspace DIR` uses that directory's store unless a database was explicitly selected. Read-only commands do not create a missing store.

The v0 runtime, its importer, the `PEERHUB_CLI` selector and the 109-command table were removed (2026-10-08); the v0 source is kept only on the Git branch `legacy/v0-main-final`.

## Package structure

```text
peerhub/
  core/          Peer, Stream, Record, Offset; SQLite and data import
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

Milestones describe development progress, not runtime ownership. Frozen specifications, historical evidence and existing durable wire/storage identifiers retain their original names. See [the cleanup decisions and AG review](docs/implementation/STRUCTURE_CLEANUP_KO.md).

## Validation

```powershell
python -m pytest -q
pyright
python tools/command_inventory.py --check
python tools/traceability.py --upto 9

# Real provider canaries spend quota: local only, never in CI (refuses when CI is set).
python -m tools.live_gate --yes
```

Real providers are opt-in: set `PEERHUB_LIVE=1` and select `live`, `slow` or `e2e` under `tests/communication/live`. Soak is separately opt-in with `PEERHUB_SOAK=1`. Local checks do not promote a milestone or authorize a release; candidate-matched CI, live and package evidence still apply.

Historical evidence is retained separately: the 2026-10-04 archived snapshot has [VERIFIED-LOCAL records](docs/m1_impl/matrix_evidence/windows-py3.11-3.13.json), and the recorded CI head has [VERIFIED-CI records](docs/m1_impl/matrix_evidence/ci-run-37189754726.json). Those records preserve their original paths and apply only to the recorded snapshot/head, not this changed worktree.
