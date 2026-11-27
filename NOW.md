# Now

Read this first: current state and next steps. Recent flow is the top of CHANGELOG.md and `git log --oneline -10`.
Update it in the same PR when the state or next steps change. Keep it under 30 lines.

Updated: 2026-11-27

## State

- Released: v0.28.2 (2026-11-27): #503, #502, #498. An activity export candidate is in review. See the [implementation record](docs/implementation/activity/WORK.md).

## Next (ordered)

1. [#504](https://github.com/greatgc-flow/peerhub/issues/504) Low-impact 0.28.2 review leftovers.
2. cc telemetry: 90 of 132 recent cc rows are UNKNOWN (`output_incomplete`); look at it only if cc is dispatched again.
3. #497 item 5 (monitor `--json` size and PARTIAL exit) is deferred until a concrete consumer appears.
4. Engram and VerySimpleGuide adopt NOW.md and AGENTS.md lane rules once this file has stayed accurate for one release.
5. #488 item 4 (watch keyboard controls) is deferred: the interactive TUI stays in the STATUS backlog.
