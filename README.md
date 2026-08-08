# peerhub

A lightweight, installable coordination layer for orchestrating multiple AI CLI agents (Claude, Codex, Antigravity, ...) as collaborating peers: dispatch, routing, consensus, and health. It's built to eventually replace an existing hand-rolled multi-peer coordination system (`hub.py`) with a proper, tested package.

## Status (2026-08-08)

Implementation in progress. The coordination kernel, command boundary, and a first slice of real (non-mocked) peer adapters are functional and tested.

- **Implemented**:
  - Coordination kernel: dispatch, process supervision, heartbeat/liveness, routing, health, telemetry, SQLite persistence with Alembic-baselined migrations.
  - A typed command boundary (`ApplicationAPI`/`Client`) with Pydantic v2 strict validation at the wire edge for all 3 registered commands.
  - **Real peer adapters** for all 3 target CLIs — `RealAgyAdapter`, `RealClaudeAdapter`, `RealCodexAdapter` — each proven by an integration test that actually shells out to the real binary (not mocked), plus a `FakePeerAdapter` for tests. Selectable via a small peer-kind registry (`peerhub.adapters.registry`), wired into `create_runtime()` (defaults to the fake adapter).
  - A first CLI entrypoint: `peerhub --version` and `peerhub status [--workspace PATH]` (see below).
  - Static type checking (Pyright, 0 errors) and CI (GitHub Actions: pytest + pyright on every push/PR).
  - 477 passing tests (`pytest -q`; add `-m slow` to also run the 3 real-CLI adapter tests, which need the actual `agy.exe`/`claude.cmd`/`codex.cmd` binaries installed and authenticated, and take real wall-clock time).
- **Not yet implemented / honest gaps**:
  - No live end-to-end multi-peer orchestration through peerhub itself — `hub.py` remains the authoritative system for real multi-peer work today. The real adapters exist and work individually, but nothing wires them into a running orchestration loop yet.
  - No diag-style live health/quota dashboard. `peerhub status` reports what it can honestly answer today (schema version, active lease count) and says so plainly where the underlying service doesn't expose a queryable answer yet (e.g. health-circuit listing).
  - Session continuation, streaming decode, detailed per-vendor error-taxonomy mapping, and PTY transport are deliberately out of scope for the current adapter slice.
  - The bespoke SQLite migration runner and the newly-added Alembic scaffolding currently coexist (Alembic is additive-only, not yet the sole migration path).

The target architecture was designed and converged through a 9-round adversarial review (`ag`/`cx`/`cc`) documented in [`docs/design/ARCHITECTURE.md`](docs/design/ARCHITECTURE.md). The full debate record, including rejected alternatives and evidence citations, is in [`docs/design/peerhub-architecture-debate.md`](docs/design/peerhub-architecture-debate.md). Later design decisions (Stage 2 command boundary, Stage 3 real adapters, the capability/mutation-lease proposal) are under [`docs/design/`](docs/design/), dated by filename.

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

# Check a workspace (creates nothing -- read-only; reports "uninitialized" if the
# workspace has no database yet)
peerhub status --workspace ./my-workspace
```

Example output against an initialized workspace with one active lease:
```
Workspace: /path/to/my-workspace
Database: /path/to/my-workspace/.peerhub/peerhub.sqlite3
Schema Migrations Applied: 13
Health Circuit ('system'): (no listing API exists yet -- not queryable from the CLI)
Active Leases: 1
Status: OK
```

To exercise a real adapter directly (not through any CLI yet -- this is library-level usage):
```python
from peerhub.adapters.agy_adapter import RealAgyAdapter
from peerhub.adapters.contract import AdapterRequest, SessionAction, TransportLimits

adapter = RealAgyAdapter()
# see tests/integration/adapters/test_real_agy_adapter.py for a complete working example
# of plan_invocation -> real subprocess -> interpret_output
```

## Run the tests

```bash
pytest -q                 # fast suite, ~1 minute, no real CLI calls
pytest -q -m slow          # + the 3 real-adapter integration tests (needs real CLIs, ~30-60s more)
pyright                    # static type check, should report 0 errors
```
