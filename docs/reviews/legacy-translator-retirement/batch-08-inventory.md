# Batch 08 — Subsystem Inventory & Rewrite Plan for test_stage2_boundary.py

> LegacyTranslator retirement, Batch 8, Step 1 (Planning & Categorization).
> Baseline commit: `82fed87` (Batch 7 merged).
> Target file: `tests/integration/test_stage2_boundary.py` (2,504 lines, 65 test functions, 355 assertions).

## Executive Summary

A complete AST and execution inventory of `tests/integration/test_stage2_boundary.py` was conducted to categorize all 65 test functions across logical subsystems and assess their usage of `LegacyTranslator`.

- **Total test function nodes**: 65
- **Nodes requiring rewrite** (`Uses LegacyTranslator? = yes`): **45**
- **Already fully native nodes** (`Uses LegacyTranslator? = no`): **20**
- **Subsystem groups identified**: 16 logical groups across 10 major domain areas

> [!NOTE]
> **Node ID and Parameterization Note**: `python -m pytest tests/integration/test_stage2_boundary.py --collect-only -q` reports 81 test invocations at execution time because 3 test functions are parameterized (`test_admit_dispatch_payload_rejects_non_enum_capability_tiers` with 10 variants, `test_admit_rejects_malformed_completion_contract_at_decode` with 6 variants, and `test_legacy_status_resolves_nested_context_and_submission_scope` with 3 variants: 81 - 19 + 3 = 65). Every collected execution variant maps unambiguously to its defining test function node. All 65 distinct test function nodes appear exactly once in the table below (65 rows in, 65 rows out).

## Subsystem Inventory Table

