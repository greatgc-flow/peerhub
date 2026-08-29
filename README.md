# peerhub

A lightweight, installable coordination layer for orchestrating multiple AI CLI agents (Claude, Codex, Antigravity, ...) as collaborating peers: dispatch, routing, consensus, and health. It's built to eventually replace an existing hand-rolled multi-peer coordination system (`hub.py`) with a proper, tested package.

## Status (2026-08-17)

**`peerhub ask` works end-to-end today** — it genuinely dispatches a prompt to a real peer CLI (agy/claude/codex) through peerhub's own governance, admission, and process-supervision layers, and returns the response. See "Try it" below.

- **Implemented**:
  - Coordination kernel: dispatch, process supervision, heartbeat/liveness, routing, health, telemetry, SQLite persistence.
  - GovernanceBroker's outbox/delivery-tracking split is fully completed end-to-end (legacy mirror writes removed, tables dropped).
  - Capability-lease enforcement (all 5 increments): required capability tier threaded end-to-end, with an atomic pre-spawn enforcement gate and explicitly documented evidence audit.
  - Persistence UoW split (read/write separation) and a migration-runner sequence-derivation fix that fast-fails on FK violations.
  - A typed command boundary (`ApplicationAPI`/`Client`) with Pydantic v2 strict validation at the wire edge.
  - **Real peer adapters** for all 3 target CLIs — `RealAgyAdapter`, `RealClaudeAdapter`, `RealCodexAdapter` — each proven both standalone and through the full supervised `dispatch_and_execute()` pipeline (not a bypass), plus a `FakePeerAdapter` for tests. Selectable via a peer-kind registry (`peerhub.adapters.registry`).
  - **A real CLI**: `peerhub --version`, `peerhub status [--workspace PATH] [--peer/--all]`, and `peerhub ask PEER PROMPT [options]` — the last one performs a genuine end-to-end dispatch (admission → routing → supervised process execution → decoded response), not a stub. See "Try it" below.
  - A direct-ask admission bootstrap (`peerhub.direct-ask/v1`) that auto-provisions a real, measured-readiness health/routing configuration for a single requested peer on a fresh workspace — no manual policy setup required.
  - Static type checking (Pyright, 0 errors) and CI (GitHub Actions: pytest + pyright on every push/PR).
  - A ratified traceability convention and fact-refresh procedure (`docs/design/TRACEABILITY-CONVENTION-R1.md`, `docs/design/FACT-REFRESH-PROCEDURE-R1.md`) — the fact-refresh tool (`tools/peerhub_facts/`) is built, functional, and handles drift reporting via live CLI probes.
  - The full T1 Phase 3 outer loop: `dispatch_with_retries()`, session resume, streaming, tool-call capture, and failover routing.
  - Multi-peer broadcast primitive A: Correlation schema and a working `BroadcastCoordinator.fan_out()` loop (T3).
  - `EvidenceArtifact` / 3-tier context partitioning (completed for Claude and Codex adapters).
  - Health/quota tracking CLI surface: `peerhub status --peer/--all` and quota telemetry persistence.
  - Ctrl-C during `peerhub ask` walks the real cancellation ladder (`SOFT_CANCEL` → `TERMINATE_TREE` → `KILL_TREE`) via a proper background-thread dispatch and cancellation hook.
- **Designed, but not yet built**:
  - Health/quota tracking's periodic background polling (`TelemetryWorker`) — currently awaiting a user decision on the process-host model (e.g., poll-on-demand vs. daemon).
  - Windows-native Brokered Read-Only Reducers — blocked pending a policy call on required OS privileges.
- **Explicitly deferred (with named triggers)**:
  - Alembic runtime cutover: Ratified as HOLD. The bespoke runner remains the sole runtime migration engine. Will revisit only if peerhub adopts SQLAlchemy ORM or is about to become the primary dispatch path.
  - Formal multi-peer consensus (voting machinery / Primitive B): Deferred until the first `r10_requires_finalized_for` decision class is actually routed to peerhub.
  - Durable response transcripts for broadcast: Deferred until a dispatch-layer durability mechanism is ratified.
  - Capability-lease enforcement evidence: Changing adapter receipts to claim positive enforcement is deferred until a machine-owned launcher, plan-bound digest, empirical negative probe, and post-plan corroboration gate exist.
  - Parallel fan-out: Deferred (blocked on measuring SQLite write contention).
