# Requirements registry

Each durable product requirement names the regression test that pins it. A fix is done only when its regression test exists. Superseded rows name their replacement by ID.

| ID | requirement | test |
| --- | --- | --- |
| R1 | The monitor's first line stays on screen at 118×37 in compact and expand. | `tests/communication/diag/test_monitor_recent_layout.py::test_heights_stay_within_the_bound_and_keep_the_top` |
| R2 | Compact and expand hide n/a pools by default. | `tests/communication/diag/test_monitor_na_hidden_400.py::test_default_pool_visibility_and_opt_in` |
| R3 | Compact and expand share section order. | `tests/communication/diag/test_monitor_section_order.py::test_full_frame_section_order` |
| R4 | Both monitor views show the Guide link in the header when links are enabled. | `tests/communication/diag/test_monitor_header_activity_links.py::test_title_reserves_guide_at_right_edge` |
| R5 | F-7D appears under C-7D and is limited by it. | `tests/communication/diag/test_monitor_pool_dependencies.py::test_fable_is_nested_and_blocked_by_fresh_common_pool_showing_the_parents_left_not_rewriting_the_payload` |
| R6 | PACE shows 0.00 for an unused window and uses two decimals. | `tests/communication/observation/test_quota_projection.py::test_unused_window_starting_after_the_measurement_shows_even_pace`, `tests/communication/diag/test_monitor_compact_view.py::test_compact_pace_cells_keep_signed_measurement_for_aged_rows` |
| R7 | The RUNNING count equals the number of active asks. | `tests/communication/diag/test_monitor_recent_layout.py::test_recent_header_counts_finished_asks_and_running_ones` |
| R8 | Expanded SESSIONS shows `uses N`. | `tests/communication/diag/test_monitor_view.py::test_expanded_sessions_show_uses_after_gen_and_compact_view_does_not` |
| R9 | Narrow terminals drop columns from the right. | `tests/communication/diag/test_monitor_pool_health.py::test_g_width_ladder_drops_details_but_keeps_core_columns` |
| R10 | Confirmed dead workers release leases and clear gone alerts. | `tests/communication/diag/test_diag_running.py::test_dead_ask_release_is_idempotent_and_preserves_uncertainty`, `tests/communication/diag/test_diag_running.py::test_monitor_and_diag_report_dead_asks_and_orphans_without_writes` |
| R11 | An agy quota probe must spend zero model turns. The token guard rejects consumption. | `tests/communication/observation/test_agy_usage_guard.py::test_num_turns_alone_is_consumption`, `tests/communication/observation/test_agy_usage_guard.py::test_tokens_alone_without_turns_is_consumption` |
| R12 | `peerhub diag --plan` is the AI view with pools, load and RAM. | `tests/communication/diag/test_diag_plan_load.py::test_plan_json_and_text_carry_load` |
| R13 | Release draft verification retries reads after draft creation (#486). | `tests/communication/package/test_release_flow.py::test_a_draft_missing_from_the_first_reads_after_create_is_still_verified` |
| R14 | Reset short windows hold without gating another pool. Both monitor views hide the expired reading by default. | `tests/communication/diag/test_diag_plan_facts.py::test_reset_short_window_holds_without_gating_and_is_hidden_in_both_views` |
| R15 | Both monitor views count running asks beyond the scope detail cap. | `tests/communication/diag/test_monitor_activity.py::test_running_counts_include_open_asks_beyond_scope_cap` |
| R16 | QUOTA groups pools by peer and orders peer groups and families by POOLS rank, keeping children under their parents. | `tests/communication/diag/test_monitor_pool_order.py::test_quota_groups_peers_and_families_by_pools_rank` |