| # | Test Node ID | Subsystem Group | Uses LegacyTranslator? | Rewrite Needed? |
|:---:|---|---|:---:|:---:|
| 1 | `tests/integration/test_stage2_boundary.py::test_legacy_consensus_propose_translates_and_executes` | `consensus` | yes | yes |
| 2 | `tests/integration/test_stage2_boundary.py::test_legacy_approval_request_translates_and_executes` | `consensus` | yes | yes |
| 3 | `tests/integration/test_stage2_boundary.py::test_legacy_consensus_sweep_translates_and_executes` | `consensus` | yes | yes |
| 4 | `tests/integration/test_stage2_boundary.py::test_legacy_arbiter_review_translates_and_executes` | `consensus` | yes | yes |
| 5 | `tests/integration/test_stage2_boundary.py::test_native_proposal_list_includes_open_and_resolved_rounds` | `proposals` | no | no |
| 6 | `tests/integration/test_stage2_boundary.py::test_legacy_proposal_list_translates_and_executes` | `proposals` | yes | yes |
| 7 | `tests/integration/test_stage2_boundary.py::test_legacy_leader_claim_translates_and_executes` | `leadership` | yes | yes |
| 8 | `tests/integration/test_stage2_boundary.py::test_legacy_leader_yield_translates_and_executes` | `leadership` | yes | yes |
| 9 | `tests/integration/test_stage2_boundary.py::test_legacy_terminal_close_translates_and_executes` | `duty_and_terminal` | yes | yes |
| 10 | `tests/integration/test_stage2_boundary.py::test_terminal_close_can_end_duty_and_room_session` | `duty_and_terminal` | yes | yes |
| 11 | `tests/integration/test_stage2_boundary.py::test_terminal_close_reports_session_failure_after_duty_close_and_retries` | `duty_and_terminal` | yes | yes |
| 12 | `tests/integration/test_stage2_boundary.py::test_legacy_terminal_duty_sweep_expires_only_timed_out_lease` | `duty_and_terminal` | yes | yes |
| 13 | `tests/integration/test_stage2_boundary.py::test_legacy_room_topic_translates_and_executes` | `room_lifecycle` | yes | yes |
| 14 | `tests/integration/test_stage2_boundary.py::test_legacy_status_resolves_explicit_room_argument` | `room_lifecycle` | yes | yes |
| 15 | `tests/integration/test_stage2_boundary.py::test_legacy_status_resolves_nested_context_and_submission_scope` | `room_lifecycle` | yes | yes |
| 16 | `tests/integration/test_stage2_boundary.py::test_legacy_status_rejects_empty_room_context_without_fallback` | `room_lifecycle` | yes | yes |
| 17 | `tests/integration/test_stage2_boundary.py::test_legacy_init_session_translates_and_executes` | `session_lifecycle` | yes | yes |
| 18 | `tests/integration/test_stage2_boundary.py::test_legacy_end_session_translates_and_executes` | `session_lifecycle` | yes | yes |
| 19 | `tests/integration/test_stage2_boundary.py::test_native_session_heartbeat_executes_through_client` | `session_lifecycle` | no | no |
| 20 | `tests/integration/test_stage2_boundary.py::test_legacy_task_checkpoint_translates_and_executes` | `task_checkpoint` | yes | yes |
| 21 | `tests/integration/test_stage2_boundary.py::test_legacy_thread_append_translates_and_executes` | `messaging_and_threads` | yes | yes |
| 22 | `tests/integration/test_stage2_boundary.py::test_legacy_send_translates_and_persists_mailbox_delivery` | `messaging_and_threads` | yes | yes |
| 23 | `tests/integration/test_stage2_boundary.py::test_legacy_broadcast_translates_executes_and_validates_arguments` | `messaging_and_threads` | yes | yes |
| 24 | `tests/integration/test_stage2_boundary.py::test_legacy_check_returns_only_the_callers_private_messages` | `messaging_and_threads` | yes | yes |
| 25 | `tests/integration/test_stage2_boundary.py::test_legacy_mark_read_translates_and_advances_cursor` | `messaging_and_threads` | yes | yes |
| 26 | `tests/integration/test_stage2_boundary.py::test_legacy_thread_promote_translates_and_marks_mailbox_source` | `messaging_and_threads` | yes | yes |
| 27 | `tests/integration/test_stage2_boundary.py::test_legacy_append_handoff_and_checkpoint_execute_end_to_end` | `messaging_and_threads` | yes | yes |
| 28 | `tests/integration/test_stage2_boundary.py::test_legacy_context_fill_translates_and_executes_read_only` | `messaging_and_threads` | yes | yes |
| 29 | `tests/integration/test_stage2_boundary.py::test_legacy_thread_react_translates_and_executes` | `messaging_and_threads` | yes | yes |
| 30 | `tests/integration/test_stage2_boundary.py::test_legacy_thread_react_remove_dispatches_to_unreact` | `messaging_and_threads` | yes | yes |
| 31 | `tests/integration/test_stage2_boundary.py::test_native_thread_react_remove_executes_through_client` | `messaging_and_threads` | no | no |
| 32 | `tests/integration/test_stage2_boundary.py::test_thread_react_rejects_unknown_action` | `messaging_and_threads` | yes | yes |
| 33 | `tests/integration/test_stage2_boundary.py::test_legacy_lesson_propose_translates_and_executes` | `lessons` | yes | yes |
| 34 | `tests/integration/test_stage2_boundary.py::test_legacy_lesson_broadcast_translates_and_delivers_to_room_members` | `lessons` | yes | yes |
| 35 | `tests/integration/test_stage2_boundary.py::test_legacy_lessons_list_translates_and_executes` | `lessons` | yes | yes |
| 36 | `tests/integration/test_stage2_boundary.py::test_legacy_list_nodes_translates_and_executes` | `peer_and_role_registry` | yes | yes |
| 37 | `tests/integration/test_stage2_boundary.py::test_legacy_register_node_translates_and_executes` | `peer_and_role_registry` | yes | yes |
| 38 | `tests/integration/test_stage2_boundary.py::test_native_bind_profile_and_legacy_model_status_execute` | `peer_and_role_registry` | yes | yes |
| 39 | `tests/integration/test_stage2_boundary.py::test_legacy_assign_role_and_role_status_translate_and_execute` | `peer_and_role_registry` | yes | yes |
| 40 | `tests/integration/test_stage2_boundary.py::test_legacy_release_role_translates_and_executes` | `peer_and_role_registry` | yes | yes |
| 41 | `tests/integration/test_stage2_boundary.py::test_legacy_release_unassigned_role_is_a_successful_noop` | `peer_and_role_registry` | yes | yes |
| 42 | `tests/integration/test_stage2_boundary.py::test_legacy_translation_ask` | `ask_dispatch` | yes | yes |
| 43 | `tests/integration/test_stage2_boundary.py::test_legacy_feedback_add_and_list_translate_and_execute` | `feedback` | yes | yes |
| 44 | `tests/integration/test_stage2_boundary.py::test_legacy_feedback_add_applies_legacy_defaults` | `feedback` | yes | yes |
| 45 | `tests/integration/test_stage2_boundary.py::test_legacy_feedback_resolve_translates_and_executes` | `feedback` | yes | yes |
| 46 | `tests/integration/test_stage2_boundary.py::test_legacy_report_error_aliases_translate_and_execute` | `diagnostics_and_alerts` | yes | yes |
| 47 | `tests/integration/test_stage2_boundary.py::test_legacy_report_error_applies_defaults` | `diagnostics_and_alerts` | yes | yes |
| 48 | `tests/integration/test_stage2_boundary.py::test_legacy_alert_raise_translates_and_executes_end_to_end` | `diagnostics_and_alerts` | yes | yes |
| 49 | `tests/integration/test_stage2_boundary.py::test_admit_dispatch_payload_requires_capability_tier` | `capability_admission` | no | no |
| 50 | `tests/integration/test_stage2_boundary.py::test_admit_dispatch_payload_rejects_unknown_capability_tier` | `capability_admission` | no | no |
| 51 | `tests/integration/test_stage2_boundary.py::test_admit_dispatch_payload_rejects_non_enum_capability_tiers` | `capability_admission` | no | no |
| 52 | `tests/integration/test_stage2_boundary.py::test_admit_dispatch_payload_accepts_every_declared_capability_tier` | `capability_admission` | no | no |
| 53 | `tests/integration/test_stage2_boundary.py::test_admit_success` | `capability_admission` | no | no |
| 54 | `tests/integration/test_stage2_boundary.py::test_missing_idempotency_key` | `capability_admission` | no | no |
| 55 | `tests/integration/test_stage2_boundary.py::test_admit_validation_error` | `capability_admission` | no | no |
| 56 | `tests/integration/test_stage2_boundary.py::test_admit_rejects_malformed_completion_contract_at_decode` | `capability_admission` | no | no |
| 57 | `tests/integration/test_stage2_boundary.py::test_unauthorized_client` | `capability_admission` | no | no |
| 58 | `tests/integration/test_stage2_boundary.py::test_unbacked_command` | `capability_admission` | no | no |
| 59 | `tests/integration/test_stage2_boundary.py::test_admit_rejected_internal_error` | `capability_admission` | no | no |
| 60 | `tests/integration/test_stage2_boundary.py::test_admit_route_exhausted` | `capability_admission` | no | no |
| 61 | `tests/integration/test_stage2_boundary.py::test_req_get_validation_error` | `request_management` | no | no |
| 62 | `tests/integration/test_stage2_boundary.py::test_request_get_enforces_resource_ownership` | `request_management` | no | no |
| 63 | `tests/integration/test_stage2_boundary.py::test_lease_get_validation_error` | `lease_management` | no | no |
| 64 | `tests/integration/test_stage2_boundary.py::test_lease_get_success` | `lease_management` | no | no |
| 65 | `tests/integration/test_stage2_boundary.py::test_lease_get_enforces_resource_ownership` | `lease_management` | no | no |

