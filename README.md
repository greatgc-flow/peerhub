# peerhub

A lightweight, installable coordination layer for orchestrating multiple AI CLI agents (Claude, Codex, Antigravity, ...) as collaborating peers: dispatch, routing, consensus, and health. It's built to eventually replace an existing hand-rolled multi-peer coordination system (`hub.py`) with a proper, tested package.

## Status (2026-08-09)

**`peerhub ask` works end-to-end today** — it genuinely dispatches a prompt to a real peer CLI (agy/claude/codex) through peerhub's own governance, admission, and process-supervision layers, and returns the response. See "Try it" below.

- **Implemented**:
  - Coordination kernel: dispatch, process supervision, heartbeat/liveness, routing, health, telemetry, SQLite persistence with Alembic-baselined migrations.
  - GovernanceBroker's exclusive-claim, completion, and recovery paths all read/write through `effect_deliveries` (the outbox/delivery-tracking split completed end to end — see `docs/design/OUTBOX-SPLIT-PROGRESS-2026-08-09.md`).
  - A typed command boundary (`ApplicationAPI`/`Client`) with Pydantic v2 strict validation at the wire edge.
  - **Real peer adapters** for all 3 target CLIs — `RealAgyAdapter`, `RealClaudeAdapter`, `RealCodexAdapter` — each proven both standalone and through the full supervised `dispatch_and_execute()` pipeline (not a bypass), plus a `FakePeerAdapter` for tests. Selectable via a peer-kind registry (`peerhub.adapters.registry`).
  - **A real CLI**: `peerhub --version`, `peerhub status [--workspace PATH]`, and `peerhub ask PEER PROMPT [options]` — the last one performs a genuine end-to-end dispatch (admission → routing → supervised process execution → decoded response), not a stub. See "Try it" below.
  - A direct-ask admission bootstrap (`peerhub.direct-ask/v1`) that auto-provisions a real, measured-readiness health/routing configuration for a single requested peer on a fresh workspace — no manual policy setup required.
  - Static type checking (Pyright, 0 errors) and CI (GitHub Actions: pytest + pyright on every push/PR).
  - A ratified traceability convention and fact-refresh procedure for keeping design/code/test/commit/memory records in sync going forward (`docs/design/TRACEABILITY-CONVENTION-R1.md`, `docs/design/FACT-REFRESH-PROCEDURE-R1.md`) — the fact-refresh tool itself (`tools/peerhub_facts/`) is spec'd but not yet built.
- **Not yet implemented / honest gaps**:
  - Ctrl-C during `peerhub ask` does not yet walk the real cancellation ladder (SOFT_CANCEL → TERMINATE_TREE → KILL_TREE) — it prints a message that the in-flight process may still be running and exits 130, rather than falsely claiming a clean cancel. Wiring this needs a `dispatch_and_execute()` signature change (see the TODO at `peerhub/cli.py`).
  - Session continuation, streaming decode, detailed per-vendor error-taxonomy mapping, and PTY transport are deliberately out of scope for the current adapter slice.
  - No shadow-mode validation yet (routing a subset of real traffic through peerhub in parallel with `hub.py` for comparison before any real cutover) — `hub.py` remains the authoritative system for real multi-peer coordination work today; `peerhub ask` is a real, working command, not yet a production replacement.
  - The bespoke SQLite migration runner and Alembic scaffolding currently coexist (Alembic is additive-only, not yet the sole migration path).
  - `effect_receipts`' legacy-mirror writes into `outbox_events` are still active during the compatibility window (safe to leave running indefinitely, costs extra writes); dropping the legacy `outbox_events`/`outbox_checkpoints` tables entirely is still pending.

See [`docs/design/HUB-REPLACEMENT-ROADMAP-2026-08-09.md`](docs/design/HUB-REPLACEMENT-ROADMAP-2026-08-09.md) for the full phased plan toward functional hub.py parity, and [`docs/design/PEERHUB-P-DRIVE-ISOLATION-2026-08-09.md`](docs/design/PEERHUB-P-DRIVE-ISOLATION-2026-08-09.md) for how peerhub's own runtime state relates to (and is deliberately isolated from) the wider P: development environment this repo happens to live inside during development.

The target architecture was designed and converged through a 9-round adversarial review (`ag`/`cx`/`cc`) documented in [`docs/design/ARCHITECTURE.md`](docs/design/ARCHITECTURE.md). The full debate record, including rejected alternatives and evidence citations, is in [`docs/design/peerhub-architecture-debate.md`](docs/design/peerhub-architecture-debate.md). Later design decisions are under [`docs/design/`](docs/design/), dated by filename.

## Install

```bash
cd peerhub
pip install -e .          # runtime only
pip install -e .[dev]     # + pytest, pyright, hypothesis, alembic (needed to run tests/type-check locally)
```

Requires Python >= 3.11. This installs the `peerhub` package and registers a `peerhub` command on your PATH.

## Try it

```bash
peerhub --version

# Check a workspace (read-only; reports "uninitialized" if no database yet)
peerhub status --workspace ./my-workspace

# Genuinely dispatch a prompt to a real peer and get its response
peerhub ask ag "say hello in exactly three words"
peerhub ask cc "..." --profile <profile-id>   # claude, if you have more than one profile configured
peerhub ask cx "..." --json                    # structured output instead of plain text
```

`ask` accepts `--workspace PATH` (default `.`), `--profile PROFILE_ID`,
`--timeout-seconds`/`--silence-timeout-seconds`/`--max-output-bytes`
(process limits), and `--json`. Exit codes: `0` verified response,
`2` usage/config/pre-spawn failure (unknown peer, executable not found,
readiness probe failed), `3` definite peer/protocol failure, `4`
uncertain execution (timeout, lost lease ownership), `130` interrupted.
It requires the real peer CLI (`agy.exe`/`claude.cmd`/`codex.cmd`) to be
installed and authenticated on your machine — `ask` will tell you clearly
if it can't find or run one, rather than failing silently.

Example `status` output against a workspace with one active lease:
```
Workspace: /path/to/my-workspace
Database: /path/to/my-workspace/.peerhub/peerhub.sqlite3
Schema Migrations Applied: 15
Health Circuit ('system'): (no listing API exists yet -- not queryable from the CLI)
Active Leases: 1
Status: OK
```

## Run the tests

```bash
pytest -q                 # fast suite, no real CLI calls
pytest -q -m slow          # + the real-adapter/real-dispatch integration tests (needs real CLIs installed & authenticated, real wall-clock time)
pyright                    # static type check, should report 0 errors
```

This repo's own convention (see `docs/design/FACT-REFRESH-PROCEDURE-R1.md`)
is to never cite a specific "current passing count" in this file — it
changes with nearly every commit. Run `pytest -q` yourself for the real,
current number.
