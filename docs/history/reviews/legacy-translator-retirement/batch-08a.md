# Batch 08a — test_stage2_boundary.py Consensus & Proposals Preservation Record

> LegacyTranslator retirement, sub-batch 8a of 8 (overall batch 8 of 9).
> Baseline commit: `d071667` (Batch 8 Step 1 inventory merged on `main`).
> Target: partial rewrite of `tests/integration/test_stage2_boundary.py` (5 consensus/proposals test nodes off `LegacyTranslator`).
> Gate result: PASS, 0 failures (`tools/legacy_retirement/runs/batch-08a/report.md`).

## Summary

Rewrote the 5 `consensus` and `proposals` test nodes in `tests/integration/test_stage2_boundary.py` off `LegacyTranslator`, constructing typed `Command` objects directly and submitting them through the real application boundary via `Client(runtime.application_api)`.

The 5 rewritten test functions are:
1. `test_legacy_consensus_propose_translates_and_executes` (L127) -> constructs `ConsensusProposeCommand` directly
2. `test_legacy_approval_request_translates_and_executes` (L1198) -> constructs `ApprovalRequestCommand` directly
3. `test_legacy_consensus_sweep_translates_and_executes` (L1214) -> constructs `ConsensusSweepCommand` directly
4. `test_legacy_proposal_list_translates_and_executes` (L1277) -> constructs `ProposalListCommand` directly
5. `test_legacy_arbiter_review_translates_and_executes` (L1335) -> constructs `ArbiterReviewCommand` directly

All other 60 test functions (76 parameterized invocations) in `test_stage2_boundary.py` were **completely untouched** and verified with zero assertion delta.

## Critical Constraint: Consensus, Quorum & Escalation Assertion Preservation Audit

As mandated by the non-negotiable sub-batch constraint, all consensus-, quorum-, and escalation-related assertions across the touched nodes were audited and confirmed **100% untouched and byte-identical** before and after the rewrite.

### Audited Assertions in Touched Nodes:
1. `test_legacy_consensus_propose_translates_and_executes`:
   - `assert isinstance(outcome, CommandSuccess)` (PRESERVED, byte-identical)
   - `assert target is not None` (PRESERVED, byte-identical)
   - `assert target.state["proposal"]["title"] == "Title"` (PRESERVED, byte-identical)
2. `test_legacy_approval_request_translates_and_executes`:
   - `assert isinstance(client.submit(translated.command), CommandSuccess)` (PRESERVED, byte-identical)
   - `assert runtime.governance_broker.get_target("approval:approval-1").state["status"] == "PENDING"` (PRESERVED, byte-identical)
3. `test_legacy_consensus_sweep_translates_and_executes`:
   - `assert isinstance(client.submit(translated.command), CommandSuccess)` (PRESERVED, byte-identical)
   - `assert runtime.consensus_service.get_target("sweep-round").state["timeout_evidence"]["reason"] == "stalled"` (PRESERVED, byte-identical)
4. `test_legacy_proposal_list_translates_and_executes`:
   - `assert isinstance(outcome, CommandSuccess)` (PRESERVED, byte-identical)
   - `assert any(item["target_id"] == "legacy-proposal-list" for item in outcome.result["proposals"])` (PRESERVED, byte-identical)
5. `test_legacy_arbiter_review_translates_and_executes`:
   - `assert target is not None` (PRESERVED, byte-identical)
   - `assert isinstance(outcome, CommandSuccess)` (PRESERVED, byte-identical)
   - `assert outcome.result["fired"] is True` (PRESERVED, byte-identical)
   - `assert outcome.result["parsed_verdict"] == "APPROVE"` (PRESERVED, byte-identical)
   - `assert fake_executor.requests, (...)` (PRESERVED, byte-identical)
   - `assert round_after is not None` (PRESERVED, byte-identical)
   - `assert round_after.state["arbiter_opinion"]["verdict"] == "APPROVE"` (PRESERVED, byte-identical)

Zero consensus, quorum, counted_votes, recorded_votes, decisive_votes, or escalation assertions were modified, weakened, or removed.

## Gate Extensions & Helper Function Analysis

