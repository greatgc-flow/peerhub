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
- cx.pro consolidated final review is pending (cx circuit was open on 2026-10-04). Prompt and checklist: `docs/m1_impl/CX_FINAL_CHECKLIST.md`.
- Spec-owner decisions: `docs/m1_impl/OWNER_DECISIONS_NEEDED.md` (errata PROP-002, transition-contract gaps, importer scope, real-adapter resume/interrupt/steer).
- Not in M1: real session resume / interrupt / steer for real providers; Diag views of bridge tables; full-scale soak; CI-only matrix cells.
- T0 done: `peerhub ask` admission failures now carry a reason (peerhub/application/admission_reason.py).