## Subsystem Breakdown Summary

| Subsystem Group | Total Nodes | Rewrite Needed | Already Native | Primary Actions / Commands Exercised |
|---|:---:|:---:|:---:|---|
| `consensus` | 4 | 4 | 0 | `consensus-propose`, `approval-request`, `consensus-sweep`, `arbiter-review` |
| `proposals` | 2 | 1 | 1 | `proposal-list` (`ProposalListCommand`) |
| `leadership` | 2 | 2 | 0 | `leader-claim`, `leader-yield` |
| `duty_and_terminal` | 4 | 4 | 0 | `terminal-close`, `terminal-duty-sweep` |
| `room_lifecycle` | 4 | 4 | 0 | `new-topic`, `status` (room resolution & scope) |
| `session_lifecycle` | 3 | 2 | 1 | `init-session`, `end-session`, `SessionHeartbeatCommand` |
| `task_checkpoint` | 1 | 1 | 0 | `task-checkpoint` |
| `messaging_and_threads` | 12 | 11 | 1 | `thread-append`, `send`, `broadcast`, `check`, `mark-read`, `thread-promote`, `append-handoff`, `checkpoint`, `context-fill`, `thread-react` |
| `lessons` | 3 | 3 | 0 | `lessons-propose`, `lesson-broadcast`, `lessons-list` |
| `peer_and_role_registry` | 6 | 6 | 0 | `list-nodes`, `register-node`, `model-status`, `assign-role`, `role-status`, `release-role` |
| `ask_dispatch` | 1 | 1 | 0 | `ask` (`dispatch.submit`) |
| `feedback` | 3 | 3 | 0 | `feedback-add`, `feedback-list`, `feedback-resolve` |
| `diagnostics_and_alerts` | 3 | 3 | 0 | `report-error`, `alert-raise` |
| `capability_admission` | 12 | 0 | 12 | `AdmitDispatch` payload decoding, tiers, contract validation, error paths |
| `request_management` | 2 | 0 | 2 | `RequestGet` validation & ownership enforcement |
| `lease_management` | 3 | 0 | 3 | `LeaseGet` validation, success & ownership enforcement |
| **TOTAL** | **65** | **45** | **20** | |