- **Not yet implemented / honest gaps**:
  - Phase 4 shadow-by-ownership-cluster validation and same-revision comparison + rollback proof.
  - Crash-linkage recovery (resuming an interrupted round after a coordinator crash).
  - Detailed per-vendor error-taxonomy mapping, and PTY transport are deliberately out of scope for the current adapter slice.
  - No shadow-mode validation yet (routing a subset of real traffic through peerhub in parallel with `hub.py` for comparison before any real cutover) — `hub.py` remains the authoritative system for real multi-peer coordination work today; `peerhub ask` is a real, working command, not yet a production replacement.

See [`docs/design/HUB-REPLACEMENT-ROADMAP-2026-08-09.md`](docs/design/HUB-REPLACEMENT-ROADMAP-2026-08-09.md) for the full phased plan toward functional hub.py parity, and [`docs/design/PEERHUB-P-DRIVE-ISOLATION-2026-08-09.md`](docs/design/PEERHUB-P-DRIVE-ISOLATION-2026-08-09.md) for how peerhub's own runtime state relates to (and is deliberately isolated from) the wider P: development environment this repo happens to live inside during development.

**hub.py-replacement TDD (2026-08-27, in progress)**: real, tested code now exists for gap-2 (consensus), gap-4 (duty-lease), gap-5 (task lifecycle), gap-6 (governance/lessons) in full, and gap-3/gap-7 partially. **All 6 domains now have real, runnable CLI commands** — `peerhub consensus|task|lesson|room|duty|session`, see "Try it" below — and `LegacyTranslator` (`peerhub/application/legacy.py`) translates 42 of the 90 legacy `hub.py` action names into typed wire commands for these same domains (plus a new room-participation-session domain — `init-session`/`end-session`, backed by a new `RoomParticipationCoordinator`, a new private-mailbox domain — `send`/`check`/`mark-read`/`thread-promote`/`lesson-broadcast`, a final-arbiter escalation domain — `arbiter-review`, backed by `ArbiterReviewCoordinator`, a peer-node-registry domain — `register-node`/`list-nodes`, backed by `PeerRegistryService`, and a durable role-assignment domain — `assign-role`/`release-role`/`role-status`, backed by `RoleAssignmentService`). **All 42 of those actions now execute end-to-end**: `ApplicationAPI` registers a `CommandDescriptor` for each one, backed by the same real services the native CLI uses, so a legacy action name genuinely runs through `LegacyTranslator.translate()` → `Client.submit()` → a persisted result, not just a name-to-wire-command translation. See [`docs/design/HUB-REPLACEMENT-TDD-PROGRESS-2026-08-27.md`](docs/design/HUB-REPLACEMENT-TDD-PROGRESS-2026-08-27.md) for exactly what's real vs. still missing — notably, 48 of the 90 `LEGACY_CATALOG` actions remain entirely untranslated and unwired. **See [`docs/design/PEERHUB-BACKLOG-2026-08-27.md`](docs/design/PEERHUB-BACKLOG-2026-08-27.md) for the full consolidated remaining-work backlog**, organized by how ready each item is (mechanical wiring vs. needs new component code vs. needs a design round vs. entirely undesigned domain).