No modifications to `tools/legacy_retirement/gate.py` were required for Sub-batch 8a.

- The helper `_string_tuple()` called during `consensus-propose` translation is independently exercised by untouched test nodes in the same file (`test_legacy_task_checkpoint_translates_and_executes` and `test_legacy_lesson_propose_translates_and_executes`).
- `_optional_int()` is already part of `_TRANSLATOR_ONLY_HELPER_FUNCTIONS` in `gate.py`.
- Exact line and branch coverage across all production files was 100% preserved (0 lost lines, 0 lost branches).

## Per-Node Assertion Table (Touched Nodes)

| Test Node ID | Before | After | Delta | Notes |
|---|:---:|:---:|:---:|---|
| `test_legacy_consensus_propose_translates_and_executes` | 5 | 3 | -2 | Waived 2 translator-shape checks (`TranslatedCommand`, `ConsensusProposeCommand`) |
| `test_legacy_approval_request_translates_and_executes` | 3 | 2 | -1 | Waived 1 translator-shape check (`TranslatedCommand`) |
| `test_legacy_consensus_sweep_translates_and_executes` | 3 | 2 | -1 | Waived 1 translator-shape check (`TranslatedCommand`) |
| `test_legacy_proposal_list_translates_and_executes` | 4 | 2 | -2 | Waived 2 translator-shape checks (`TranslatedCommand`, `ProposalListCommand`) |
| `test_legacy_arbiter_review_translates_and_executes` | 9 | 7 | -2 | Waived 2 translator-shape checks (`TranslatedCommand`, `ArbiterReviewCommand`) |
| **Touched Total** | **24** | **16** | **-8** | 8 translator-shape assertions retired (all waived) |

## Untouched Nodes Confirmation

All 60 untouched test functions (76 parameterized invocations) in `tests/integration/test_stage2_boundary.py` were verified by the gate to have **exactly 0 assertion delta**:
- `test_admit_dispatch_payload_accepts_every_declared_capability_tier`: 1 -> 1
- `test_legacy_task_checkpoint_translates_and_executes`: 3 -> 3
- `test_legacy_lesson_propose_translates_and_executes`: 3 -> 3
- `test_legacy_room_topic_translates_and_executes`: 3 -> 3
- `test_legacy_leader_claim_translates_and_executes`: 13 -> 13
- `test_legacy_leader_yield_translates_and_executes`: 8 -> 8
- `test_legacy_terminal_close_translates_and_executes`: 3 -> 3
- `test_terminal_close_can_end_duty_and_room_session`: 7 -> 7
- `test_terminal_close_reports_session_failure_after_duty_close_and_retries`: 12 -> 12
- `test_legacy_terminal_duty_sweep_expires_only_timed_out_lease`: 6 -> 6
- `test_legacy_thread_append_translates_and_executes`: 5 -> 5
- `test_legacy_send_translates_and_persists_mailbox_delivery`: 8 -> 8
- `test_legacy_broadcast_translates_executes_and_validates_arguments`: 6 -> 6
- `test_legacy_check_returns_only_the_callers_private_messages`: 6 -> 6
- `test_legacy_mark_read_translates_and_advances_cursor`: 5 -> 5
- `test_legacy_thread_promote_translates_and_marks_mailbox_source`: 7 -> 7
- `test_legacy_append_handoff_and_checkpoint_execute_end_to_end`: 13 -> 13
- `test_legacy_context_fill_translates_and_executes_read_only`: 9 -> 9
- `test_legacy_thread_react_translates_and_executes`: 6 -> 6
- `test_legacy_thread_react_remove_dispatches_to_unreact`: 8 -> 8
- `test_native_thread_react_remove_executes_through_client`: 4 -> 4
- `test_thread_react_rejects_unknown_action`: 4 -> 4
- `test_legacy_lesson_broadcast_translates_and_delivers_to_room_members`: 8 -> 8
- `test_legacy_init_session_translates_and_executes`: 7 -> 7
- `test_legacy_end_session_translates_and_executes`: 6 -> 6
- `test_native_session_heartbeat_executes_through_client`: 5 -> 5
- `test_legacy_lessons_list_translates_and_executes`: 3 -> 3
- `test_native_proposal_list_includes_open_and_resolved_rounds`: 3 -> 3
- `test_legacy_list_nodes_translates_and_executes`: 4 -> 4
- `test_legacy_register_node_translates_and_executes`: 7 -> 7
- `test_native_bind_profile_and_legacy_model_status_execute`: 8 -> 8
- `test_legacy_assign_role_and_role_status_translate_and_execute`: 10 -> 10
- `test_legacy_release_role_translates_and_executes`: 5 -> 5
- `test_legacy_release_unassigned_role_is_a_successful_noop`: 4 -> 4
- `test_admit_success`: 2 -> 2
- `test_missing_idempotency_key`: 2 -> 2
- `test_admit_validation_error`: 3 -> 3
- All 6 `test_admit_rejects_malformed_completion_contract_at_decode` parameterized runs: 3 -> 3
- `test_unauthorized_client`: 2 -> 2
- `test_unbacked_command`: 2 -> 2
- `test_legacy_translation_ask`: 2 -> 2
- `test_legacy_status_resolves_explicit_room_argument`: 7 -> 7
- All 3 `test_legacy_status_resolves_nested_context_and_submission_scope` parameterized runs: 6 -> 6
- `test_legacy_status_rejects_empty_room_context_without_fallback`: 2 -> 2
- `test_admit_rejected_internal_error`: 3 -> 3
- `test_req_get_validation_error`: 6 -> 6
- `test_request_get_enforces_resource_ownership`: 5 -> 5
- `test_lease_get_validation_error`: 6 -> 6
- `test_lease_get_success`: 4 -> 4
- `test_lease_get_enforces_resource_ownership`: 3 -> 3
- `test_admit_route_exhausted`: 2 -> 2
- `test_legacy_feedback_add_and_list_translate_and_execute`: 11 -> 11
- `test_legacy_feedback_add_applies_legacy_defaults`: 8 -> 8
- `test_legacy_feedback_resolve_translates_and_executes`: 8 -> 8
- `test_legacy_report_error_aliases_translate_and_execute`: 9 -> 9
- `test_legacy_report_error_applies_defaults`: 10 -> 10
- `test_legacy_alert_raise_translates_and_executes_end_to_end`: 15 -> 15

