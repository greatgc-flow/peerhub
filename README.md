# peerhub

A lightweight, installable coordination layer for orchestrating multiple AI CLI agents (Claude, Codex, Antigravity, ...) as collaborating peers: dispatch, routing, consensus, and health. It's built to eventually replace an existing hand-rolled multi-peer coordination system (`hub.py`) with a proper, tested package.

## Status (last empirically re-verified 2026-09-09)

**`peerhub ask` works end-to-end today** — it genuinely dispatches a prompt to a real peer CLI (agy/claude/codex) through peerhub's own governance, admission, and process-supervision layers, and returns the response. See "Try it" below.

> **2026-09-07**: a real, 100%-reproducible regression made this claim false on any *fresh*
> workspace for a period (cli-probe evidence was unconditionally rejected by the admission
> gate, before any prompt dispatch was attempted) — found via empirical live-dispatch testing,
> not caught by the existing 1453-test suite (which never exercised a genuinely fresh
> bootstrap). Fixed and re-verified live for all three peers (cc/ag/cx) the same night; see
> `tests/e2e/` (the new empirical test tier this fix introduced) and the "peerhub ask Bootstrap
> Deadlock" project memory for the full account. The claim above is accurate again as of this
> commit, confirmed by direct execution, not by re-reading old test results.

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
  - The full T1 Phase 3 outer loop: `dispatch_with_retries()`, session resume, streaming, tool-call capture, and failover routing. *(Correction, 2026-09-09: this line previously overstated reality — the primitives existed but `peerhub ask`'s production path had zero caller for any of them until the fix noted below. Genuinely true now.)*
  - Multi-peer broadcast primitive A: Correlation schema and a working `BroadcastCoordinator.fan_out()` loop (T3).
  - `EvidenceArtifact` / 3-tier context partitioning (completed for Claude and Codex adapters).
  - Health/quota tracking CLI surface: `peerhub status --peer/--all` and quota telemetry persistence.
  - Ctrl-C during `peerhub ask` walks the real cancellation ladder (`SOFT_CANCEL` → `TERMINATE_TREE` → `KILL_TREE`) via a proper background-thread dispatch and cancellation hook.
  - **`peerhub ask` genuinely consumes its own durable primitives** (2026-09-09, see below): directive/lesson/room-context injection into the outgoing prompt, session resume via `dispatch_with_retries()`/`classify_and_open_circuit()`, a real resilient-dispatch retry loop with health-circuit-breaker integration, consensus/Final-Call resolutions bound to observable effect intents, oversized-prompt staging to a file (`prompt_reference`, instead of failing), and durable per-dispatch transcript storage. Adapter profile-tier parity (`ag`/`cc` now expose `effort`/`deepthink` tiers, matching `codex`), a reusable directive-digest canonicalization function, an automatic high-risk-mutation trigger for the DIR-005 arbiter, an enforcement gate on lesson activation, and Codex reset-credit read/consume primitives were added alongside it.
- **Designed, but not yet built**:
  - Health/quota tracking's periodic background polling as an ambient daemon process. Poll-on-demand (refreshing usage projections synchronously as part of a live command, e.g. `_refresh_usage_projections()`) is already implemented and shipping; only an always-running background daemon variant remains deferred.
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

See [`docs/design/HUB-REPLACEMENT-ROADMAP-2026-08-09.md`](https://github.com/greatgc-flow/peerhub/blob/main/docs/design/HUB-REPLACEMENT-ROADMAP-2026-08-09.md) for the full phased plan toward functional hub.py parity, and [`docs/design/PEERHUB-P-DRIVE-ISOLATION-2026-08-09.md`](https://github.com/greatgc-flow/peerhub/blob/main/docs/design/PEERHUB-P-DRIVE-ISOLATION-2026-08-09.md) for how peerhub's own runtime state relates to (and is deliberately isolated from) the wider P: development environment this repo happens to live inside during development. For the Engram/peerhub architectural separation itself (Engram is a portable dev-environment package, peerhub is the standalone AI-collaboration layer that used to live inside it) — what's done, what's verified clean in both directions, and what's left on either side — see [Engram's `2026-09-03_separation-completion-backlog.md`](https://github.com/greatgc-flow/Engram/blob/main/_sys/data/sessions/2026-09-03_separation-completion-backlog.md).

**hub.py-replacement TDD (2026-08-27 to 2026-09-02)**: real, tested code exists for gap-2 (consensus), gap-4 (duty-lease), gap-5 (task lifecycle), gap-6 (governance/lessons, capability matching) in full, and gap-3/gap-7 partially. **All 6 domains have real, runnable CLI commands** — `peerhub consensus|task|lesson|room|duty|session`, see "Try it" below — backed by dedicated services (`RoomParticipationCoordinator`, `ArbiterReviewCoordinator`, `PeerRegistryService`, `RoleAssignmentService`, `FeedbackService`, `OperationalErrorService`, `LeadershipService`, `RoomsService`, `ProposalCoordinator`, `ArtifactRecordService`, `CapabilityMatchingCoordinator`, and others). See [`docs/design/HUB-REPLACEMENT-TDD-PROGRESS-2026-08-27.md`](https://github.com/greatgc-flow/peerhub/blob/main/docs/design/HUB-REPLACEMENT-TDD-PROGRESS-2026-08-27.md) for exactly what's real, and [`docs/design/PEERHUB-BACKLOG-2026-08-27.md`](https://github.com/greatgc-flow/peerhub/blob/main/docs/design/PEERHUB-BACKLOG-2026-08-27.md) for the full consolidated remaining-work backlog, organized by how ready each item is (mechanical wiring vs. needs new component code vs. needs a design round vs. entirely undesigned domain), and [`docs/design/SESSION-LESSONS-INDEX-2026-09-02.md`](https://github.com/greatgc-flow/peerhub/blob/main/docs/design/SESSION-LESSONS-INDEX-2026-09-02.md) for a topic-indexed pointer into this session's recurring bug classes and process lessons.

**LegacyTranslator fully retired (2026-09-09)**: `hub.py`'s legacy CLI action names used to be exercised through a test-only translation shim (`LegacyTranslator`/`LEGACY_CATALOG` in `peerhub/application/legacy.py`) that mapped ~90 legacy action names to typed native commands. An empirical audit (2026-09-07) had already found this shim had zero production callers — `peerhub/cli.py`'s real, shipped CLI never called it; every native subcommand talks to its service layer directly — so it was a tested-but-unreachable compatibility layer, not a live parity surface. Since it had no production role, the shim (`LegacyTranslator`, `LEGACY_CATALOG`, and their supporting types) has now been deleted outright, across a 9-batch effort documented in [`docs/reviews/legacy-translator-retirement/`](https://github.com/greatgc-flow/peerhub/tree/main/docs/reviews/legacy-translator-retirement) (batch-01 through batch-09-final). The wire-protocol contracts it used to exercise indirectly are preserved directly, natively, in `tests/unit/application/test_command_wire_contracts.py`. `docs/design/phase0/migration-ledger-v2.json` (the legacy-action-to-command mapping ledger) is kept as a historical record, no longer authoritative.

**AI-collaboration production-integration gaps closed (2026-09-09).** A zero-base MECE audit comparing `P:\`'s legacy `hub.py` against peerhub's own shipped `ask` path found that peerhub had built most of `hub.py`'s durable coordination *primitives* (directive/lesson services, session-lease/binding machinery, retry/circuit-breaker code, consensus effect intents) but its production `ask` path didn't actually call most of them — a real production-parity gap, not the LegacyTranslator's already-closed compatibility-shim question above. Independently ratified via a dialectical review (two independent peer reviews, adjudicated by a third), then implemented end-to-end: `peerhub ask` now injects user/runtime directives, peer-filtered lessons, and room/task context into the outgoing prompt; performs real session resume with model-fingerprint validation; dispatches through the resilient retry loop with health-circuit-breaker integration instead of a single direct call; binds consensus/Final-Call resolutions to real, observable effect intents instead of a no-op default; stages oversized prompts to a file instead of failing; and durably persists the full dispatch transcript. Alongside this: `ag`/`cc` adapters gained `effort`/`deepthink` profile tiers (parity with `cx`, and with what `P:\`'s `orchestration.json` actually dispatches), a reusable directive-digest canonicalization function replaced an unreproducible hardcoded-digest table, the DIR-005 arbiter gained an automatic high-risk-mutation trigger (previously dissent-only and manually invoked), lesson activation is now gated on a real enforcement check (previously approval alone was sufficient), and Codex reset-credit read/consume primitives were added (mirroring `hub.py`'s `CodexAccountClient`; CLI exposure deliberately deferred). Full record, including the dialectical ratification and independent verification of every claim: [`docs/reviews/p-drive-mece-migration-audit-2026-09-09.md`](https://github.com/greatgc-flow/peerhub/blob/main/docs/reviews/p-drive-mece-migration-audit-2026-09-09.md).

**Released as v0.3.0.** Breaking change: `arbiter.json`/`proposals.json` now require a top-level `"schema_version": 1` at every layer, including an unmigrated legacy `.peerhub/*.json` file (add the key by hand, or run `peerhub config migrate`).

**Config dotdir consolidation (2026-09-09).** `arbiter.json` and `proposals.json` moved from `.peerhub/*.json` to the same `config/` tier `ask.toml` already used, and both files gained a global fallback layer (workspace → global → built-in safe default) with a required `schema_version` and diagnostics reporting which layer won each field. New commands: `peerhub config validate` (exercises every config family's real loader and reports OK/ERROR, surfacing the winning-layer diagnostics), `peerhub config init --scope global|workspace` (scaffolds a tier's `config/` directory with a commented starter file), `peerhub config migrate` (moves a legacy file to `config/` after validating it, refusing outright if both locations exist), and `peerhub backup workspace`/`restore` (a live SQLite-online-backup-API snapshot bundled with `config/`, with dispatch-transcript inclusion an explicit opt-in, never implicit). Full hierarchy, precedence, and privacy rules: [`docs/config-hierarchy.md`](https://github.com/greatgc-flow/peerhub/blob/main/docs/config-hierarchy.md); full design record: [`docs/design/dotdir-consolidation-RATIFIED-2026-09-09.md`](https://github.com/greatgc-flow/peerhub/blob/main/docs/design/dotdir-consolidation-RATIFIED-2026-09-09.md).

**hub.py-replacement design phase (2026-08-23 to 2026-08-26): DESIGN-complete, TDD-ready.** The 7 functional categories `hub.py` covers beyond basic dispatch — compat/cutover strategy, consensus, session/room/thread continuity, health/leadership/duty-lease, task lifecycle/approval, governance/learning, and diagnostics parity — each now have either a concrete `TargetState` JSON schema or a concrete dedicated design, all converged and ratified (52 of 53 remaining open items resolved by design-consistency reasoning, 1 genuine business decision resolved by the user, 1 shared infrastructure prerequisite scoped as its own task). Start at [`docs/design/HUB-REPLACEMENT-PRE-TDD-FINAL-RATIFICATION-2026-08-26.md`](https://github.com/greatgc-flow/peerhub/blob/main/docs/design/HUB-REPLACEMENT-PRE-TDD-FINAL-RATIFICATION-2026-08-26.md) (supersedes older per-doc "Unresolved" lists), then [`docs/design/HUB-REPLACEMENT-DESIGN-REINFORCEMENT-INDEX-2026-08-24.md`](https://github.com/greatgc-flow/peerhub/blob/main/docs/design/HUB-REPLACEMENT-DESIGN-REINFORCEMENT-INDEX-2026-08-24.md) for the full per-gap breakdown. **Update**: most of this design has since been implemented during the TDD phase below (consensus, task, lessons, duty-lease, and half of session/room/thread) — see [`docs/design/PEERHUB-BACKLOG-2026-08-27.md`](https://github.com/greatgc-flow/peerhub/blob/main/docs/design/PEERHUB-BACKLOG-2026-08-27.md) for exactly what from this design phase is still unimplemented.

The target architecture was designed and converged through a 9-round adversarial review (`ag`/`cx`/`cc`) documented in [`docs/design/ARCHITECTURE.md`](https://github.com/greatgc-flow/peerhub/blob/main/docs/design/ARCHITECTURE.md). The full debate record, including rejected alternatives and evidence citations, is in [`docs/design/peerhub-architecture-debate.md`](https://github.com/greatgc-flow/peerhub/blob/main/docs/design/peerhub-architecture-debate.md). Later design decisions are under [`docs/design/`](https://github.com/greatgc-flow/peerhub/tree/main/docs/design), dated by filename.

## Install

### Option A: Install from PyPI (Recommended)

```bash
pip install peerhub
```

### Option B: Install an exact GitHub release

```bash
pip install "git+https://github.com/greatgc-flow/peerhub.git@v0.1.11"
```

### Option C: Local editable development install

```bash
git clone https://github.com/greatgc-flow/peerhub.git
cd peerhub
pip install -e .          # runtime only
pip install -e .[dev]     # + pytest, pyright, hypothesis, alembic (needed to run tests/type-check locally)
```

Requires Python >= 3.11. This installs the `peerhub` package and registers a `peerhub` entrypoint on your PATH (verified via a real sdist build + install: `pyproject.toml`'s `[project.scripts]` defines only `peerhub`, not a separate `hub` alias).

## Try it

```bash
peerhub --version

# Real-time multi-peer quota telemetry, headroom matrix, and active failover routing targets
peerhub diag
# Add a governed-domain state section (consensus/task/lesson) to the same command
peerhub diag --domains --workspace ./my-workspace

# Check a workspace (read-only; reports "uninitialized" if no database yet)
peerhub status --workspace ./my-workspace

# Auto-detect which built-in peer CLIs (agy/claude/codex) are installed and
# resolvable on PATH right now -- no workspace required
peerhub adapter discover
peerhub adapter discover --json   # MEASURED/UNAVAILABLE/ABSENT per peer

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

# Config hierarchy: see resolved paths, validate every layer, or scaffold one
peerhub config paths --workspace .
peerhub config validate --workspace .
peerhub config init --scope workspace --workspace .

# Back up a workspace (live SQLite snapshot + config/ files), then restore it
peerhub backup workspace --workspace . --output ./backups
peerhub backup restore ./backups/peerhub-backup-<...> --workspace .
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
