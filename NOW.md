# Now

Read this first for recent flow, current state and next steps. Give peers only the task delta and file pointers.
Update it in the same PR when state, flow or next steps change. Keep it within 60 lines. Older flow belongs in [CHANGELOG](CHANGELOG.md) or [docs/history](docs/history/).

Updated: 2026-11-27

## State

- Released: v0.27.1 (2026-11-27). Branches: `main` only. In flight: none.

## Recent flow (newest first, at most 5)

- 2026-11-27 v0.27.1: batch reports `denied_actions` (#493); a just-reset short window keeps a `reset · refresh pending` row (#495); CLI review fixes (observation refresh and diag health text, exit-code table, error hints); model defaults re-measured (agy 1.5.18, all cx/ag pings passed).
- 2026-11-27 v0.27.0: scope decision ([record](docs/history/scope-decision-2026-11-27.md)): one-shot diag, trimmed monitor and batch flags, lessons consolidated with aliases, RUNNING ask IDs with `monitor open`, NOW.md handoff, repeatable `--query-file`, `suggest` deprecated.
- 2026-11-27 #491: Release flow dispatches full-matrix CI when the main push leaves the version unchanged.
- 2026-11-27 v0.26.19 (#490): Fixed just-reset short windows, RUNNING totals past the detail cap, expanded QUOTA rank and a flaky cc adapter test (#488 follow-ups).
- 2026-11-26 v0.26.19 (#489): Added a requirements registry and alert-code lessons, set the release review bar to 80 and aligned monitor billing and plan gaps.

## Next (ordered, at most 8)

1. [#497](https://github.com/greatgc-flow/peerhub/issues/497) Triage the remaining CLI review findings (priority: monitor `suggest:` line, bare `diag` USED/PACE(pt), plan JSON size, compact/expand pool count, LOST sessions shown resumable).
2. Remove `peerhub suggest` together with the monitor `suggest:` line (deprecated in 0.27.0; `diag --plan` gives per-pool ACTION and profiles).
3. Engram and VerySimpleGuide adopt NOW.md once this file has stayed accurate for one release.
4. #488 item 4 (watch keyboard controls) is deferred: the interactive TUI stays in the STATUS backlog.

## Rules that bite (at most 8, one line each, link the doc)

- Give Git writes and shared release files to one lead and keep reviewers read-only. [AI peer workflow](CONTRIBUTING.md#working-with-ai-peers)
- Release only with no functional defects and a mean review score of at least 80. [Release lessons](CONTRIBUTING.md#release-lessons)
- Pin durable requirements to regression tests and add or name the test for every fix. [Requirements registry](docs/requirements.md)
- For an untouched test that fails on main, rerun the failed CI job once. Treat a repeat as a defect and record the log line and rerun result in an issue either way. [Release lessons](CONTRIBUTING.md#release-lessons)
- Keep monitor changes consistent in compact and expand. Check rich and plain at narrow and wide widths. [Release checks](CONTRIBUTING.md#releasing)
