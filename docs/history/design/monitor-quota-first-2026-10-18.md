# Feasibility Report: Monitor Quota-First Display (Issue #146)
Date: 2026-10-18 | Status: Ratified | Scope: Monitor S1

## 1. Quota Read Model Fields & Window Derivation
- Fields present in `capacity` observation dictionaries:
  - `pool_ref` (str | None, e.g. `quota:ag:3P-7D`, `quota:cx:5h`)
  - `pool_kind` (str | None, e.g. `QUOTA`)
  - `measurements["remaining_fraction"]` (float 0.0-1.0; None if unmeasured)
  - `state` (`MEASURED`, `STALE`, `UNKNOWN`, `ERROR`)
  - `age_seconds` (float | None; freshness age from read clock)
  - `projection["pace_pt"]` (float | None; drift in percentage points)
  - `measurements["reset_in_seconds"]` (float | None; seconds to window reset)
  - `projection["exhaustion_at"]` (float | None; unix epoch UTC)
- Window derivation: parsed directly from `pool_ref` token suffix (e.g.
  `quota:ag:3P-7D` -> `7D`, `quota:cx:5h` -> `5H`). Never synthetically invented.

## 2. Snapshot vs Watch Header Differences
- `refresh_state`, `refresh_warnings`, and countdown (`next_refresh_in`) exist
  solely in `_run_monitor_grouped_watch` where `refresh_quota` actively runs.
- `_run_monitor_snapshot` is read-only; header reports status, UTC timestamp,
  and DB source/path. It must never claim "refresh OK" or display countdowns.

## 3. Existing Pinned Tests & Rewrite Strategy
- `tests/communication/diag/test_monitor_view.py`:
  - `test_rich_frame_respects_terminal_width_without_losing_groups`: pins box
    corners `╭─` and `╮`. Rewrite to assert single-line section rules.
  - `test_colored_heading_keeps_ansi_after_width_fitting`: pins `╭─ OVERVIEW`.
    Rewrite to assert ANSI styling on rule heading.
  - `test_capacity_rows_stay_contiguous_under_capacity_heading_and_usage_fallback`:
    pins `100% remaining ########`. Rewrite to tabular `REM [bar]` format.
  - `test_detail_loop_stopping_early_emits_hidden_by_terminal_height`: pins
    `+4 rows hidden by terminal height`. Rewrite to table overflow format.
- `tests/communication/e2e/test_cli_monitor.py`:
  - `test_monitor_expand_help_wording`: pins "quota is always fully shown;
    --expand applies to other groups". Rewrite to updated help text in
    `monitor.py:47` without the inaccurate claim.
  - `test_posix_pty_snapshot_obeys_physical_viewport`: pins omission wording.
    Rewrite to match single-line omission line and exact hint.

## 4. Overflow Policy Decision for 80x12
- Budget: 80 columns x 12 lines (max 11 physical lines).
- QUOTA block renders first beneath 1-line status header.
- Lower sections (PEERS, EXECUTION, SESSIONS, etc.) are suppressed if budget
  is consumed by quota rows.
- Never silently clip: emit omission line with exact hint:
  `peerhub monitor --snapshot --group capacity --expand`

## 5. UTC Timestamps
- All rendered timestamps (read_at, quota ETA) format in UTC with `Z` suffix
  (`%H:%M:%SZ` or `%Y-%m-%d %H:%M:%SZ`), never using local time offsets.