## Proposed Sub-Batch Split

To keep changes reviewable, manageable, and independently verifiable via `tools/legacy_retirement/gate.py` with 0 risk of runaway blast radius, the 45 nodes requiring rewriting are divided into **8 discrete sub-batches (8a through 8h)**. Each sub-batch targets 5–6 LegacyTranslator-using test nodes grouped by architectural domain:

### Sub-Batch 8a: Consensus & Proposals (5 nodes needing rewrite)
- **Domain**: Consensus rounds, governance approvals, sweep timeouts, arbiter reviews, proposal listings.
- **Critical Constraint**: Consensus-adjacent assertions on votes, quorums, escalation, and arbiter verdicts must be preserved byte-identical.
- **Nodes to rewrite (5)**:
  1. `test_legacy_consensus_propose_translates_and_executes` (`consensus-propose`)
  2. `test_legacy_approval_request_translates_and_executes` (`approval-request`)
  3. `test_legacy_consensus_sweep_translates_and_executes` (`consensus-sweep`)
  4. `test_legacy_proposal_list_translates_and_executes` (`proposal-list`)
  5. `test_legacy_arbiter_review_translates_and_executes` (`arbiter-review`)
- **Untouched native nodes (1)**: `test_native_proposal_list_includes_open_and_resolved_rounds`

### Sub-Batch 8b: Leadership & Duty Lifecycle (6 nodes needing rewrite)
- **Domain**: Leader election/claim/yield, terminal close sequences, and duty sweep lease expiration.
- **Nodes to rewrite (6)**:
  1. `test_legacy_leader_claim_translates_and_executes` (`leader-claim`)
  2. `test_legacy_leader_yield_translates_and_executes` (`leader-yield`)
  3. `test_legacy_terminal_close_translates_and_executes` (`terminal-close`)
  4. `test_terminal_close_can_end_duty_and_room_session` (`terminal-close`)
  5. `test_terminal_close_reports_session_failure_after_duty_close_and_retries` (`terminal-close`)
  6. `test_legacy_terminal_duty_sweep_expires_only_timed_out_lease` (`terminal-duty-sweep`)

### Sub-Batch 8c: Mailbox & Broadcast Messaging (5 nodes needing rewrite)
- **Domain**: Mailbox delivery, thread appends, room broadcast messaging, private message polling, and read-cursor advancement.
- **Nodes to rewrite (5)**:
  1. `test_legacy_thread_append_translates_and_executes` (`thread-append`)
  2. `test_legacy_send_translates_and_persists_mailbox_delivery` (`send`)
  3. `test_legacy_broadcast_translates_executes_and_validates_arguments` (`broadcast`)
  4. `test_legacy_check_returns_only_the_callers_private_messages` (`check`)
  5. `test_legacy_mark_read_translates_and_advances_cursor` (`mark-read`)

### Sub-Batch 8d: Thread Reactions, Handoffs & Context Fill (6 nodes needing rewrite)
- **Domain**: Thread promotion to mailbox, handoff appending, context-fill read-only queries, and emoji reaction add/remove toggles.
- **Nodes to rewrite (6)**:
  1. `test_legacy_thread_promote_translates_and_marks_mailbox_source` (`thread-promote`)
  2. `test_legacy_append_handoff_and_checkpoint_execute_end_to_end` (`append-handoff`, `checkpoint`)
  3. `test_legacy_context_fill_translates_and_executes_read_only` (`context-fill`)
  4. `test_legacy_thread_react_translates_and_executes` (`thread-react` ADD)
  5. `test_legacy_thread_react_remove_dispatches_to_unreact` (`thread-react` REMOVE)
  6. `test_thread_react_rejects_unknown_action` (`thread-react` invalid action translation)
- **Untouched native nodes (1)**: `test_native_thread_react_remove_executes_through_client`

