# peerhub

A lightweight, installable coordination layer for orchestrating multiple AI CLI agents (Claude, Codex, Antigravity, ...) as collaborating peers: dispatch, routing, consensus, and health. It's built to eventually replace an existing hand-rolled multi-peer coordination system (`hub.py`) with a proper, tested package.

## Status (2026-08-12)

**`peerhub ask` works end-to-end today** — it genuinely dispatches a prompt to a real peer CLI (agy/claude/codex) through peerhub's own governance, admission, and process-supervision layers, and returns the response. See "Try it" below.

- **Implemented**:
  - Coordination kernel: dispatch, process supervision, heartbeat/liveness, routing, health, telemetry, SQLite persistence.
  - GovernanceBroker's outbox/delivery-tracking split is fully completed end-to-end (legacy mirror writes removed, tables dropped).
  - Capability-lease enforcement (all 5 increments): required capability tier threaded end-to-end, with an atomic pre-spawn enforcement gate and explicitly documented evidence audit.
  - Persistence UoW split (read/write separation) and a migration-runner sequence-derivation fix that fast-fails on FK violations.
  - A typed command boundary (`ApplicationAPI`/`Client`) with Pydantic v2 strict validation at the wire edge.
  - **Real peer adapters** for all 3 target CLIs — `RealAgyAdapter`, `RealClaudeAdapter`, `RealCodexAdapter` — each proven both standalone and through the full supervised `dispatch_and_execute()` pipeline (not a bypass), plus a `FakePeerAdapter` for tests. Selectable via a peer-kind registry (`peerhub.adapters.registry`).
  - **A real CLI**: `peerhub --version`, `peerhub status [--workspace PATH]`, and `peerhub ask PEER PROMPT [options]` — the last one performs a genuine end-to-end dispatch (admission → routing → supervised process execution → decoded response), not a stub. See "Try it" below.
  - A direct-ask admission bootstrap (`peerhub.direct-ask/v1`) that auto-provisions a real, measured-readiness health/routing configuration for a single requested peer on a fresh workspace — no manual policy setup required.
  - Static type checking (Pyright, 0 errors) and CI (GitHub Actions: pytest + pyright on every push/PR).
  - A ratified traceability convention and fact-refresh procedure (`docs/design/TRACEABILITY-CONVENTION-R1.md`, `docs/design/FACT-REFRESH-PROCEDURE-R1.md`) — the fact-refresh tool (`tools/peerhub_facts/`) is built, functional, and handles drift reporting via live CLI probes.
- **Designed, but not yet built**:
  - Phase 3 dispatch-loop shared contract surface (retry-neutral classification, `TerminalClassification`, `DecoderEventKind.TOOL_CALL`) is ratified (`docs/design/PHASE3-DISPATCH-LOOP-CONTRACT-DESIGN-2026-08-12.md`) and validated via prototype, but the implementation increments are not yet started.
  - Multi-peer broadcast primitive A (correlation schema and fan-out) is designed and prototype-validated (`docs/design/PEERHUB-MULTIPEER-BROADCAST-DESIGN-2026-08-11.md`), and the database schema (migration 0020) has landed, but the `BroadcastCoordinator.fan_out()` loop is not yet built.
- **Explicitly deferred (with named triggers)**:
  - Alembic runtime cutover: Ratified as HOLD. The bespoke runner remains the sole runtime migration engine. Will revisit only if peerhub adopts SQLAlchemy ORM or is about to become the primary dispatch path.
  - Formal multi-peer consensus (voting machinery / Primitive B): Deferred until the first `r10_requires_finalized_for` decision class is actually routed to peerhub.
  - Durable response transcripts for broadcast: Deferred until a dispatch-layer durability mechanism is ratified.
  - Capability-lease enforcement evidence: Changing adapter receipts to claim positive enforcement is deferred until a machine-owned launcher, plan-bound digest, empirical negative probe, and post-plan corroboration gate exist.
- **Not yet implemented / honest gaps**:
  - Ctrl-C during `peerhub ask` does not yet walk the real cancellation ladder (SOFT_CANCEL → TERMINATE_TREE → KILL_TREE) — it prints a message that the in-flight process may still be running and exits 130, rather than falsely claiming a clean cancel. Wiring this needs a `dispatch_and_execute()` signature change (see the TODO at `peerhub/cli.py`).
  - Session continuation, streaming decode, detailed per-vendor error-taxonomy mapping, and PTY transport are deliberately out of scope for the current adapter slice.
  - No shadow-mode validation yet (routing a subset of real traffic through peerhub in parallel with `hub.py` for comparison before any real cutover) — `hub.py` remains the authoritative system for real multi-peer coordination work today; `peerhub ask` is a real, working command, not yet a production replacement.

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
peerhub ask ag "say hello in exactly three words" --capability-tier READ_ONLY
peerhub ask cc "..." --capability-tier READ_ONLY --profile <profile-id>   # claude, if you have more than one profile configured
peerhub ask cx "..." --capability-tier WORKTREE_WRITE --json              # structured output instead of plain text
```

`ask` accepts `--capability-tier` (required: READ_ONLY, WORKTREE_WRITE, GIT_MUTATE, REMOTE_MUTATE),
`--workspace PATH` (default `.`), `--profile PROFILE_ID`,
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
Schema Migrations Applied: 20
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
