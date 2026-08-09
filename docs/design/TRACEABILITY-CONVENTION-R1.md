# peerhub traceability convention (R1)

Status: RATIFIED 2026-08-09. cx proposed, ag independently verified the
load-bearing claims and required 3 corrections (incorporated below), cc
synthesized. Scope: peerhub only — this convention says nothing about
P:'s hub.py, protocol.json, or _sys/docs-v2.

## Why

Tonight's session informally reused labels like "Step D2" and "Phase 3
increment 1" across design docs, commits, and memory files. It worked
well enough to be worth keeping, but was inconsistent: `rg` across the
repo tonight found "Phase 3"/"Step D2"/"increment" in exactly one place
in actual code (a TODO comment in `peerhub/cli.py`) and nowhere in test
names — commit messages and project-memory files carried nearly all the
real traceability. Formalizing this closes that gap rather than
pretending it doesn't exist.

## ID format

```
PH-<WORKSTREAM>-<UNIT>
```

Regex: `^PH-[A-Z0-9]{2,12}-[A-Z0-9]{1,12}$` — uppercase ASCII only.

`WORKSTREAM` is required, not optional. Tonight's own commits reused
"Phase 3 increment 1" for two unrelated things — the supervised-adapter
proof (`eee8d39`) and part of the direct-ask bootstrap (`4e3e1a3`) —
independently confirmed by both cx and ag reading the real commits. A
generic `PH3-INC1`-style scheme would already be ambiguous on day one.

Examples:

```
PH-GOV-D2       GovernanceBroker Step D2 (claim_effect cutover)
PH-ASK-INC4     direct-ask CLI Increment 4
PH-CLI-BUG2     an independently traceable CLI bug
PH-ROADMAP-R1   a roadmap design revision
```

Local labels already established this session (`A`, `D2`, `E1`, `INC4`)
are preserved as the `<UNIT>` suffix rather than renumbered. Unplanned
bugs use `BUG<n>`; design revisions use `R<n>`. Human-readable wording
follows the ID but is never part of the ID itself:
`PH-ASK-INC4 — ask CLI and exit contract`.

## What deserves an ID

Assign one when at least one applies:

- Changes an externally observable contract or a durable schema.
- Switches a source of truth, a safety boundary, CAS/fencing behavior,
  or a write path.
- Implements a separately-ratified increment with its own acceptance
  criterion.
- Fixes a bug that could have produced incorrect state, unsafe
  execution, disclosure, or complete feature failure.
- Establishes a previously-unproven real integration property.

Tonight's traceable examples: `002fa48` (exclusive-claim source switch)
→ `PH-GOV-D2`; `8b222e1` (receipt FK rebuild) → `PH-GOV-E1`; `c96a978`
(the composed admit/prepare/dispatch/decode path) → `PH-ASK-INC3`; the
executable-resolution defect → `PH-CLI-BUG1`; the UUIDv4 defect that
made every real `ask` fail before dispatch → `PH-CLI-BUG2`.

**Not** tagged: replacing `__import__()` hackery with normal imports,
correcting a test's wrong profile-fixture value (that repaired the test,
not production behavior), formatting/import-ordering/renames inside an
already-tagged unit, a mechanical prefix normalization by itself (though
the cross-module admission bug it *exposed* is separately traceable).
Do not manufacture a tag for every diff — that defeats the purpose.

## Required artifacts

| Artifact | Requirement |
|---|---|
| Design document | One canonical heading, e.g. `### [PH-GOV-D2] Step D2 — claim cutover`. |
| Primary implementation | One comment at the primary site: `# Trace-Unit: PH-GOV-D2 — <one-line why>`. |
| Acceptance test | `Trace-Unit: PH-GOV-D2` as the first line of one sentinel test's docstring. Keep behavioral function names — hyphens aren't valid in Python identifiers, and names like `test_claim_effect_concurrent_contenders_have_one_winner` are already more readable than an ID-encoded name would be. |
| Commit | Required trailer line: `Trace-Unit: PH-GOV-D2`. |
| Project memory | `metadata.trace_units: [PH-GOV-D2]` added to the corresponding memory file's frontmatter. |

**Multiple trace units in one commit** (ag's correction): use one
trailer line per unit, never comma-separated — this is what native
tooling expects:
```
Trace-Unit: PH-ASK-INC3
Trace-Unit: PH-CLI-BUG1
```
`git log --format="%(trailers:key=Trace-Unit)"` parses repeated keys
correctly; a comma-joined single line does not.

For a ratified-but-unimplemented decision: design anchor, commit
trailer, and memory are required immediately; code and test are marked
`PENDING`. For a permanently documentation-only decision, code/test are
marked `N/A`. Never write dead code just to carry a tag.

## Retrieval

No index or database file. Retrieval is just search:

```
rg -n -F "PH-GOV-D2" docs peerhub tests tools
git log --all --grep="Trace-Unit: PH-GOV-D2"
rg -n -F "PH-GOV-D2" <project-memory-root>
```

An index would be a sixth artifact requiring its own synchronization —
not worth the maintenance cost at this project's size. Revisit only if
grep/git-log retrieval genuinely stops scaling.

## Retrofit of tonight's 12 commits

Grandfather these exactly, in one bounded, non-history-rewriting commit
that adds the convention doc + appendix + tags current primary code/test
sites + adds `trace_units` metadata to the 2 existing memory files:

| Commit | Trace unit(s) |
|---|---|
| `fe9b8ae` | `PH-GOV-A` |
| `97ef5a9` | `PH-GOV-B` |
| `9ce4321` | `PH-GOV-C` |
| `c09d46b` | `PH-GOV-D1` |
| `002fa48` | `PH-GOV-D2` |
| `aeeec10` | `PH-GOV-D3` |
| `c88c6d5` | `PH-ROADMAP-R1` |
| `8b222e1` | `PH-GOV-E1` |
| `eee8d39` | `PH-EXEC-INC1` |
| `4e3e1a3` | `PH-ASK-INC1`, `PH-ASK-INC2` |
| `c96a978` | `PH-ASK-INC3`, `PH-CLI-BUG1` |
| `c77ebbb` | `PH-ASK-INC4`, `PH-CLI-BUG2` |

The appendix is a one-time grandfathering receipt, not an ongoing index.
Commit trailers become mandatory only for work done *after* this
convention's ratification — do not rewrite shared git history to add
trailers retroactively.
