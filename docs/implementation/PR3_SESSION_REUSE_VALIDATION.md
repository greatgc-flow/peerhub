# PR3 session selection validation

Recorded PR3 validation before review fixes: `python -m pytest -q` — 2314 passed; `pyright` — 0 errors.

Review fixes: 231 focused tests passed; pyright 0 errors with the development venv explicitly selected.

Test paths below are relative to `tests/communication/`.

| Gate item | Test name |
| --- | --- |
| Default/fresh/exact selection, older-session delta and receipts (native/local) | `bridge/test_session_selection.py::test_default_fresh_exact_and_older_session_delta` |
| CLI default/fresh/exact | `e2e/test_cli_session_selection.py::test_cli_default_fresh_exact` |
| Selector conflicts and handle syntax before store creation | `bridge/test_session_selection.py::test_invalid_selector_before_store_creation` |
| Stable CLI selector codes and nullable error envelope | `e2e/test_cli_session_selection.py::test_cli_selector_errors_have_final_nullable_envelope` |
| Lost session exits 1 before append, during resume and on replay | `e2e/test_cli_session_selection.py::test_cli_session_lost_exits_one` |
| Exact scope mismatch: Stream, Peer, workspace lineage | `bridge/test_session_selection.py::test_direct_exact_rejection_never_appends_allocates_or_invokes` |
| Exact binding, fingerprint and runtime mismatches | `bridge/test_session_selection.py::test_direct_exact_rejection_never_appends_allocates_or_invokes` |
| Missing, expired, lost or non-resumable session; missing vendor/watermark or untrusted watermark version | `bridge/test_session_selection.py::test_direct_exact_rejection_never_appends_allocates_or_invokes` |
| Overflow before prompt append | `bridge/test_session_selection.py::test_direct_overflow_is_checked_before_prompt_append` |
| Queued overflow before resume | `bridge/test_session_selection.py::test_exact_delta_overflow_is_rejected_before_resume` |
| Queued rejection atomically consumes Offset and permits progress | `bridge/test_session_selection.py::test_queued_exact_failure_is_consumed_atomically_and_replayable` |
| Queued expiry commits tombstone and rejection before resume | `bridge/test_session_selection.py::test_queued_expiry_and_claim_revalidation_before_resume` |
| Replay after tombstone and changed-selector conflicts | `bridge/test_session_selection.py::test_replay_after_tombstoning_and_changed_selector_conflict` |
| Rejection replay before liveness and changed-intent conflicts | `bridge/test_session_selection.py::test_queued_rejection_replays_before_liveness_and_changed_intent_conflicts` |
| Fingerprint/lineage conflicts before cached replay | `bridge/test_session_selection.py::test_complete_identity_is_checked_before_cached_replay` |
| Queued effective intent revalidation before allocation | `bridge/test_session_selection.py::test_queued_effective_intent_is_revalidated_before_allocation` |
| Exact resume failure never allocates a substitute | `bridge/test_session_selection.py::test_exact_resume_failure_never_creates_substitute` |
| Uncertain execution blocks selectors | `bridge/test_session_selection.py::test_uncertain_work_blocks_all_selectors` |
| Storage fault rolls back result, evidence and Offset | `bridge/test_session_selection.py::test_selector_rejection_storage_fault_rolls_back_result_evidence_and_offset` |
| Stale fence and open invocation marker refuse consumption | `bridge/test_session_selection.py::test_open_marker_and_lost_claim_never_qualify_for_selector_consumption` |
| Removed Python alias | `bridge/test_session_selection.py::test_removed_python_alias_is_rejected` |
| CLI help and live-helper selectors | `e2e/test_cli_session_selection.py::test_help_and_live_helper_selectors` |
| Four-turn live matrix (separate opt-in; not run) | `live/test_ask_measurements.py::test_fresh_exact_four_turn_session_matrix` |
