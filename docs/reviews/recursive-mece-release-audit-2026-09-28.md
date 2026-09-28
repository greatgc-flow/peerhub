# Recursive MECE release audit — 2026-09-28

## Decision

PeerHub 0.9.0 was released after the active product surface, tests,
documentation, configuration, release artifacts, and live vendor integrations
were cross-checked. A post-release audit restored the intended mandatory live
provider gate after the initial 0.9.0 publication workflow had temporarily made
it informational. Future publication blocks until that gate succeeds.

## Scope and evidence

| Area | Authoritative implementation/config | Tests and empirical evidence | User/maintainer documentation |
|---|---|---|---|
| CLI and recursive help | `peerhub/cli/`, `peerhub/_version.py` | `tests/unit/test_cli_help.py`, CLI integration tests | `README.md` scenario cookbook and `peerhub /?` tree |
| Adapter discovery and dispatch | `peerhub/adapters/`, packaged model defaults | adapter unit/integration tests; twelve live profile PONGs; slow/e2e tiers | `docs/adapters/`, `docs/compatibility/` |
| Diagnostics, quota, and failover | diagnostics, health, routing, telemetry services | full suite plus interactive `diag --live` and fresh JSON probe | README diagnostics guidance and 2026-09-27 profile report |
| Governance and feedback | task, room, lesson, feedback, error-review, artifact, lock, duty, consensus services | command wire contracts and service/integration suites | README feedback-loop scenarios; production call map |
| Portability and migration | workspace/config resolvers and explicit migration arguments | temporary-root migration fixture; path/context tests | config hierarchy and migration docs |
| Packaging | `_version.py`, `pyproject.toml`, publish workflow | wheel/sdist build, strict Twine check, isolated wheel import | README exact-release install instructions |

The generated CLI inventory contains 30 top-level command groups, 107 leaf
commands, and 134 parser nodes including groups. Every leaf has a description,
argument help, and an example. `/?` is normalized at every nesting level and is
covered by a recursive test, so the help contract cannot silently regress when a
command is added.

## History and loss audit

- All remotes, tags, reachable commits, legacy evidence branches, archive bundle
  receipts, and repository object integrity were checked before the legacy
  drive was removed. No unpushed or unreachable product content was found.
- Historical Phase-0 fixtures remain under `docs/history/` as provenance, not
  current runtime guidance. Active adapter documentation was rewritten from
  current installed CLI behavior instead of mutating those historical records.
- The former `D:\Engram&Peerhub` staging tree and `P:\` are absent after the
  verified migration. Ambiguous personal/backlog material was isolated outside
  the product repositories rather than silently discarded.

## Correctness and test gates

- Tagged-release deterministic suite: **2,404 passed, 4 skipped, 16 deselected, 13 subtests**.
  The four skips are host limitations: two Windows symlink-privilege cases and
  two POSIX-only permission/symlink semantics.
- Post-release real model/vendor validation returned PONG evidence from all
  twelve profiles, passed all **6 e2e tests**, and passed **9 of 10 slow tests**;
  the remaining Claude probe received an empirical 429 weekly-quota rejection.
  This is an external-capacity failure and intentionally blocks a new release
  rather than being skipped.
- Static typing: Pyright is required locally and in CI; source-tree analysis has
  zero errors. Build smoke copies are deleted after inspection so they cannot be
  misidentified as a second package.
- Package: `peerhub-0.9.0` wheel and sdist build successfully, pass strict Twine
  validation, and the wheel imports as version 0.9.0 from an isolated target.
- Live diagnostics: all three vendor CLIs are discoverable. `diag --live` reports
  CLI reachability separately from quota telemetry; missing AG telemetry is not
  presented as a failure or invented capacity. At verification time CC and CX
  had real quota pressure, so automatic failover correctly remained unavailable.

## Feedback closure

The shortest repeatable loop is: reproduce with `diag --fresh --json` or the
relevant command; record the runtime problem with `error record`; inspect and
resolve it through `error review`; capture reusable knowledge with `lesson`; and
promote only reviewed lessons. README examples cover this path. Confirmed defects
must end in a regression test, declarative policy/config change where possible,
and a documentation warning only when the limitation belongs to an OS or vendor.

## Structural and no-code findings

- Runtime behavior is repository-root independent. Workspace and global config
  locations come from arguments/environment/resolvers rather than repository
  names. The migration tool now requires an explicit workspace instead of a
  historical absolute path.
- Vendor model bindings and compatibility contracts remain declarative. The
  adapters contain protocol mechanics, not account-specific model selection.
- The production call map now matches the registered command surface. Duplicate
  help prose is generated from parser metadata, and no parallel hand-maintained
  command catalog is authoritative.
- Vendor authentication, quota reporting, model availability, sandbox rules,
  and special-character/aliased-path behavior remain external constraints.
  PeerHub diagnoses or warns; it does not weaken third-party security controls.

## Five-whys review and decisions reserved for maintainers

1. **Why is quota sometimes unknown?** Vendors do not expose uniform telemetry;
   guessing would make routing unsafe. Current choice: honest `CLI_OK`/unknown
   separation. Optional future choice: build an always-running quota daemon.
2. **Why retain the large Phase-0 oracle?** It is the only loss-audit provenance
   for the legacy cutover. Current recommendation: keep it archived until an
   explicit retention policy approves deletion.
3. **Why are live tests opt-in?** They spend quota, depend on credentials, and can
   vary with providers. Current recommendation: keep deterministic CI mandatory
   and run slow/e2e before releases or protocol/model changes.
4. **Why not support every unusual Windows alias/path?** Vendor sandboxes own part
   of path validation. Current recommendation: continue warning with the exact
   rejected path instead of bypassing sandbox enforcement.

