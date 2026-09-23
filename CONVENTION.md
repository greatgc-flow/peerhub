# peerhub — Coding Conventions

This document records the conventions already in force across the codebase
(as observed, not aspirational). Update it when a convention changes rather
than letting code and doc drift apart.

---

## 1. Package Layout

`peerhub/` is organized by architectural layer, not by feature:

```
peerhub/
  core/          protocol primitives, identity, envelopes
  adapters/      per-peer-CLI adapters (registry, discovery)
  dispatch/      command execution, materialization, retry/lease machinery
  application/   use-case orchestration (workflows) over the above
  governance/    consensus, proposals, quarantine, role assignment
  routing/       capability matching and target-selection policy
  persistence/   SQLite state store, migrations, unit-of-work
  state/         feature-independent transactional state-store ports
  telemetry/     usage/quota measurement and feedback
  events/        event contract (envelopes, log records, offsets)
  health/        health checks and fault-boundary reporting
  cli/commands/  one module per CLI subcommand
  builtins/      default configuration/data shipped with the package
  config_data/   packaged TOML configuration defaults
  client.py      root-level embedded-client facade
  runtime.py     root-level production-composition facade
```

New code belongs in the layer that owns its concern, not next to whatever
happened to call it first. If a new concern doesn't fit an existing layer,
that's a design decision (see `docs/design/`), not a place to improvise a
new top-level package.

## 2. Test Layout

- `tests/unit/` mirrors the `peerhub/` package tree (`tests/unit/dispatch/`,
  `tests/unit/governance/`, ...) for tests that need no real I/O. A handful
  of unit tests that don't map to one subpackage cleanly live flat at
  `tests/unit/test_*.py` — that's an accepted pattern, not a violation.
- `tests/integration/` mirrors the same layers for tests that exercise a
  real `SqliteStateStore`, real subprocess dispatch, or multi-component
  wiring (`tests/integration/persistence/`, `tests/integration/dispatch/`).
- `tests/contract/` holds cross-version/compatibility contract tests
  (e.g. Phase-0 wire-shape compatibility).
- `tests/e2e/` holds real external-peer tests. Every test in this tier uses
  `@pytest.mark.e2e` so it is excluded from the default local suite unless
  explicitly selected.
- `tests/static/` holds source and architecture assertions that inspect the
  repository's declared structure without exercising runtime integration.
- `tests/fakes.py` is a shared root-level module (in-memory fakes for
  `UnitOfWork`, adapters, etc.) imported via `from tests.fakes import ...`
  across both `unit/` and `integration/`. It stays at `tests/` root
  precisely because it is shared by both trees — do not move it into
  either subtree or duplicate it.
- Mark slow or end-to-end tests with `@pytest.mark.slow` / `@pytest.mark.e2e`
  (`pyproject.toml` deselects both by default via `addopts`); don't invent a
  third ad-hoc marker for the same purpose.
- A new test file goes next to its siblings for the module under test, by
  the same unit-vs-integration rule above — not at whichever `tests/`
  location is fastest to type.

## 3. Result / Status Modeling

Outcomes that can fail in more than one *meaningfully different* way are
modeled as an explicit `enum.Enum`, not a bare `bool` or an unstructured
exception, and the enum distinguishes **permanent** failure from
**retryable** failure wherever the distinction is real (see
`peerhub.dispatch.materializer.MaterializationStatus`:
`HARD_FAILURE` vs `RETRYABLE_FAILURE`). A caller further up the stack (e.g.
retry-authority / attempt scheduling) relies on that distinction to decide
whether to schedule another attempt — collapsing both into one generic
"error" status silently removes that signal.

## 4. Windows File-System Safety

Any atomic swap (`os.replace`, `Path.rename`) on a path that could be
touched by antivirus, a search indexer, or a just-terminated child process
must retry a bounded number of times with backoff before surfacing failure
— a bare, unretried `PermissionError` on first attempt is a false negative
on Windows. See `ArtifactMaterializer._replace_with_retry` (5 attempts,
0.1s * 2^n backoff) for the reference pattern; Engram's
`provisioner.py::_safe_rename` uses the identical shape for the same class
of bug. Do not retry unboundedly, and do not swallow the final failure —
surface it as a retryable (not silently-dropped) result once attempts are
exhausted.

## 5. CLI Scope Filters

A CLI flag that accepts a comma-separated scope/target list (e.g.
broadcast's target list in `cli/commands/daily.py`) must normalize and
validate every entry against a known set *before* doing any real work
(network calls, dispatch, mutation) — reject unknown values up front with
a clear error rather than silently ignoring them or failing mid-operation.

## 6. Paths

No hardcoded drive letters or absolute-path assumptions in library code;
resolve everything relative to the workspace root / `sys_dir`-equivalent
passed in by the caller. `pyrightconfig.json` type-checks under
`pythonPlatform: "Linux"` specifically so cross-platform path bugs surface
in CI rather than only on the Windows machines this is normally run on —
don't "fix" a pyright error by assuming Windows-only path semantics.

## 7. Type Checking

`pyright --project pyrightconfig.json` runs in `strict` mode over
`peerhub/` (tests and tools are excluded). Code should be pyright-clean
before merge; don't add `# type: ignore` to work around a real type error
without a one-line comment explaining why it's a false positive.

## 8. Docstrings and Design Traceability

Module/class docstrings that implement a specific ratified design
reference it by path (e.g. "Covers the `ArtifactMaterializer` contract
ratified in `docs/history/design/2026-08/SLICE5-KICKOFF-R1.md`"). When you
implement a design doc's contract, add that reference; when a design
changes, update the doc and the docstring together.

---

See also: [`docs/design/README.md`](docs/design/README.md) for the design
process, [`docs/reviews/`](docs/reviews/) for point-in-time audits, and
[`docs/history/`](docs/history/) for archived/superseded material (including
`docs/history/from-engram-repo/protocol-docs/CONVENTION.md`, the pre-split
monorepo's convention doc — Engram-specific and no longer authoritative for
this repo).