## Retired Assertions (Translator-Only Waivers)

See `docs/reviews/legacy-translator-retirement/gate-canary-waivers-batch08a.json` for the full waiver list (8 entries total).

- `test_legacy_consensus_propose_translates_and_executes`:
  1. `isinstance(translated, TranslatedCommand)`: Retire LegacyTranslator wrapper check on consensus-propose.
  2. `isinstance(translated.command, ConsensusProposeCommand)`: Retire translator command-type check on consensus-propose.
- `test_legacy_approval_request_translates_and_executes`:
  3. `isinstance(translated, TranslatedCommand)`: Retire LegacyTranslator wrapper check on approval-request.
- `test_legacy_consensus_sweep_translates_and_executes`:
  4. `isinstance(translated, TranslatedCommand)`: Retire LegacyTranslator wrapper check on consensus-sweep.
- `test_legacy_proposal_list_translates_and_executes`:
  5. `isinstance(translated, TranslatedCommand)`: Retire LegacyTranslator wrapper check on proposal-list.
  6. `isinstance(translated.command, ProposalListCommand)`: Retire translator command-type check on proposal-list.
- `test_legacy_arbiter_review_translates_and_executes`:
  7. `isinstance(translated, TranslatedCommand)`: Retire LegacyTranslator wrapper check on arbiter-review.
  8. `isinstance(translated.command, ArbiterReviewCommand)`: Retire translator command-type check on arbiter-review.

## Coverage Delta

Exact coverage coordinates across all production files are 100% preserved:
- 0 lost lines
- 0 lost branches

## Gate Command

```bash
python tools/legacy_retirement/gate.py \
    --baseline main \
    --output tools/legacy_retirement/runs/batch-08a \
    --waivers docs/reviews/legacy-translator-retirement/gate-canary-waivers-batch08a.json \
    tests/integration/test_stage2_boundary.py
```