**hub.py-replacement design phase (2026-08-23 to 2026-08-26): DESIGN-complete, TDD-ready.** The 7 functional categories `hub.py` covers beyond basic dispatch — compat/cutover strategy, consensus, session/room/thread continuity, health/leadership/duty-lease, task lifecycle/approval, governance/learning, and diagnostics parity — each now have either a concrete `TargetState` JSON schema or a concrete dedicated design, all converged and ratified (52 of 53 remaining open items resolved by design-consistency reasoning, 1 genuine business decision resolved by the user, 1 shared infrastructure prerequisite scoped as its own task). Start at [`docs/design/HUB-REPLACEMENT-PRE-TDD-FINAL-RATIFICATION-2026-08-26.md`](docs/design/HUB-REPLACEMENT-PRE-TDD-FINAL-RATIFICATION-2026-08-26.md) (supersedes older per-doc "Unresolved" lists), then [`docs/design/HUB-REPLACEMENT-DESIGN-REINFORCEMENT-INDEX-2026-08-24.md`](docs/design/HUB-REPLACEMENT-DESIGN-REINFORCEMENT-INDEX-2026-08-24.md) for the full per-gap breakdown. **Update**: most of this design has since been implemented during the TDD phase below (consensus, task, lessons, duty-lease, and half of session/room/thread) — see [`docs/design/PEERHUB-BACKLOG-2026-08-27.md`](docs/design/PEERHUB-BACKLOG-2026-08-27.md) for exactly what from this design phase is still unimplemented.

The target architecture was designed and converged through a 9-round adversarial review (`ag`/`cx`/`cc`) documented in [`docs/design/ARCHITECTURE.md`](docs/design/ARCHITECTURE.md). The full debate record, including rejected alternatives and evidence citations, is in [`docs/design/peerhub-architecture-debate.md`](docs/design/peerhub-architecture-debate.md). Later design decisions are under [`docs/design/`](docs/design/), dated by filename.

## Install

### Option A: Install via Pip from GitHub Release (Recommended)

```bash
pip install "git+https://github.com/greatgc-flow/peerhub.git@v0.1.1"
```

### Option B: Local Editable Development Install

```bash
git clone https://github.com/greatgc-flow/peerhub.git
cd peerhub
pip install -e .          # runtime only
pip install -e .[dev]     # + pytest, pyright, hypothesis, alembic (needed to run tests/type-check locally)
```

Requires Python >= 3.11. This installs the `peerhub` package and registers `peerhub` and `hub` entrypoints on your PATH.

## Try it

```bash
peerhub --version

# Real-time multi-peer quota telemetry, headroom matrix, and active failover routing targets
peerhub diag
# Add a governed-domain state section (consensus/task/lesson) to the same command
peerhub diag --domains --workspace ./my-workspace

# Check a workspace (read-only; reports "uninitialized" if no database yet)
peerhub status --workspace ./my-workspace

# Genuinely dispatch a prompt to a real peer and get its response
peerhub ask ag "say hello in exactly three words" --capability-tier READ_ONLY
peerhub ask cc "..." --capability-tier READ_ONLY --profile <profile-id>   # claude, if you have more than one profile configured
peerhub ask cx "..." --capability-tier WORKTREE_WRITE --json              # structured output instead of plain text

# Multi-peer broadcast coordination across peers with unified consensus
peerhub broadcast "reply with exactly: pong" --peers ag,cx --capability-tier READ_ONLY

# Propose a consensus round
peerhub consensus propose --round-id r1 --title "Ship" --question "Ready?" --body "Decide" --proposer cx --required cx,ag --eligible cx,ag
# Create a task
peerhub task create --task-id t1 --summary "Ship" --spec "Do it" --creator cx
# Propose a governance lesson
peerhub lesson propose --lesson-id l1 --title "Rule" --rule "Do this" --category ops --severity HIGH --proposer cx --affected cx,ag
# Create a room
peerhub room create --room-id room1 --topic-id topic1 --title "Work" --creator cx --participants cx,ag
# Claim terminal duty
peerhub duty claim --room-id room1 --instance-id i1 --profile-id cx.standard --owner-principal-id p1 --authority-epoch 1
# Open a room-participation session
peerhub session open --workspace-scope-id ws1 --room-id room1 --actor-principal-id p1 --instance-id i1 --profile-id cx.standard --session-fingerprint fp1
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
Schema Migrations Applied: 24
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
