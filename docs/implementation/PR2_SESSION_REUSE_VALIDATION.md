# Session reuse PR2 validation

Reviewed 2026-10-14 against the canonical
[PR2 scope](../design/analysis-session-reuse-as-is-2026-10-13.md#pr2--retain-sessions-and-switch-all-writersreaders-atomically).
Validation covers the uncommitted PR2 tree; no commit was made.

| Required behavior | Deterministic evidence | Documentation |
| --- | --- | --- |
| Extension handles, history, monotonic generations, cap/expiry, eligible pointer repair | `test_retained_sessions.py`: binding A/B/A, retention cap, exact age boundary, expiry pointer repair, migration high-water mark | [Registry contract](RETAINED_SESSIONS.md), README, usage guide, Unreleased Added |
| Atomic old-slot migration and all mapping consumers | `test_retained_sessions.py`: migration/reopen/rollback, delivery-bound taint, recovery; `test_native_resume.py`: cancellation, loss, legacy custom resume, boundary validation; existing Bridge/control/fault/diag suites | Registry contract, native resume design, adapter capabilities, Unreleased Changed |
| Latest compatible history, consumed-prompt watermark and complete delta; safe overflow | `test_retained_sessions.py`: native/custom A/B/A with intervening response; `test_native_resume.py`: concurrent record, all eligible records, overflow, uncertain execution | Registry contract, context efficiency, README |
| Normalized latest intent, complete identity before replay, original immutable snapshot, legacy rejection | `test_retained_sessions.py`: original replay after tombstoning/pointer deletion, changed prompt conflict, selector-less historical terminal rejection, legacy queue progress; existing ask identity tests | Registry contract, README, usage guide, Unreleased Changed |
| Exactly-once call/telemetry receipts and atomic projections; unknown legacy counts and coverage; quota index | `test_retained_sessions.py`: repeated STARTED, duplicate terminal/recovery, stale fence, call/counter rollback, terminal/telemetry rollback, unknown totals after new calls, indexed quota query | Registry contract, Unreleased Added |
| Permanent JSON/session trailer, nullable failure session, unknown occupancy, packaged modules | `test_session_envelopes.py`: text/replay, resumed/fallback bytes, JSON nulls, pre/post-allocation and storage errors; installed-artifact session contract in `test_rel_build_install.py`; both extension/module gates | Registry contract, README, adapter guides/reference, usage guide, Unreleased Changed |

LOST transitions repair or delete the affected MRU pointer in the same fenced
transaction. Registry regressions assert persisted history and pointer state
immediately after loss and cancellation, before another resolution.

Recorded deterministic baseline: **2260 tests passed**. Critic follow-up
regressions: **346 passed**. Pyright: **0 errors, 0 warnings**.
The spec seal is regenerated with `python docs/m1_spec/tools/seal_package.py`.
Sandbox configuration and run-log details are kept under `.peerhub/work/`.

Fresh/exact addressing belongs to PR3; measured occupancy and bounded inspection
to PR4; shared monitor views to PR5. Real-provider validation requires explicit
opt-in and remains separate release work.
