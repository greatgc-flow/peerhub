# Wave 4 report (11 ids: CTL-001..005, CTX-001..003, BRG-001, BRG-008, BRG-009)

Production: `peerhub/extensions/bridge.py` (control intent, gating, cancel scope, catch-up wiring, context.boundary), `catchup.py` (TD-12 projection), `direction.py` (redirect vs new Stream). Tests: `tests/m1/control/{test_ctl_control,test_ctx_catchup,test_w4_control_hardening}.py`, helpers `tests/m1/control_helpers.py`, fake runtime extended (interrupt/terminate/steer capabilities, context loss), harness `handle_control` port, child-process worker `control_crash_worker`. 87 new tests.

## RED
Stubs (NotImplementedError) first: 83 failed / 2 passed. Reasons: `NotImplementedError` (handle_control, projection, direction), `no such table: bridge_controls`, `unexpected keyword catch_up_budget`, delivery returned `delivered` where `paused` was required, `DID NOT RAISE`. The 2 passes: BRG-001 plain case (Wave-3 behaviour already correct; its control-record variant failed) and a type-relation check.

## Decisions (spec)
TD-05 (intent committed before effect; asserted by raw-SQL snapshot taken inside the effect call), TD-12/TD-22 (injected budget, ordered suffix, truncation metadata, paged reads), TD-19 (control lease), TD-11/TD-26 untouched, LONG_HORIZON (pause != rollback, redirect same Stream, new goal new Stream, loss -> fresh session + bounded catch-up). Replaced the Wave-3 `pending_control` interim: controls no longer block (see Q-W4-2). Open questions Q-W4-1..9 in OPEN_QUESTIONS.md.

## Bug found by tests
Crash between fresh-generation creation and delivery lost the catch-up on retry; rule is now "no delivery reached this generation".

## Mutation probes (production broken, test fails, restored): 28 killed
no lookahead of controls; lease ignored; outcome write not fenced by lease (first SURVIVED, new test added, then killed); pause gate before marker off; paused by handling order; cancel fence inverted; write-once/delete/identity triggers removed (3); catch-up always / only-on-fresh-event; byte and record budget off by one; order check removed; truncation flag lost; forged kind / targets / self-authored control accepted; unsupported capability treated as supported; failed reported as done; cancel-skip evidence not deduplicated; boundary Record not written; redirect always same stream; prior stream not closed; intent row not inserted before effect; pause ignores resume; steer uses interrupt; unseen redirects not handed. No surviving non-equivalent mutants.

## Gate
Full `pytest tests/m1 tests/unit/m1` green; control+concurrency 3x green; traceability --upto 4 ok (wave 4: 11/11); validate_package PASS.
Not done: real adapters (T1), fault matrix over controls beyond the listed crash points (Wave 6), Diag views of control state (Wave 5).

## Gate fixes (cx.pro BLOCK on d1945ca, D-W4-1..10)
RED first: 22 new tests in tests/m1/control/test_w4_gate_fixes.py against d1945ca: 12 failed for the intended reasons (stale token interrupted, expired lease committed done, superseded handler executed, retarget, gate at invoke hook, creation retry after pause, stranded control, redirect scope/budget, boundary squat, direction partial success); later added: expired-token-without-takeover, crashed-retry vs newer cancel, cancel-consumption crash replay (total 25 in that file).
Fixed: fencing (target persisted once, lease+token+claim generation, `stale_target`), gating through marker and creation retries, stranded recovery, redirect scope/budget/boundary metadata, boundary validation, direction atomicity, position-based RETRY/cancel precedence, cancel-skip reverted (D-W4-7), Offset window (D-W4-8) with pinned unseen redirects. Wave-3 tests BRG-004/BRG-016 oracles updated for the Offset window.
Mutation probes (round 2, 20 probes): 19 killed. Two first SURVIVED and were killed by new tests: pre-effect token fence (new expired-token test) and crashed-retry cancel precedence (new test). One equivalent/unreachable via normal flow: dropping the RETRY-position threshold in `_cancel_applies` (a cancel accepted after an attempt cannot be positioned before that attempt's RETRY Record because the RETRY exists before the attempt); kept as defence in depth. Note: one probe run was interrupted by a tool timeout and left a mutation in bridge.py; it was detected and restored by hand (git diff checked).
Gate: tests/m1/control 109 passed (3x), concurrency 17 passed (3x), bridge/arch/meta/schema/property/migration 128 passed, core/integration/unit 67 passed; traceability --upto 4 ok; validate_package PASS.

## Re-gate fixes (cx.pro BLOCK on 257a758; D-W4-8b/2b/5b/9b)
RED first (8 new tests, 7 failed: bootstrap budget 3 and ample, pause during failed resume, 3 forged boundary payloads, RETRY1->cancel->RETRY2; 1 control passed). Fixed: fresh-generation bootstrap = bounded ordered Stream history suffix (budget) + pinned redirects (BRG-004/BRG-016/CTX oracles updated accordingly); gate before the first create_session; full boundary payload validation (5 forged variants incl. everything-valid-but-reason); position-based reconcile authorization replacement with a trigger-guarded move; extra trigger test.
Mutation probes (foreground): 9/9 killed (no first-creation gate, reason/shape/flag/count validation x4, no RETRY replacement, replacement of consumed, older replaces newer, Offset window again). The first survivor (reason check) was killed by an everything-valid-but-reason payload.
Gate: control 119 (3x), concurrency 17 (3x), bridge 78, bridge/arch/meta/schema/property/migration 128, core/integration/unit 67, traceability --upto 4 ok, validate_package PASS.
