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
