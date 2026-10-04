# PeerHub M1 Very Simple Renewal — Progress Tracker

Branch: `feat/m1-very-simple-renewal` (worktree `D:\PkgDev\workspace\peerhub-m1-renewal`), local only, no PR.
Spec: `docs/m1_spec/` (R2 FINAL, 88 requirements / 210 tests). Plan and rules: `docs/m1_impl/PLAN.md`.
Core = Peer / Stream / Record / Offset; first-party extensions = Session Bridge, Observation, Readonly Diag.

## Status (2026-10-04): waves 0-9 implemented, 210/210 catalog ids present

| Wave | Scope | State | Report |
|---|---|---|---|
| 0 | Meta / architecture / schemas | done, reviewed | docs/m1_impl/wave0_report.md |
| 1 | Core domain | done, reviewed (cx.pro, ag.pro) | wave1_report.md |
| 2 | SQLite / multi-process / migration | done, reviewed (cx.pro, ag.pro) | wave2_report.md |
| 3 | Claim / session / execution certainty | done, reviewed (cx.pro, ag.pro) | wave3_report.md |
| 4 | Control / context continuity | done, reviewed (cx.pro x4, ag.pro) | wave4_report.md |
| 5 | Observation / Diag | done, reviewed (ag.pro) | wave5_report.md |
| 6 | Fake-runtime E2E / fault injection | done, reviewed (ag.pro) | wave6_report.md |
| 7 | Package / compatibility / cutover | done, reviewed (ag.pro) | wave7_report.md |
| 8 | Real adapters + live canary (cc/cx/ag, run once, minimal quota) | done | wave8_report.md, live_evidence/ |
| 9 | Scheduled soak (evidence only) | done (moderate scale run; full scale opt-in) | wave9_report.md, soak_evidence/ |
| final | pragma-independent no-replace guards (core migration v2 + extension triggers) | done | final_hardening_report.md |

Tools: `python tools/m1_traceability.py [--upto N] [-v]`, `python docs/m1_spec/tools/validate_package.py`.
Run tests by directory under `tests/m1/<dir>` (+ `tests/unit/m1`); live/soak/CI-only parts are opt-in or skipped with machine-readable reasons.

## Open items
- Owner decisions: all resolved or accepted by delegation, nothing blocking (`docs/m1_impl/OWNER_DECISIONS_NEEDED.md`). A2/A4 implemented (separate invocation marker, no certainty downgrade; `final_ownerdecisions_report.md`).
- Consolidated final review (cx.effort, 3 scoped parts; cx.pro was unavailable while pacing was critical) found 13 defects across parts 1-3; all fixed and re-verified by ag.pro (HEAD 6ecd4d6). Live canary re-run with strict adapter completion checks: 6/6 passed (live_evidence/2026-10-04.json).
- New docs: `SPEC_FEEDBACK_RECORD.md`, `LEGACY_IMPORT_SCOPE.md`, `ADAPTER_CAPABILITIES.md`.
- Not in M1: real session resume / interrupt / steer for real providers; Diag views of bridge tables; full-scale soak; matrix cells not executed by CI are UNVERIFIED (TD-18) except windows py3.11-3.14 (VERIFIED-LOCAL, evidence docs/m1_impl/matrix_evidence/windows-py3.11-3.13.json); ubuntu cells remain UNVERIFIED.
- T0 done: `peerhub ask` admission failures now carry a reason (peerhub/application/admission_reason.py).