### Sub-Batch 8e: Room Lifecycle & Session State (6 nodes needing rewrite)
- **Domain**: Room topic updates, room status queries across submission scopes and contexts, session initialization and termination.
- **Nodes to rewrite (6)**:
  1. `test_legacy_room_topic_translates_and_executes` (`new-topic`)
  2. `test_legacy_init_session_translates_and_executes` (`init-session`)
  3. `test_legacy_end_session_translates_and_executes` (`end-session`)
  4. `test_legacy_status_resolves_explicit_room_argument` (`status`)
  5. `test_legacy_status_resolves_nested_context_and_submission_scope` (`status`)
  6. `test_legacy_status_rejects_empty_room_context_without_fallback` (`status`)
- **Untouched native nodes (1)**: `test_native_session_heartbeat_executes_through_client`

### Sub-Batch 8f: Peer Registry, Profiles & Role Management (6 nodes needing rewrite)
- **Domain**: Node listing/registration, model profile binding status, role assignments and unassignments.
- **Nodes to rewrite (6)**:
  1. `test_legacy_list_nodes_translates_and_executes` (`list-nodes`)
  2. `test_legacy_register_node_translates_and_executes` (`register-node`, `list-nodes`)
  3. `test_native_bind_profile_and_legacy_model_status_execute` (`model-status`)
  4. `test_legacy_assign_role_and_role_status_translate_and_execute` (`assign-role`, `role-status`)
  5. `test_legacy_release_role_translates_and_executes` (`release-role`)
  6. `test_legacy_release_unassigned_role_is_a_successful_noop` (`release-role`)

### Sub-Batch 8g: Lessons, Task Checkpoints & Ask Dispatch (5 nodes needing rewrite)
- **Domain**: Lesson proposals, broadcasts, and listings; task checkpointing; legacy ask dispatch translation.
- **Nodes to rewrite (5)**:
  1. `test_legacy_task_checkpoint_translates_and_executes` (`task-checkpoint`)
  2. `test_legacy_lesson_propose_translates_and_executes` (`lessons-propose`)
  3. `test_legacy_lesson_broadcast_translates_and_delivers_to_room_members` (`lesson-broadcast`)
  4. `test_legacy_lessons_list_translates_and_executes` (`lessons-list`)
  5. `test_legacy_translation_ask` (`ask`)

### Sub-Batch 8h: Feedback, Error Reporting & Alerts (6 nodes needing rewrite)
- **Domain**: Feedback creation, listing, and resolution; error reporting aliases and defaults; system alert raising.
- **Nodes to rewrite (6)**:
  1. `test_legacy_feedback_add_and_list_translate_and_execute` (`feedback-add`, `feedback-list`)
  2. `test_legacy_feedback_add_applies_legacy_defaults` (`feedback-add`)
  3. `test_legacy_feedback_resolve_translates_and_executes` (`feedback-resolve`)
  4. `test_legacy_report_error_aliases_translate_and_execute` (`report-error`)
  5. `test_legacy_report_error_applies_defaults` (`report-error`)
  6. `test_legacy_alert_raise_translates_and_executes_end_to_end` (`alert-raise`)

### Non-Rewritten Subsystems (20 Fully Native Nodes)
The remaining 20 test nodes in `test_stage2_boundary.py` are already completely native (using typed commands like `AdmitDispatch`, `RequestGet`, `LeaseGet`, `SessionHeartbeatCommand`, etc.) and require zero modifications:
- **Capability Admission** (12 nodes): `test_admit_dispatch_payload_requires_capability_tier`, `test_admit_dispatch_payload_rejects_unknown_capability_tier`, `test_admit_dispatch_payload_rejects_non_enum_capability_tiers`, `test_admit_dispatch_payload_accepts_every_declared_capability_tier`, `test_admit_success`, `test_missing_idempotency_key`, `test_admit_validation_error`, `test_admit_rejects_malformed_completion_contract_at_decode`, `test_unauthorized_client`, `test_unbacked_command`, `test_admit_rejected_internal_error`, `test_admit_route_exhausted`
- **Request Management** (2 nodes): `test_req_get_validation_error`, `test_request_get_enforces_resource_ownership`
- **Lease Management** (3 nodes): `test_lease_get_validation_error`, `test_lease_get_success`, `test_lease_get_enforces_resource_ownership`
- **Threads** (1 node): `test_native_thread_react_remove_executes_through_client`
- **Sessions** (1 node): `test_native_session_heartbeat_executes_through_client`
- **Proposals** (1 node): `test_native_proposal_list_includes_open_and_resolved_rounds`

