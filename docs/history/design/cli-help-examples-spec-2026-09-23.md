# Spec: recursive CLI --help usage examples

**Status:** ratified for implementation, staged rollout.
**Trigger:** user asked for peerhub's `--help` to be recursively MECE with
usage-scenario examples at every level (already true for flags; examples
were missing). Companion Engram work already shipped as the style
reference (see Engram PR #2, `_sys/core/updater.py` etc. — plain
`command — one-line "when you'd use it"` lines, not prose).

## Scope (MECE partition into 4 stages — implement + test + review each
before starting the next; do not start stage N+1 until stage N's tests
and pyright are both green and the diff has been reviewed)

- **Stage 1** (do first): top-level `peerhub --help` epilog ("Common
  workflows": workspace init, ask, broadcast, status/diag, one task
  example) + README.md expansion (one-line description for every
  top-level group + a short "Try it" walkthrough for: workspace init,
  ask, broadcast, task lifecycle create→claim-start→complete, consensus
  propose→vote).
- **Stage 2** — Setup & config groups: `workspace`, `adapter`, `config`,
  `backup`.
- **Stage 3** — Operations/peer-infrastructure groups: `health`, `peer`,
  `lease`, `gate`, `node`, `routing`, `leadership`, `broker`.
- **Stage 4** — Governance groups: `consensus`, `task`, `lesson`,
  `directive`, `room`, `duty`, `lock`, `artifact`, `role`, `feedback`,
  `error`, `alert`, `session`, `statusline`, and any other group the
  implementer finds via `python -m peerhub --help` that isn't listed here
  (this list may be stale — the live `--help` output is the source of
  truth for the full group inventory, not this doc).

## Per-group requirement (exception case, applies to every group in every
stage — MECE: every group gets exactly this, no group is skipped, no
group gets more than this without a reason)

Add `epilog=` to that subparser with 1-3 concrete example invocations —
the ones an actual user would run for that group's most common actions,
not every leaf subcommand (the group's own `--help` already lists those
exhaustively). Use `formatter_class=argparse.RawDescriptionHelpFormatter`
so line breaks render as written (match whatever import/usage pattern
already exists for this in `peerhub/cli/__init__.py`, if any — otherwise
add it consistently). Format:

```
Examples:
  peerhub <group> <subcommand> <required-args>       one-line: when/why
  peerhub <group> <subcommand> <required-args> --flag   one-line: when/why
```

**Exception case — a group needs no example** (e.g. a diagnostic-only
group with one obvious subcommand): still add a 1-line epilog rather than
omitting it, so the recursive-MECE property holds (every group has an
epilog, even if trivially short).

## Correctness bar (non-negotiable, per stage)

1. Every example must be a REAL, runnable invocation — verified against
   the actual `add_argument` calls for that subcommand (required
   positional/flag names must be spelled exactly right). Where practical,
   actually run the example against a scratch workspace to confirm it
   doesn't error.
2. `pytest -q` and `pyright` must both stay clean after each stage. If an
   existing test asserts exact `--help` text and breaks because of an
   epilog addition, fix the test's expectation — the epilog is the
   intended change, never revert it to make an old assertion pass.
3. Commit each stage as its own commit on the current branch
   (`chore/structure-audit-and-materializer-fix-2026-09-23`), with a
   commit message naming which stage it is. Leave it committed but do not
   push — the terminal (cc) reviews and pushes.

## Explicitly NOT in scope for this spec

- Restructuring `peerhub/cli/__init__.py`'s monolithic-module structure
  (a separate, larger, already-flagged structural-debt item — do not
  fold it into this task).
- Adding new CLI behavior/flags — this is help-text and README only.
