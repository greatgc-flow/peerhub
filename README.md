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

`peerhub ask` works end-to-end today: it genuinely dispatches through peerhub's own governance, admission, and process-supervision layers and returns the real response. See "Key commands" below for the full reference, including the governance/room/session surface used by automated multi-peer coordination (consensus, task, lesson, room, duty, session, ...) — `peerhub --help` groups all of it into tiers so the everyday commands above aren't buried under the 30 total top-level commands, then prints the complete leaf-command catalog. `-h`, `--help`, and the Windows-style `/?` work at the root and after every nested command path.

## Install

### Option A: Install from PyPI (Recommended)

```bash
pip install peerhub
```

### Option B: Install an exact GitHub release

```bash
pip install "git+https://github.com/greatgc-flow/peerhub.git@v0.8.0"
```

The commands above install the stable `v0.8.0` line. Development changes after
that release remain on `main` until the next version is cut.

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

### Scenario cookbook (all 30 command groups)

These are safe starting points, not a second command specification. Run
`peerhub /?` for the generated 107-leaf catalog and `peerhub <path> /?` for
the authoritative arguments and description at any depth.

| Scenario | Example |
|---|---|
| Create a workspace | `peerhub workspace init --workspace ./peerhub-demo` |
| Inspect it | `peerhub status --workspace ./peerhub-demo --all` |
| Validate effective config | `peerhub config validate --workspace ./peerhub-demo --json` |
| Back it up | `peerhub backup workspace --workspace ./peerhub-demo --output ./backups` |
| Discover installed adapters | `peerhub adapter discover --json` |
| Inspect telemetry | `peerhub diag --fresh --json` (all quota windows include exact remaining fraction and reset time) |
| Ask one peer | `peerhub ask cx "Summarize this repository" --workspace ./peerhub-demo` |
| Ask several peers | `peerhub broadcast "List one risk." --peers cx,ag --workspace ./peerhub-demo` |
| Check health | `peerhub health check --workspace ./peerhub-demo --peer cc` |
| Inspect peer lifecycle | `peerhub peer status --workspace ./peerhub-demo --all` |
| Inspect process leases | `peerhub lease status --workspace ./peerhub-demo` |
| Inspect delivery backlog | `peerhub broker status --workspace ./peerhub-demo --json` |
| Check an admission gate | `peerhub gate check cc --workspace ./peerhub-demo` |
| Render a statusline | `peerhub statusline --peer ag --workspace ./peerhub-demo` |
| Review consensus rounds | `peerhub consensus list --workspace ./peerhub-demo` |
| Create governed work | `peerhub task create --workspace ./peerhub-demo --task-id docs-demo --summary "Refresh docs" --spec "Add a usage example." --creator cx` |
| Sweep expired lessons | `peerhub lesson sweep --workspace ./peerhub-demo` |
| Review directives | `peerhub directive list --workspace ./peerhub-demo` |
| Inspect registered nodes | `peerhub node list --workspace ./peerhub-demo` |
| Inspect file locks | `peerhub lock status --workspace ./peerhub-demo` |
| Inspect artifact claims | `peerhub artifact status --workspace ./peerhub-demo` |
| Inspect role assignments | `peerhub role status --workspace ./peerhub-demo` |
| Discover capability-fit routes | `peerhub routing discover --workspace ./peerhub-demo --needs review` |
| Inspect leadership | `peerhub leadership status --workspace ./peerhub-demo` |
| Review feedback gaps | `peerhub feedback list --workspace ./peerhub-demo` |
| Review operational errors | `peerhub error review list --workspace ./peerhub-demo` |
| Raise a room alert | `peerhub alert raise --workspace ./peerhub-demo --room-id docs-room --raiser-instance-id cx-1 --raiser-profile-id standard --message "Review blocked"` |
| Create a collaboration room | `peerhub room create --workspace ./peerhub-demo --room-id docs-room --topic-id docs --title "Docs review" --creator cx --participants cx,ag` |
| Inspect terminal duty | `peerhub duty status --workspace ./peerhub-demo --room-id docs-room` |
| Open a participant session | `peerhub session open --workspace ./peerhub-demo --workspace-scope-id demo --room-id docs-room --actor-principal-id cx --instance-id cx-1 --profile-id standard --session-fingerprint cx-1-demo` |

### Feedback loop

Use `feedback add → list → resolve` for product/process gaps, `error report →
review list → review resolve` for repeatable runtime failures, and `lesson
propose → approve → activate → retire/supersede` for rules learned from the
evidence. These are durable workspace records rather than loose notes. The
group-level `/?` pages contain copyable end-to-end examples. For package bugs,
use the GitHub issue templates and include `peerhub --version`, `peerhub config
paths --json`, `peerhub config validate --json`, and `peerhub diag --json`;
remove prompts/transcripts or secrets before attaching output.

## Status

**Released as v0.8.0.** All nine advertised AG/Claude/Codex profiles have
reviewed packaged model bindings and live dispatch evidence. `diag --live` now
polls the portable runtime without requiring an initialized workspace, labels
measured quota separately from executable-only discovery, and refuses to
recommend a critical quota/pacing target. The complete 107-leaf CLI supports
`-h`, `--help`, and `/?` recursively, with generated descriptions, argument
help, workflows, and feedback-loop examples. Adapter contracts and active docs
now match AG 1.2.12, Claude Code 2.1.283 stream JSON, and Codex 0.157.1 JSONL.
The full governance/room/session surface still dispatches through PeerHub's
governance, admission, and process-supervision layers; intentional direct CLI
boundaries remain enumerated in
`docs/design/peerhub-production-call-map-R1.json`.

For the detailed development history — implementation status by feature, deferred items with their triggers, the hub.py-replacement roadmap, and the full architecture debate record — see [`docs/STATUS.md`](docs/STATUS.md).

## Run the tests

```bash
pytest -q                 # fast suite, no real CLI calls
pytest -q -m slow          # + the real-adapter integration tests (needs real CLIs installed & authenticated, real wall-clock time)
pytest -q -m e2e           # + genuine end-to-end `peerhub ask` dispatch through a real peer CLI (separate marker from slow -- also needs real CLIs)
pyright                    # static type check, should report 0 errors
```

The deterministic suite runs on every push and pull request. The `slow` and
`e2e` tiers consume real provider quota, so they run on the dedicated
`peerhub-live` self-hosted runner for releases and for changes to adapters,
provider protocols, model bindings, or quota telemetry. PyPI publication is
blocked until both live tiers pass. The runner must have authenticated
`agy.exe`, `claude.cmd`, and `codex.cmd` commands on `PATH`.

## Contributing / reporting issues

See [CONTRIBUTING.md](CONTRIBUTING.md) to contribute, report a bug, or request a feature.

This repo's own convention (see `docs/history/design/2026-08/FACT-REFRESH-PROCEDURE-R1.md`) is to never cite a specific "current passing count" in this file — it changes with nearly every commit. Run `pytest -q` yourself for the real, current number.
