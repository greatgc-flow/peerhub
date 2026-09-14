# peerhub / P: isolation decision

Status: ratified 2026-08-09. Read this before assuming peerhub shares any
runtime state with P:'s hub.py-based coordination system.

## Finding

`P:\_sys\core\hub.py`'s `find_ai_root()` resolves its own coordination-state
root (`ask_guards/`, `ask_history.jsonl`, `leases.json`, `nodes.json`,
`routing_metrics.jsonl`, session-room state) by walking up from the
invoking process's CWD to the nearest ancestor containing a `.ai/` or
`.git/` marker. This is documented, intentional behavior — it lets hub.py
operate against an external project's own `.ai/` for portability.

peerhub is its own git repository (`P:\peerhub\.git`). Every `hub.py ask`
invocation made with CWD inside `P:\peerhub` — which has been effectively
all of them, since peerhub is the active project — therefore resolves to
`P:\peerhub\.ai\`, not `P:\.ai\`. This has been happening since at least
2026-07-29 (earliest file timestamps found in `P:\peerhub\.ai\`), not just
during this session.

Practical consequence observed directly: an `AskGuardRecord` collision
(`ask-5f26`) during this session's dispatching was a symptom of this
split — the collision-guard state peerhub-context dispatches check against
is not the same state a P:-rooted session would see.

**Not affected**: `P:\_sys\cli\diag.py` resolves its own root via a fixed
`PORTABLE_ROOT / ".ai"` derived from its own file location, independent of
CWD — it always reads the canonical `P:\.ai\`. EXH/quota/context numbers
shown by `diag.py` throughout this session were NOT affected by the split;
token/API quota is a genuinely global, account-level resource and it is
correct for it to stay unified regardless of which project is active.

**Not affected**: peerhub's own git history and working tree. `P:\peerhub\.ai\`
is covered by `.gitignore` and has never been tracked.

## Decision

Do not merge `P:\peerhub\.ai\` back into `P:\.ai\`, and do not force
unification via `HUB_AI_ROOT`. Treat the split as the intended outcome
going forward: **peerhub-context hub.py dispatch coordination (ask_guards,
leases, room-session state, ask_history for peerhub-related work) is
deliberately isolated in `P:\peerhub\.ai\`, separate from P:'s own
coordination state.** This is consistent with peerhub's overall goal —
peerhub is meant to become a standalone package, eventually independent of
hub.py entirely; a peerhub-scoped coordination silo while hub.py is still
the dispatch mechanism for peerhub's *own development* is the correct
direction, not an accident to paper over.

Quota/EXH tracking remains intentionally global (`P:\.ai\` via `diag.py`)
— this is a different kind of resource (account-level, not project-scoped)
and should not be split.

No action was taken on `P:\peerhub\.ai\` or `P:\.ai\` themselves — both are
hub.py-owned runtime state outside peerhub's own code, and this decision
does not require touching either.

## A related bug this surfaced, and its fix

While verifying nothing in peerhub's own code accidentally depended on or
collided with this boundary, found: `execute_direct_ask()`
(`peerhub/application/direct_ask.py`) was NOT using the codebase's own
standard `PathLayout.for_workspace()` convention (`workspace_root / ".peerhub"`,
already the default used by the `status` CLI command and already reflected
in `.gitignore`'s `.peerhub/` entry). It had instead hand-rolled a
`workspace_root / ".ai" / "peerhub"` path.

This was a real collision risk: `peerhub ask`'s `--workspace` flag defaults
to `"."`. Running `peerhub ask ...` from inside `P:\peerhub` itself (the
most natural place to test it during development) with the default
`--workspace .` would have created `P:\peerhub\.ai\peerhub\peerhub.db` —
peerhub's own real database sitting inside the exact directory tree hub.py
uses for unrelated coordination bookkeeping. Fixed by using
`PathLayout.for_workspace()` directly, matching the rest of the codebase;
peerhub's database now always lands under `.peerhub/`, which shares no
name or directory with anything hub.py touches.

Verified after the fix: 0 pyright errors, 528/528 tests, and a real
`peerhub ask` invocation confirmed the database now lands at
`<workspace>/.peerhub/peerhub.sqlite3`.

## Summary for future readers

- `P:\peerhub\.ai\` = hub.py's own peerhub-scoped coordination bookkeeping.
  Not peerhub code. Not git-tracked. Not merged with P:'s copy, by design.
- `P:\peerhub\.peerhub\` (or `<any-workspace>/.peerhub/`) = peerhub's own
  real runtime state (its SQLite database), created via
  `PathLayout.for_workspace()`. This is peerhub's actual deliverable data.
- These two directories are unrelated despite both being dotfolders one
  level under a peerhub-rooted path. Do not confuse them; do not let future
  code default either one into the other's namespace.
