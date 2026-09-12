# peerhub

A lightweight, installable coordination layer for orchestrating multiple AI CLI agents (Claude, Codex, Antigravity, ...) as collaborating peers: dispatch, routing, consensus, and health. It's built to eventually replace an existing hand-rolled multi-peer coordination system (`hub.py`) with a proper, tested package.

## Quick start

```bash
pip install peerhub

# Auto-detect which peer CLIs (agy/claude/codex) are installed and ready
peerhub adapter discover

# Genuinely dispatch a prompt to a real peer and get its response
peerhub ask ag "say hello in exactly three words"

# Live multi-peer quota telemetry, headroom, and failover routing
peerhub diag

# Check a workspace (reports "uninitialized" if no database yet)
peerhub status
```

`peerhub ask` works end-to-end today: it genuinely dispatches through peerhub's own governance, admission, and process-supervision layers and returns the real response. See "Key commands" below for the full reference, including the governance/room/session surface used by automated multi-peer coordination (consensus, task, lesson, room, duty, session, ...) — `peerhub --help` groups all of it into tiers so the everyday commands above aren't buried under the ~30 total top-level commands.

## Install

### Option A: Install from PyPI (Recommended)

```bash
pip install peerhub
```

### Option B: Install an exact GitHub release

```bash
pip install "git+https://github.com/greatgc-flow/peerhub.git@v0.3.0"
```

### Option C: Local editable development install

```bash
git clone https://github.com/greatgc-flow/peerhub.git
cd peerhub
pip install -e .          # runtime only
pip install -e .[dev]     # + pytest, pyright, hypothesis, alembic (needed to run tests/type-check locally)
```

Requires Python >= 3.11. This installs the `peerhub` package and registers a `peerhub` entrypoint on your PATH (verified via a real sdist build + install: `pyproject.toml`'s `[project.scripts]` defines only `peerhub`, not a separate `hub` alias). `python -m peerhub` works too.

## Key commands

| Command | What it does |
|---|---|
| `peerhub adapter discover` | Find which peer CLIs (agy/claude/codex) are installed and ready |
| `peerhub ask PEER PROMPT` | Dispatch a prompt to a real peer CLI (defaults to `--capability-tier READ_ONLY`) |
| `peerhub broadcast PROMPT --peers ag,cx` | Fan out one prompt to multiple peers with unified consensus |
| `peerhub diag` | Live multi-peer quota telemetry, headroom, and failover routing |
| `peerhub status` | Workspace health check |
| `peerhub config validate` | Check every config layer (workspace/global/built-in) resolves cleanly |
| `peerhub backup workspace` / `backup restore` | Live SQLite-snapshot backup and restore |

See `peerhub --help` for the full command list — including the governance/room/session surface (`consensus`, `task`, `lesson`, `room`, `duty`, `session`, ...), grouped into its own tier since it's primarily an API contract for automated multi-peer coordination (hub.py-parity), not something a human types day to day.

`ask` also accepts `--workspace PATH` (default `.`), `--profile PROFILE_ID`, `--timeout-seconds`/`--silence-timeout-seconds`/`--max-output-bytes` (process limits), and `--json`. Exit codes: `0` verified response, `2` usage/config/pre-spawn failure (unknown peer, executable not found, readiness probe failed), `3` definite peer/protocol failure, `4` uncertain execution (timeout, lost lease ownership), `130` interrupted. It requires the real peer CLI (`agy.exe`/`claude.cmd`/`codex.cmd`) to be installed and authenticated on your machine — `ask` will tell you clearly if it can't find or run one, rather than failing silently.

## Status

**Released as v0.3.0.** `peerhub ask` and the full governance/room/session command surface are real, working, and dispatch through peerhub's own governance, admission, and process-supervision layers today — not a stub. `hub.py` remains the authoritative system for production multi-peer coordination; peerhub is a real, tested candidate replacement, not yet a cutover.

For the detailed development history — implementation status by feature, deferred items with their triggers, the hub.py-replacement roadmap, and the full architecture debate record — see [`docs/STATUS.md`](docs/STATUS.md).

## Run the tests

```bash
pytest -q                 # fast suite, no real CLI calls
pytest -q -m slow          # + the real-adapter/real-dispatch integration tests (needs real CLIs installed & authenticated, real wall-clock time)
pyright                    # static type check, should report 0 errors
```

This repo's own convention (see `docs/design/FACT-REFRESH-PROCEDURE-R1.md`) is to never cite a specific "current passing count" in this file — it changes with nearly every commit. Run `pytest -q` yourself for the real, current number.
