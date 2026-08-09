# peerhub fact-refresh procedure (R1)

Status: RATIFIED 2026-08-09 (procedure only — `tools/peerhub_facts/` itself
is not yet implemented; this document is what a future implementation
must satisfy). cx proposed, ag independently verified and required 2
corrections (incorporated below), cc synthesized. Scope: peerhub only.

## Why

Tonight's session repeatedly rediscovered facts by hand mid-dispatch —
that `agy.exe --version` is free and instant, that Codex emits sandbox
warnings before its real version line, that `_resolve_executable_path`
needs to check the literal name before PATHEXT-appending, that the test
count climbed from 479 to 528 across the night with no single number
ever being "the" count. None of this was captured anywhere durable
enough that a future session wouldn't have to rediscover it. This
procedure exists to make that capture routine instead of incidental.

## What it checks

A manually-invoked routine (`python -m tools.peerhub_facts`, plus a
`--live` flag for real-dispatch checks) performs, in order:

1. Resolve all 3 peer aliases via `resolve_peer_target()`.
2. **Graceful degradation** (ag's correction): if any peer's executable
   isn't resolvable on the machine running the routine (e.g. a dev box
   without `claude.cmd`/`codex.cmd` installed), record that peer as
   `ABSENT` and continue — do not crash (exit 2) or treat a missing
   local install as structural drift (exit 1). A routine that requires
   all 3 real CLIs installed to run at all would be unusable for most
   contributors.
3. For each resolvable peer: run `--version` through the same supervised
   pipe mechanism `bootstrap.py`'s readiness probe already uses (not a
   bare unsupervised subprocess call), parse the version output
   **vendor-specifically** (not raw string comparison — Codex's
   `--version` output includes sandbox/path warnings before the actual
   version line; a naive exact-match would report false drift on every
   run), and compare against a recorded `verified_versions` list.
4. Run `--help` and check for required semantic tokens per peer (e.g.
   agy needs `-p`/`--output-format`) — again semantic token presence,
   not exact-text comparison, since incidental wording/whitespace
   changes in vendor help text aren't real drift.
5. Run each adapter's decoder against a fixture/conformance case per
   output protocol (agy's flat JSON, Claude's `result`/`is_error`
   shape, Codex's JSONL `item.completed` events).
6. **Dependency versions** (ag's correction — this was stated
   incorrectly in the original draft): `pyproject.toml` only holds
   *declared constraints* (e.g. `pytest>=7.0`), not what's actually
   installed. Read real installed versions via `importlib.metadata` or
   `pip list --format=json` against the active environment, evaluate
   them against `pyproject.toml`'s specifiers, and run `pip check` for
   coherence. Report the absence of a lockfile as `UNLOCKED`, not a
   failure — adopting a lock format (`uv.lock`, etc.) is a separate
   packaging decision this routine does not make on its own.
7. Run the full default `pytest -q` fresh and record HEAD SHA,
   pass/fail/deselected counts, duration, and a raw-output digest.

`--live` additionally runs the real, slow, actually-shells-out adapter
conformance tests for all 3 peers (the existing
`test_real_{agy,claude,codex}_adapter*.py` family) — version/help
success alone cannot prove an output schema still matches what the
decoders expect. Do not run `--live` on every ordinary invocation; it
spends real peer usage.

## Where the expected facts live

```
tools/peerhub_facts/
    __init__.py
    __main__.py
    collectors.py      # probes: version/help/decoder/dependency/pytest
    compare.py          # expected-vs-observed, produces PASS/DRIFT/...
    model.py             # the report data shapes

docs/compatibility/
    peer-cli-contracts.toml      # machine-checkable expected facts
    peer-cli-observations.md     # durable human-readable discovery log
```

`peer-cli-contracts.toml` records **semantic** expectations, not raw
snapshots — e.g. `verified_versions`, `required_help_tokens`,
`output_protocol`, `required_output_fields` per peer. Do not conflate an
adapter's internal `adapter_version` field (currently a static
placeholder like `"1.0.0"`) with the real external CLI's version.

`peer-cli-observations.md` is where empirically-discovered facts get a
durable home instead of being rediscovered every session — one entry
per discovery, each with: trace unit (per the traceability convention),
peer + external CLI version, timestamp + source tag, a redacted
reproducer, the observed behavior, which adapter symbol depends on it, a
regression test covering it, and an `ACTIVE`/`SUPERSEDED` status. The
literal-name/PATHEXT resolution behavior, the free/instant `--version`
probes, Claude's warning-prefix handling, and Codex's JSONL event shape
all belong here as the first entries once this is implemented.

Explicitly **do not** extend the existing
`tools/drift_report/generate_drift_report.py` — verified (both cx and
ag independently) that it's a legacy-hub-surface comparator, parsing
`action_vector`/`argparse_surface`/shared-helper call-graphs against a
`legacy-hub-surface-current.json` baseline. Bolting peerhub-specific
semantic checks onto it would mix an unrelated legacy Phase-0 tool with
Phase 3+ domain logic.

## Output contract

Reports write only under the already-gitignored `build/` tree:

```
build/peerhub-facts/latest.json
build/peerhub-facts/latest.md
```

Per fact: `status` (`PASS | DRIFT | REVIEW_REQUIRED | ERROR | NOT_RUN`),
`expected`, `observed`, `source_tag`, the probe command run, exit code,
an evidence digest, and a recommended action.

Process exit code: `0` all mandatory checks pass; `1` semantic drift or
human review required; `2` a mandatory probe or the test suite itself
failed to run (distinct from a peer simply being `ABSENT`, which is not
an error).

**Nothing is auto-applied except generating these reports.** Expected
versions, required flags, decoder schemas, dependency constraints, and
compatibility prose are never auto-updated by this routine — doing so
would bless observed drift just because it was observed, exactly the
failure mode the routine exists to catch.

## Test counts

Do not maintain a living "current test count" anywhere in normative
documentation. Tonight's own commits cited wildly different counts as
work progressed (479 through 528) and an earlier dated summary cited
415 — none of these were ever "the" count, only a snapshot at a
specific SHA. The routine should display:

```
Current run:  N passed at <HEAD>
Last cited:   528/528 at c77ebbb
Delta:        +X tests (informational only)
```

A changed count on a green run is not drift. Counts are permitted only
inside dated session summaries and commit bodies, always paired with
the commit SHA they describe. Living/normative docs should say "run
`pytest -q`", never cite a specific number as current.

## Cadence

- Run the default routine at the start of any session that will touch
  adapters, runtime composition, dependencies, or CLI execution.
- Run it before and after starting each new roadmap phase.
- Run it immediately after installing or updating any adapted peer CLI
  or any dependency.
- Run `--live` when a CLI version/help surface has changed, when
  adapter planning/decoding logic changes, before closing a phase, and
  before any release.

## Implementation status

This document is the ratified procedure. `tools/peerhub_facts/` itself
is **not yet built** — this is the next concrete implementation task,
to be done with the same dispatch → independent-verification discipline
as every other increment this session, not assumed complete just
because the design converged.
