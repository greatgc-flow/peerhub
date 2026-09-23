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
pip install "git+https://github.com/greatgc-flow/peerhub.git@v0.7.0"
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

`peerhub --help` is the authoritative command inventory. Every current top-level command is listed here with its one-line purpose.

| Command | What it does |
|---|---|
| `peerhub workspace` | Manage the peerhub workspace itself |
| `peerhub status` | Show the current workspace status |
| `peerhub config` | Inspect peerhub's own resolved configuration |
| `peerhub backup` | Back up or restore one workspace |
| `peerhub adapter` | Manage peerhub adapters |
| `peerhub diag` | Show live peer diagnostics and quota telemetry |
| `peerhub broadcast` | Broadcast one prompt to multiple peers |
| `peerhub health` | Manage peer health |
| `peerhub peer` | Inspect and recover peer nodes |
| `peerhub lease` | Inspect session leases |
| `peerhub broker` | Inspect governance effect delivery status |
| `peerhub gate` | Check dispatch gate condition for an agent |
| `peerhub ask` | Send one prompt to a real peer CLI |
| `peerhub statusline` | Format live statusline for an AI peer |
| `peerhub consensus` | Manage consensus rounds |
| `peerhub task` | Manage task lifecycles |
| `peerhub lesson` | Manage governance lessons |
| `peerhub directive` | Manage governance directives |
| `peerhub node` | Manage the peer node registry |
| `peerhub lock` | Manage durable file locks |
| `peerhub artifact` | Manage durable named artifact records |
| `peerhub role` | Manage durable workspace role assignments |
| `peerhub routing` | Discover candidates and elect capability-fit leaders |
| `peerhub leadership` | Manage the workspace-global leadership slot |
| `peerhub feedback` | Manage the governance feedback journal |
| `peerhub error` | Record durable operational-error evidence |
| `peerhub alert` | Raise durable alerts for live room participants |
| `peerhub room` | Manage rooms and messages |
| `peerhub duty` | Manage terminal duty |
| `peerhub session` | Manage room-participation sessions |

## Try it

Use a separate workspace for this walkthrough; every command below refers to the same `./peerhub-demo` directory.

```bash
# Initialize the workspace first.
peerhub workspace init --workspace ./peerhub-demo

# Send one prompt, then fan the same kind of work out to two configured peers.
peerhub ask cx "Summarize this repository" --workspace ./peerhub-demo
peerhub broadcast "List one risk." --peers cx,ag --workspace ./peerhub-demo

# Create, start, and complete a task.
peerhub task create --workspace ./peerhub-demo --task-id docs-demo --summary "Refresh docs" --spec "Add a usage example." --creator cx
peerhub task claim-start --workspace ./peerhub-demo --task-id docs-demo --actor cx --request-id docs-demo-request --coordinator cx --attempt-id docs-demo-attempt
peerhub task complete --workspace ./peerhub-demo --task-id docs-demo --actor cx

# Propose a consensus round and cast its first vote.
peerhub consensus propose --workspace ./peerhub-demo --round-id docs-demo-round --title "Adopt docs" --question "Adopt the README update?" --body "Approve the proposed README example." --proposer cx --required cx,ag --eligible cx,ag
peerhub consensus vote --workspace ./peerhub-demo --round-id docs-demo-round --actor cx --choice agree
```

`ask` also accepts `--workspace PATH` (default `.`), `--profile PROFILE_ID`, `--timeout-seconds`/`--silence-timeout-seconds`/`--max-output-bytes` (process limits), and `--json`. Exit codes: `0` verified response, `2` usage/config/pre-spawn failure (unknown peer, executable not found, readiness probe failed), `3` definite peer/protocol failure, `4` uncertain execution (timeout, lost lease ownership), `130` interrupted. It requires the real peer CLI (`agy.exe`/`claude.cmd`/`codex.cmd`) to be installed and authenticated on your machine — `ask` will tell you clearly if it can't find or run one, rather than failing silently.

## Status

**Released as v0.7.0.** `peerhub ask` and the full governance/room/session command surface are real, working, and dispatch through peerhub's own governance, admission, and process-supervision layers today — not a stub. Since v0.6.0: every one of the CLI's 31 top-level command groups now has runnable `--help` usage examples, a `CONVENTION.md` and `CONTRIBUTING.md` (plus GitHub issue templates) were added, a real Windows file-lock retry gap in artifact materialization was fixed, and a packaging bug that broke the `fake` adapter kind for non-editable installs was fixed. D-CTX (dispatch-context credential verification, closing the consensus-vote impersonation gap) is fully wired end-to-end. As of this release, R4/P4b governance convergence is complete across every governance-decision domain (`feedback`, `artifact`, `file-lock`, `task`, `room`, `lesson`, `duty`, `error` review, `role`, `leadership`, `consensus`) — all route through a uniform `ApplicationAPI.submit()` gateway instead of bypassing it, with `consensus vote` exercising the gateway's verified-credential path. `docs/design/peerhub-production-call-map-R1.json` still lists 26 commands as `DIRECT_CLI_BYPASS` — this is a deliberate scope boundary, not an oversight: 21 are bootstrap/session/config/health/routing/directive/node-level administrative primitives (`workspace init`, `config migrate`/`init`, `backup workspace`/`restore`, `health revalidate`/`check`/`sweep`, `peer quarantine`/`recover`, `lease sweep`, `directive add`/`migrate`/`clear`, `node register`/`bind-profile`, `routing elect-leader`/`import-capabilities`, `session open`/`heartbeat`/`close`), and 5 are the core peer-dispatch primitives themselves (`ask`, `broadcast`, `diag`, `status`, `alert raise`) that intentionally predate/sit outside the governance-decision gateway. Documented in the call-map's own `known_gaps`/`scope_boundary` fields. `hub.py` remains the authoritative system for production multi-peer coordination; peerhub is a real, tested candidate replacement, not yet a cutover.

For the detailed development history — implementation status by feature, deferred items with their triggers, the hub.py-replacement roadmap, and the full architecture debate record — see [`docs/STATUS.md`](docs/STATUS.md).

## Run the tests

```bash
pytest -q                 # fast suite, no real CLI calls
pytest -q -m slow          # + the real-adapter integration tests (needs real CLIs installed & authenticated, real wall-clock time)
pytest -q -m e2e           # + genuine end-to-end `peerhub ask` dispatch through a real peer CLI (separate marker from slow -- also needs real CLIs)
pyright                    # static type check, should report 0 errors
```

## Contributing / reporting issues

See [CONTRIBUTING.md](CONTRIBUTING.md) to contribute, report a bug, or request a feature.

This repo's own convention (see `docs/history/design/2026-08/FACT-REFRESH-PROCEDURE-R1.md`) is to never cite a specific "current passing count" in this file — it changes with nearly every commit. Run `pytest -q` yourself for the real, current number.
