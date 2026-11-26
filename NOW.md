# Now

Read this first for recent flow, current state and next steps. Give peers only the task delta and file pointers.
Update it in the same PR when state, flow or next steps change. Keep it within 60 lines. Older flow belongs in [CHANGELOG](CHANGELOG.md) or [docs/history](docs/history/).

Updated: 2026-11-27

## State

- Released: v0.26.19 (2026-11-27). Integration branch: `main`. The 0.27.0 monitor lane implements the approved scope decision.

## Recent flow (newest first, at most 5)

- 2026-11-27 Scope decision ([record](docs/history/scope-decision-2026-11-27.md)): one-shot diag, trimmed monitor flags, consolidated lessons with aliases.
- 2026-11-27 #491: Release flow dispatches full-matrix CI when the main push leaves the version unchanged.
- 2026-11-27 v0.26.19 (#490): Fixed just-reset short windows, RUNNING totals past the detail cap, expanded QUOTA rank and a flaky cc adapter test (#488 follow-ups).
- 2026-11-26 v0.26.19 (#489): Added a requirements registry and alert-code lessons, set the release review bar to 80 and aligned monitor billing and plan gaps.
- 2026-11-26 #487: Release flow polls for a new draft before verifying it to tolerate a lagging release list (#486).

## Next (ordered, at most 8)

1. [#488](https://github.com/greatgc-flow/peerhub/issues/488) Integrate and verify RUNNING inspection and the monitor CLI removals.
2. [#492](https://github.com/greatgc-flow/peerhub/issues/492) Continue the tracked follow-ups. Item 1 now uses pytest-managed scratch space for release-flow tests.

## Rules that bite (at most 8, one line each, link the doc)

- Give Git writes and shared release files to one lead and keep reviewers read-only. [AI peer workflow](CONTRIBUTING.md#working-with-ai-peers)
- Release only with no functional defects and a mean review score of at least 80. [Release lessons](CONTRIBUTING.md#release-lessons)
- Pin durable requirements to regression tests and add or name the test for every fix. [Requirements registry](docs/requirements.md)
- For an untouched test that fails on main, rerun the failed CI job once. Treat a repeat as a defect and record the log line and rerun result in an issue either way. [Release lessons](CONTRIBUTING.md#release-lessons)
- Keep monitor changes consistent in compact and expand. Check rich and plain at narrow and wide widths. [Release checks](CONTRIBUTING.md#releasing)
