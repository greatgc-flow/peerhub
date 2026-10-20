"""Human grouped monitor presentation stays readable and faithful to the read model."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.diag]


@pytest.fixture
def quota_visual_frame():
    states = ["on_pace", "watch", "threat", "exhausted", "ERROR", "UNKNOWN", "STALE", "insufficient"]
    observations = [
        {"peer_id": "ag", "pool_ref": f"quota:ag:P-{i + 1}H",
         "state": state if state in {"ERROR", "UNKNOWN", "STALE"} else "MEASURED",
         "measurements": {"remaining_fraction": [1.0, .5, .01, 0.0, .8, .8, .8, .9][i],
                          "reset_in_seconds": 7200},
         "projection": {"state": state, "exhaustion_at": 2800.0 if state == "threat" else None},
         "age_seconds": 30.0}
        for i, state in enumerate(states)
    ]
    observations.extend([
        {"peer_id": "cc", "pool_kind": "quota", "state": "ERROR",
         "reason": "claude_usage_timeout", "age_seconds": 10635},
        {"peer_id": "cc", "pool_kind": "quota", "state": "UNKNOWN", "reason": "duplicate"},
        {"peer_id": "ag", "pool_kind": "quota", "state": "UNKNOWN",
         "reason": "fresh measured evidence is required with a deliberately long explanation", "age_seconds": 2043},
    ])
    return {"status": "OK", "read_at": 1000.0, "selected_groups": ["capacity"],
            "groups": {"capacity": {"status": "OK", "data": {"observations": observations}}}}


@pytest.mark.parametrize("color", [False, True])
def test_quota_visual_snapshot_at_80_columns(quota_visual_frame, color, monkeypatch):
    from peerhub.cli.monitor_view import render_grouped_frame, _width

    monkeypatch.delenv("NO_COLOR", raising=False)
    rendered = render_grouped_frame(quota_visual_frame, color=color, width=80, height=24)
    snapshot = Path(__file__).with_name("fixtures") / f"quota_s2_color_{'on' if color else 'off'}.txt"
    assert rendered == snapshot.read_text(encoding="utf-8")
    assert all(_width(line) <= 79 for line in rendered.splitlines())
    assert rendered.count("cc probe:") == rendered.count("ag probe:") == 1
    assert "soonest exhaust: ag P-3H 1% in 30m" in rendered
    assert "duplicate" not in rendered
    assert "STATE: INSUFFICIENT=insufficient projection data" in rendered
    if color:
        for code, state in [(32, "ON_PACE"), (33, "WATCH"), (31, "THREAT"),
                            (31, "EXHAUSTED"), (31, "ERROR"), (2, "UNKNOWN"), (2, "STALE")]:
            assert any(line.startswith(f"\x1b[{code}m") and state in line for line in rendered.splitlines())
        assert any(line.startswith("\x1b[2mcc probe:") for line in rendered.splitlines())
    else:
        assert "\x1b" not in rendered


def test_quota_color_suppression_preserves_identical_text(quota_visual_frame, monkeypatch):
    from peerhub.cli.monitor_view import render_grouped_frame

    monkeypatch.delenv("NO_COLOR", raising=False)
    options = {"width": 80, "height": 24, "unicode": False}
    off = render_grouped_frame(quota_visual_frame, color=False, **options)
    on = render_grouped_frame(quota_visual_frame, color=True, **options)
    assert re.sub(r"\x1b\[[0-9;]*m", "", on) == off
    assert render_grouped_frame(quota_visual_frame, view="plain", color=True, **options) == off
    monkeypatch.setenv("NO_COLOR", "")
    assert render_grouped_frame(quota_visual_frame, color=True, **options) == off
    assert "\x1b" not in off


@pytest.mark.parametrize("unicode_mode", [False, True])
def test_quota_bar_boundaries_and_monotonic_fill(unicode_mode):
    from peerhub.cli.monitor_view import _bar_quota

    full, empty, low = ("█", "░", "▏") if unicode_mode else ("#", ".", ":")
    assert _bar_quota(1.0, unicode=unicode_mode) == f"[{full * 8}]"
    assert _bar_quota(.999, unicode=unicode_mode) == f"[{full * 8}]"
    assert _bar_quota(0.0, unicode=unicode_mode) == f"[{empty * 8}]"
    assert _bar_quota(.001, unicode=unicode_mode) == f"[{low}{empty * 7}]"
    bars = [_bar_quota(i / 1000, unicode=unicode_mode) for i in range(1001)]
    fills = [bar.count(full) + .1 * bar.count(low) for bar in bars]
    assert fills == sorted(fills)
    assert all(len(bar) == 10 for bar in bars)


def test_soonest_exhaustion_ignores_stale_and_counts_down(quota_visual_frame):
    from peerhub.cli.monitor_view import render_grouped_frame

    observations = quota_visual_frame["groups"]["capacity"]["data"]["observations"]
    observations[0]["projection"]["exhaustion_at"] = 4600
    observations[6]["projection"]["exhaustion_at"] = 1100
    quota_visual_frame["display_elapsed_seconds"] = 60
    rendered = render_grouped_frame(quota_visual_frame, width=80, height=12)
    assert "soonest exhaust: ag P-3H 1% in 29m" in rendered.splitlines()[0]
    assert "STATE:" not in rendered
    for i in range(1, 9):
        assert f"P-{i}H" in rendered


@pytest.mark.parametrize(("state", "seconds", "expected"), [
    ("MEASURED", 17880, "4h58m"), ("MEASURED", None, "-"),
    ("STALE", 17880, "-"), ("UNKNOWN", 17880, "-"),
])
@pytest.mark.parametrize("elapsed", [0, 60])
def test_quota_reset_countdown_plain_and_tty_parity(frame, state, seconds, expected, elapsed):
    from peerhub.cli.monitor_view import render_grouped_frame

    frame["selected_groups"] = ["capacity"]
    item = frame["groups"]["capacity"]["data"]["observations"][0]
    item.update(state=state, reset_in_seconds=seconds)
    frame["display_elapsed_seconds"] = elapsed
    if expected == "4h58m" and elapsed:
        expected = "4h57m"
    for view, unicode_mode in [("plain", False), ("rich", True)]:
        rendered = render_grouped_frame(frame, view=view, unicode=unicode_mode,
                                        width=120, height=24)
        header = next(line for line in rendered.splitlines() if "RESET-IN" in line)
        row = next(line for line in rendered.splitlines() if "5h" in line)
        start = header.index("RESET-IN")
        assert row[start:start + 9].strip() == expected


@pytest.fixture
def frame():
    return {
        "status": "PARTIAL",
        "read_at": 1000.0,
        "selected_groups": ["overview", "execution", "sessions", "usage", "context", "capacity", "runtime", "coverage", "store"],
        "groups": {
            "overview": {"status": "OK", "data": {"peers": [
                {"peer_id": "cx", "display_name": "Codex", "adapter_ref": "codex"}],
                "sample_size": 1, "truncated": False}},
            "execution": {"status": "OK", "data": {"scopes": [
                {"stream_id": "work", "peer_id": "cx",
                 "certainty": {"not_started": 1, "may_have_started": 0, "started": 2, "terminal": 3},
                 "latency_30d": {"sum_ns": 3_000_000_000, "count": 2}}],
                "sample_size": 1, "truncated": False}},
            "sessions": {"status": "OK", "data": {"items": [
                {"peer_id": "cx", "state": "ACTIVE", "generation": 2, "resumable": True,
                 "configured_binding": {"profile": "cx.standard", "model": "gpt-test", "effort": "medium"}}],
                "sample_size": 1, "truncated": False}},
            "usage": {"status": "OK", "data": {"sessions": [
                {"peer_id": "cx", "telemetry": {"normalized": {
                    "provider_input_tokens": 100, "output_tokens": 25, "provider_total_tokens": 125}}}],
                "coverage_by_scope": [{"peer_id": "cx", "complete": True}]}},
            "context": {"status": "OK", "data": {"sessions": [
                {"peer_id": "cx", "context": {"percent": 25.0, "state": "MEASURED", "reason": None}}]}},
            "capacity": {"status": "OK", "data": {"observations": [
                {"peer_id": "cx", "pool_ref": "quota:cx:5h", "state": "MEASURED",
                 "measurements": {"remaining_fraction": 0.75},
                 "projection": {"state": "safe", "pace_pt": 12.5}, "age_seconds": 30.0}]}},
            "runtime": {"status": "OK", "data": {"sessions": [
                {"peer_id": "cx", "configured_binding": {"profile": "cx.standard"},
                 "current_profile": {"source": "workspace", "status": "OK"}}]}},
            "coverage": {"status": "OK", "data": {"scopes": [
                {"peer_id": "cx", "delivery_complete": True,
                 "usage_coverage": {"measured": 3, "terminal": 3, "complete": True}}], "truncated": False}},
            "store": {"status": "OK", "data": {"schema_version": 7, "database_size_bytes": 2048}},
        },
    }


def test_rich_grouped_view_is_visual_and_never_dumps_json(frame):
    from peerhub.cli.monitor_view import render_grouped_frame

    rendered = render_grouped_frame(frame, view="rich", color=False, unicode=True, width=100)

    for group_id in frame["selected_groups"]:
        group_label = "QUOTA" if group_id == "capacity" else group_id.upper()
        assert group_label in rendered
    assert "Codex" in rendered
    assert "cx.standard" in rendered
    assert "75%" in rendered
    assert "SAFE" in rendered and "30s" in rendered
    assert "█" in rendered and "░" in rendered
    assert "delivery" in rendered.lower() and "terminal" in rendered.lower()
    assert "task success" not in rendered.lower()
    assert not re.search(r"^\s*[\[{]", rendered, re.MULTILINE)
    assert '"peer_id"' not in rendered


def test_plain_grouped_view_is_aligned_ascii_without_terminal_controls(frame):
    from peerhub.cli.monitor_view import render_grouped_frame

    rendered = render_grouped_frame(frame, view="plain", color=False, unicode=False, width=80)

    assert "OVERVIEW" in rendered and "QUOTA" in rendered
    assert "#" in rendered and "." in rendered
    assert "\x1b" not in rendered and "\r" not in rendered
    rendered.encode("ascii")
    assert '"peer_id"' not in rendered


@pytest.mark.parametrize("width", [20, 40, 60, 80, 120, 200])
def test_compact_width_keeps_every_selected_group_visible(frame, width):
    from peerhub.cli.monitor_view import render_grouped_frame

    rendered = render_grouped_frame(frame, view="plain", color=False, unicode=False, width=width)

    assert all(group.upper() in rendered or (group == "capacity" and "QUOTA" in rendered) for group in frame["selected_groups"])
    assert "truncated" in rendered.lower() or "partial" in rendered.lower()
    assert all(len(line) <= width for line in rendered.splitlines())


def test_grouped_view_reports_stale_unknown_and_partial_states_explicitly(frame):
    from peerhub.cli.monitor_view import render_grouped_frame

    frame["groups"]["context"]["data"]["sessions"][0]["context"].update(
        percent=None, state="UNKNOWN", reason="no_context_measurement")
    frame["groups"]["capacity"]["status"] = "PARTIAL"
    frame["groups"]["capacity"]["coverage"] = {
        "read": "partial", "sample_size": 101, "truncated": True, "reason": "sample_cap"}

    rendered = render_grouped_frame(frame, view="plain", color=False, unicode=False, width=100)

    assert "UNKNOWN" in rendered
    assert "no_context_measurement" in rendered
    assert "PARTIAL" in rendered
    assert "sample_cap" in rendered or "truncated" in rendered.lower()


def test_coverage_group_does_not_call_empty_scope_set_complete(frame):
    from peerhub.cli.monitor_view import render_grouped_frame

    frame["groups"]["coverage"]["data"]["scopes"] = []
    rendered = render_grouped_frame(frame, view="plain", unicode=False)
    assert "No scope evidence" in rendered
    assert "Scopes complete: 0/0" not in rendered


def test_human_renderer_is_isolated_from_schema_11_adapter():
    import ast
    from pathlib import Path

    source = Path(__file__).resolve().parents[3] / "peerhub" / "cli" / "monitor_view.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    imported = [node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
    imported.extend(alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names)
    assert all("monitor_schema11" not in name for name in imported)


@pytest.mark.parametrize("width", [32, 48, 80, 120])
def test_rich_frame_respects_terminal_width_without_losing_groups(frame, width):
    from peerhub.cli.monitor_view import render_grouped_frame, _width

    rendered = render_grouped_frame(frame, view="rich", color=True, unicode=True, width=width)
    assert all(group.upper() in rendered or (group == "capacity" and "QUOTA" in rendered) for group in frame["selected_groups"])
    assert all(_width(line) <= width for line in rendered.splitlines())
    if width >= 48:
        assert any("── " in line for line in rendered.splitlines())


def test_colored_heading_keeps_ansi_after_width_fitting(frame, monkeypatch):
    from peerhub.cli.monitor_view import render_grouped_frame

    monkeypatch.delenv("NO_COLOR", raising=False)
    rendered = render_grouped_frame(frame, view="rich", color=True, unicode=True, width=80)
    assert "\x1b[1;36m── OVERVIEW" in rendered
    assert "OVERVIEW [OK]" in rendered
    assert "\x1b[0m" in rendered


@pytest.mark.parametrize("width", [15, 20, 40, 80, 120, 200])
@pytest.mark.parametrize("height", [1, 2, 3, 4, 5, 8, 24, 60])
def test_height_aware_frame_stays_inside_viewport_and_discloses_overflow(frame, width, height):
    from peerhub.cli.monitor_view import render_grouped_frame

    rendered = render_grouped_frame(frame, view="plain", unicode=False, width=width, height=height)

    assert len(rendered.splitlines()) <= height - 1
    assert all(len(line) <= width - 1 for line in rendered.splitlines())
    if height == 2:
        assert f"+{len(frame['selected_groups'])}" in rendered
        if width >= 64:
            assert "peerhub monitor --snapshot --group capacity --expand" in rendered
        else:
            assert "--expand" in rendered
    elif height >= 3 and len(frame["selected_groups"]) > max(0, height - 3):
        assert "omitted" in rendered or "hidden" in rendered or any(f"+{i}" in rendered for i in range(1, 10))
        if width >= 64:
            assert any(f"peerhub monitor --snapshot --group {group} --expand" in rendered
                       for group in ("capacity", "all"))
        else:
            assert "--expand" in rendered


def test_height_budget_prioritizes_errors_and_preserves_catalog_order(frame):
    from peerhub.cli.monitor_view import render_grouped_frame

    frame["selected_groups"] = ["overview", "execution", "coverage", "store"]
    frame["groups"]["coverage"]["status"] = "ERROR"
    frame["groups"]["execution"]["status"] = "PARTIAL"
    rendered = render_grouped_frame(frame, view="plain", unicode=False, width=80, height=7)
    visible = rendered.upper()

    assert visible.index("COVERAGE [ERROR]") < visible.index("EXECUTION [PARTIAL]")
    assert "2 GROUPS OMITTED" in visible or "GROUPS OMITTED" in visible


def test_full_severity_classes_keep_catalog_order_when_every_summary_fits(frame):
    from peerhub.cli.monitor_view import render_grouped_frame

    statuses = {"overview": "ERROR", "sessions": "UNAVAILABLE", "usage": "STALE",
                "context": "PARTIAL", "runtime": "UNKNOWN", "execution": "OK",
                "capacity": "OK", "coverage": "OK", "store": "OK"}
    for group_id, status in statuses.items():
        frame["groups"][group_id]["status"] = status

    rendered = render_grouped_frame(frame, view="plain", unicode=False, width=100, height=28)
    headings = [line.split(" [", 1)[0].lstrip("-─ ").strip() for line in rendered.splitlines() if line.startswith(("-- ", "── ")) and " [" in line]

    assert headings == ["QUOTA", "OVERVIEW", "EXECUTION", "SESSIONS", "USAGE", "CONTEXT", "RUNTIME", "COVERAGE", "STORE"]


def test_store_provenance_and_safe_read_error_are_visible(frame):
    from peerhub.cli.monitor_view import render_grouped_frame

    frame["store_selection"] = {"path": "D:/users/alice/private/core.db", "source": "discovered"}
    frame["errors"] = {"sessions": "OperationalError: secret-token SELECT * FROM private"}
    frame["groups"]["sessions"]["status"] = "ERROR"
    rendered = render_grouped_frame(frame, view="plain", unicode=False, width=100, height=24)

    assert "discovered" in rendered
    assert "core.db" in rendered
    assert "OperationalError" in rendered or "database read failed" in rendered.lower()
    assert "secret-token" not in rendered and "SELECT *" not in rendered


def test_narrow_store_header_retains_source_while_eliding_path(frame):
    from peerhub.cli.monitor_view import render_grouped_frame

    frame["store_selection"] = {"path": "D:/a/very/long/location/core.db", "source": "workspace"}
    rendered = render_grouped_frame(frame, view="plain", unicode=False, width=40, height=24)

    assert "workspace" in rendered
    assert "D:/a/very/long/location/core.db" not in rendered


def test_dependent_error_groups_show_safe_root_reason(frame):
    from peerhub.cli.monitor_view import render_grouped_frame

    frame["groups"]["coverage"]["status"] = "ERROR"
    frame["errors"] = {"execution": "OperationalError: SELECT secret from user"}
    rendered = render_grouped_frame(frame, view="plain", unicode=False, width=100, height=24)

    assert "operationalerror: database read failed" in rendered.lower()
    assert "SELECT secret" not in rendered


def test_display_width_ignores_combining_and_joiner_and_sanitizes_bidi(frame):
    from peerhub.cli.monitor_view import render_grouped_frame, _width

    rendered = render_grouped_frame(frame, view="plain", unicode=True, width=20, height=8)
    assert _width("e\u0301") == 1
    assert _width("👩\u200d💻") <= 4
    assert "\u202e" not in rendered


def test_cjk_combining_and_joined_emoji_fit_actual_viewport(frame):
    from peerhub.cli.monitor_view import render_grouped_frame, _width

    frame["selected_groups"] = ["overview"]
    frame["groups"]["overview"]["data"]["peers"][0]["display_name"] = "\u202e\u200b東京 cafe\u0301 👩\u200d💻\u0378"
    rendered = render_grouped_frame(frame, view="rich", unicode=True, width=20, height=8)

    assert all(_width(line) <= 19 for line in rendered.splitlines())
    assert "\u202e" not in rendered
    assert "�" in rendered


def test_unicode_line_separators_cannot_inflate_physical_frame_height(frame):
    from peerhub.cli.monitor_view import render_grouped_frame

    frame["groups"]["overview"]["data"]["peers"][0]["display_name"] = "A\u2028B\u2029C"
    rendered = render_grouped_frame(frame, view="plain", unicode=True, width=80, height=8)
    physical_lines = rendered.rstrip("\n").split("\n")

    assert len(physical_lines) <= 7
    assert "\u2028" not in rendered and "\u2029" not in rendered


def test_refresh_warning_reason_is_bounded_and_sanitized(frame):
    from peerhub.cli.monitor_view import render_grouped_frame

    frame["refresh_state"] = "REFRESH PARTIAL · 1 warnings"
    frame["refresh_warnings"] = ["cx: probe_timeout: secret at D:/private/token"]
    rendered = render_grouped_frame(frame, view="plain", unicode=False, width=100, height=24)

    assert "Warning: cx probe_timeout" in rendered
    assert "secret" not in rendered and "D:/private/token" not in rendered

    frame["refresh_warnings"] = ["secret-token: private value"]
    rendered = render_grouped_frame(frame, view="plain", unicode=False, width=100, height=24)
    assert "secret-token" not in rendered

    frame["refresh_warnings"] = [{"secret": "token"}]
    rendered = render_grouped_frame(frame, view="plain", unicode=False, width=100, height=24)
    assert "probe_warning" in rendered and "secret" not in rendered


def test_agy_stop_hint_is_available_in_frame_when_it_fits(frame):
    from peerhub.cli.monitor_view import render_grouped_frame

    frame["refresh_state"] = "STOPPED · AGY TOKEN USE"
    rendered = render_grouped_frame(frame, view="plain", unicode=False, width=100, height=24)

    assert "--allow-agy-token-use" in rendered


def test_append_only_renderer_keeps_full_legacy_human_frame(frame):
    from peerhub.cli.monitor_view import render_grouped_frame

    rendered = render_grouped_frame(frame, view="plain", unicode=False, width=80)

    assert len(rendered.splitlines()) > 10
    assert all(group.upper() in rendered or (group == "capacity" and "QUOTA" in rendered) for group in frame["selected_groups"])
    assert "D:/" not in rendered


def test_plain_ascii_monitor_replaces_middle_dot_cleanly_without_question_marks(frame):
    from peerhub.cli.monitor_view import render_grouped_frame

    frame["refresh_state"] = "REFRESH PARTIAL · 1 warnings"
    rendered = render_grouped_frame(frame, view="plain", unicode=False, width=100, height=24)

    assert "·" not in rendered
    # Delimiters should be ASCII | rather than replacing · with ?
    assert " | " in rendered
    assert "REFRESH PARTIAL | 1 WARNINGS" in rendered
    assert "1.50s avg | 2 samples" in rendered
    assert "Schema 7 | database" in rendered
    # Ensure no ? is produced from middle dot replacement
    for line in rendered.splitlines():
        assert " ? " not in line


def test_expand_renders_all_loaded_detail_rows_and_session_handle(frame):
    from peerhub.cli.monitor_view import render_grouped_frame, _width

    # Add 4 sessions so total is 4 (exceeding 2-row cap)
    frame["groups"]["sessions"]["data"]["items"] = [
        {"peer_id": "cx", "state": "ACTIVE", "generation": i, "resumable": True, "handle": f"sess_test_{i}",
         "configured_binding": {"profile": "cx.standard", "model": "gpt-test", "effort": "medium"}}
        for i in range(1, 5)
    ]
    frame["groups"]["usage"]["data"]["sessions"] = [
        {"peer_id": "cx", "telemetry": {"normalized": {"provider_total_tokens": 100 * i}}}
        for i in range(1, 5)
    ]
    frame["groups"]["context"]["data"]["sessions"] = [
        {"peer_id": "cx", "context": {"percent": 10.0 * i, "state": "MEASURED", "reason": None}}
        for i in range(1, 5)
    ]
    frame["groups"]["capacity"]["data"]["observations"] = [
        {"peer_id": "cx", "pool_ref": f"quota:cx:{i}h", "state": "MEASURED",
         "measurements": {"remaining_fraction": 0.2 * i},
         "projection": {"state": "safe", "pace_pt": 5.0}, "age_seconds": 10.0}
        for i in range(1, 5)
    ]
    frame["groups"]["runtime"]["data"]["sessions"] = [
        {"peer_id": "cx", "configured_binding": {"profile": "cx.standard"},
         "current_profile": {"source": "workspace", "status": "OK"}}
        for i in range(1, 5)
    ]

    # Default (expand=False): 2-row cap + "+N not shown" for sessions/runtime; ALL quota observations visible
    default_rendered = render_grouped_frame(frame, view="plain", unicode=False, width=80)
    assert "+2 sessions not shown" in default_rendered
    assert "+2 runtime sessions not shown" in default_rendered
    assert "sess_test_1" not in default_rendered
    assert "1h" in default_rendered
    assert "4h" in default_rendered

    # Expanded (expand=True): ALL rows rendered across all groups, no "+N not shown", session handle present
    expanded_rendered = render_grouped_frame(frame, view="plain", unicode=False, width=80, expand=True)
    assert "not shown" not in expanded_rendered
    assert "sess_test_1" in expanded_rendered
    assert "sess_test_4" in expanded_rendered
    # Widths must be <= width - 1 (79 columns)
    for line in expanded_rendered.splitlines():
        assert _width(line) <= 79, f"Line exceeds width - 1: {line}"


def test_limited_height_drops_other_groups_before_clipping_quota(frame):
    """When terminal height is limited, other groups are dropped before clipping quota rows."""
    from peerhub.cli.monitor_view import render_grouped_frame

    frame["groups"]["capacity"]["data"]["observations"] = [
        {"peer_id": "cx", "pool_ref": f"quota:cx:{i}h", "state": "MEASURED",
         "measurements": {"remaining_fraction": 0.2 * i},
         "projection": {"state": "safe", "pace_pt": 5.0}, "age_seconds": 10.0}
        for i in range(1, 5)
    ]
    # Height 14 is too tight to fit all group summaries (9 * 2 = 18 lines) plus 3 detail lines
    rendered = render_grouped_frame(frame, view="plain", unicode=False, width=80, height=14)
    # Other groups dropped
    assert "groups omitted" in rendered
    # All 4 quota observations are retained unclipped
    for i in range(1, 5):
        assert f"{i}h" in rendered
    assert len(rendered.splitlines()) <= 13


def test_default_output_byte_identical_when_expand_is_false(frame):
    from peerhub.cli.monitor_view import render_grouped_frame

    out_implicit = render_grouped_frame(frame, view="plain", unicode=False, width=80)
    out_explicit = render_grouped_frame(frame, view="plain", unicode=False, width=80, expand=False)
    assert out_implicit == out_explicit

    out_implicit_rich = render_grouped_frame(frame, view="rich", unicode=True, width=100)
    out_explicit_rich = render_grouped_frame(frame, view="rich", unicode=True, width=100, expand=False)
    assert out_implicit_rich == out_explicit_rich


def test_detail_loop_stopping_early_emits_hidden_by_terminal_height(frame):
    """When a detail loop stops early due to viewport height, emit '+N rows hidden by terminal height'."""
    from peerhub.cli.monitor_view import render_grouped_frame

    frame["selected_groups"] = ["capacity"]
    frame["groups"]["capacity"]["data"]["observations"] = [
        {"peer_id": "cx", "pool_ref": f"quota:cx:{i}h", "state": "MEASURED",
         "measurements": {"remaining_fraction": 0.1 * i},
         "projection": {"state": "safe", "pace_pt": 5.0}, "age_seconds": 10.0}
        for i in range(1, 7)
    ]
    # Height 6: 1 title + 1 heading + 1 table header + 1 detail row + 1 hidden line = 5 lines (<= height - 1)
    rendered = render_grouped_frame(frame, view="plain", unicode=False, width=80, height=6)
    assert "rows hidden" in rendered
    assert "peerhub monitor --snapshot --group capacity --expand" in rendered
    assert "1h" in rendered
    assert "6h" not in rendered
    assert len(rendered.splitlines()) <= 5


def test_warning_and_next_stop_overhead_accounted_for_without_clipping_quota(frame):
    """lines_used accounts for warning and Next: stop lines so quota is not clipped while other groups are dropped."""
    from peerhub.cli.monitor_view import render_grouped_frame

    frame["groups"]["capacity"]["data"]["observations"] = [
        {"peer_id": "cx", "pool_ref": f"quota:cx:{i}h", "state": "MEASURED",
         "measurements": {"remaining_fraction": 0.2 * i},
         "projection": {"state": "safe", "pace_pt": 5.0}, "age_seconds": 10.0}
        for i in range(1, 5)
    ]
    frame["refresh_state"] = "STOPPED · AGY TOKEN USE"
    frame["refresh_warnings"] = ["cx: probe_timeout"]
    rendered = render_grouped_frame(frame, view="plain", unicode=False, width=80, height=15)
    assert "--allow-agy-token-use" in rendered
    assert "Warning: cx probe_timeout" in rendered
    # Quota rows must not be clipped
    for i in range(1, 5):
        assert f"{i}h" in rendered, f"{i}h was clipped"
    assert len(rendered.splitlines()) <= 14


def test_capacity_rows_stay_contiguous_under_capacity_heading_and_usage_fallback(frame):
    """#146 and #147: all CAPACITY rows render inside QUOTA [OK] (before RUNTIME/STORE), bar fills with remaining %, and USAGE computes input+output fallback when provider_total_tokens is None."""
    from peerhub.cli.monitor_view import render_grouped_frame

    frame["groups"]["capacity"]["data"]["observations"] = [
        {"peer_id": "cx", "pool_ref": "quota:cx:5h", "state": "MEASURED",
         "measurements": {"remaining_fraction": 1.0, "reset_in_seconds": 7500},
         "projection": {"state": "on_pace", "pace_pt": -2.0}, "age_seconds": 10.0},
        {"peer_id": "ag", "pool_ref": "quota:ag:g-5h", "state": "MEASURED",
         "measurements": {"remaining_fraction": 0.0, "reset_in_seconds": 1800},
         "projection": {"state": "exhausted", "pace_pt": 20.0}, "age_seconds": 10.0},
    ]
    frame["groups"]["usage"]["data"]["sessions"] = [
        {"peer_id": "cx", "telemetry": {"normalized": {
            "provider_input_tokens": 1000, "cached_input_tokens": 800, "output_tokens": 250, "provider_total_tokens": None}}},
    ]

    rendered = render_grouped_frame(frame, view="plain", unicode=False, width=100, height=28)
    cap_idx = rendered.index("QUOTA [OK]")
    first_pool_idx = rendered.index("g-5h")
    second_pool_idx = rendered.index("5h")
    runtime_idx = rendered.index("RUNTIME [OK]")
    store_idx = rendered.index("STORE [OK]")

    # Contiguous inside QUOTA before RUNTIME and STORE
    assert cap_idx < first_pool_idx < second_pool_idx < runtime_idx < store_idx
    # 100% remaining has full # bar; 0% remaining has empty . bar
    assert "100%  [########]" in rendered
    assert "0%  [........]" in rendered
    assert "2h05m" in rendered
    assert "30m" in rendered
    assert "cx provider-reported tokens: 1,250 | cache 80%" in rendered


def test_monitor_capacity_renders_argon_a_7d_pool_row_like_other_pools(frame):
    """#149: A-7D pool row renders in QUOTA like other pools without special casing."""
    from peerhub.cli.monitor_view import render_grouped_frame

    frame["groups"]["capacity"]["data"]["observations"] = [
        {
            "peer_id": "ag",
            "pool_ref": "quota:ag:A-7D",
            "state": "MEASURED",
            "measurements": {"remaining_fraction": 0.406, "reset_in_seconds": 550000},
            "projection": {"state": "on_pace", "pace_pt": 0.0},
            "age_seconds": 15.0,
        },
    ]

    rendered = render_grouped_frame(frame, view="plain", unicode=False, width=100, height=28)
    assert "QUOTA [OK]" in rendered
    assert "A-7D" in rendered
    assert "41%" in rendered
    assert "ag" in rendered


@pytest.mark.parametrize("color", [False, True])
def test_fixed_time_render_fixtures_viewport_matrix(color, monkeypatch):
    """Fixed-time render fixtures at 80x12, 80x24, 100x30, 120x40 (+plain) obey boundaries."""
    from peerhub.cli.monitor_view import render_grouped_frame, _width

    monkeypatch.delenv("NO_COLOR", raising=False)
    t0 = 1760790000.0  # 2025-10-18T12:20:00Z
    fixture = {
        "status": "OK",
        "read_at": t0,
        "selected_groups": ["overview", "peers", "execution", "sessions", "usage", "context", "capacity", "coverage", "store"],
        "store_selection": {"path": "D:/workspace/core.db", "source": "workspace"},
        "groups": {
            "capacity": {
                "status": "OK",
                "data": {
                    "observations": [
                        {"peer_id": "ag", "pool_ref": "quota:ag:3P-5H", "state": "MEASURED",
                         "measurements": {"remaining_fraction": 1.0, "reset_in_seconds": 18000.0},
                         "projection": {"state": "on_pace", "pace_pt": 0.0}, "age_seconds": 30.0},
                        {"peer_id": "ag", "pool_ref": "quota:ag:3P-7D", "state": "MEASURED",
                         "measurements": {"remaining_fraction": 0.74, "reset_in_seconds": 86400.0},
                         "projection": {"state": "on_pace", "pace_pt": 1.2, "exhaustion_at": t0 + 200000.0}, "age_seconds": 59.0},
                        {"peer_id": "ag", "pool_ref": "quota:ag:G-5H", "state": "MEASURED",
                         "measurements": {"remaining_fraction": 1.0, "reset_in_seconds": 18000.0},
                         "projection": {"state": "on_pace", "pace_pt": 0.0}, "age_seconds": 30.0},
                        {"peer_id": "ag", "pool_ref": "quota:ag:G-7D", "state": "MEASURED",
                         "measurements": {"remaining_fraction": 0.54, "reset_in_seconds": 300000.0},
                         "projection": {"state": "on_pace", "pace_pt": -0.5}, "age_seconds": 30.0},
                        {"peer_id": "cc", "pool_ref": "quota:cc:C-5H", "state": "MEASURED",
                         "measurements": {"remaining_fraction": 0.95, "reset_in_seconds": 14000.0},
                         "projection": {"state": "safe", "pace_pt": -1.0}, "age_seconds": 12.0},
                        {"peer_id": "cc", "pool_ref": "quota:cc:C-7D", "state": "MEASURED",
                         "measurements": {"remaining_fraction": 0.20, "reset_in_seconds": 100000.0},
                         "projection": {"state": "exhausted", "pace_pt": 15.0, "exhaustion_at": t0 + 7200.0}, "age_seconds": 12.0},
                        {"peer_id": "cx", "pool_ref": "quota:cx:X-5H", "state": "MEASURED",
                         "measurements": {"remaining_fraction": 0.99, "reset_in_seconds": 17800.0},
                         "projection": {"state": "safe", "pace_pt": 0.0}, "age_seconds": 5.0},
                        {"peer_id": "cx", "pool_ref": "quota:cx:X-7D", "state": "MEASURED",
                         "measurements": {"remaining_fraction": 0.96, "reset_in_seconds": 580000.0},
                         "projection": {"state": "safe", "pace_pt": 0.0}, "age_seconds": 5.0},
                    ]
                }
            },
            "peers": {"status": "OK", "data": {"peers": []}},
            "overview": {"status": "OK", "data": {"peers": []}},
            "execution": {"status": "OK", "data": {"scopes": []}},
            "sessions": {"status": "OK", "data": {"items": []}},
            "usage": {"status": "OK", "data": {"sessions": []}},
            "context": {"status": "OK", "data": {"sessions": []}},
            "coverage": {"status": "OK", "data": {"scopes": []}},
            "store": {"status": "OK", "data": {"schema_version": 4, "database_size_bytes": 1024}},
        }
    }

    for width, height in [(80, 12), (80, 24), (100, 30), (120, 40)]:
        for view, unicode_mode in [("rich", True), ("plain", False)]:
            rendered = render_grouped_frame(fixture, view=view, color=color, unicode=unicode_mode, width=width, height=height)
            lines = rendered.rstrip("\n").split("\n")
            assert len(lines) <= height - 1, f"Height exceeded: {len(lines)} > {height - 1} at {width}x{height} {view}"
            for line in lines:
                assert _width(re.sub(r"\x1b\[[0-9;]*m", "", line)) <= width - 1, f"Line width exceeded at {width}x{height}: {line}"

            # Quota block is always at top
            assert "QUOTA [OK]" in rendered
            first_rule_idx = min(i for i, l in enumerate(lines) if "QUOTA [OK]" in l)
            assert first_rule_idx == (0 if (width, height) == (80, 12) else 1)

            # Check columns
            if width >= 100:
                assert "EXHAUST-IN" in rendered
            else:
                assert "EXHAUST-IN" not in rendered

            if (width, height) == (80, 12):
                for observation in fixture["groups"]["capacity"]["data"]["observations"]:
                    assert observation["pool_ref"].split(":")[-1] in rendered
                # All eight quota pools fit; the hint must recover the eight other groups.
                assert "8 groups omitted" in rendered
                assert "peerhub monitor --snapshot --group all --expand" in rendered
                assert "--group capacity" not in rendered
                assert "STORE" not in rendered

            if not unicode_mode:
                assert "·" not in rendered
                assert "█" not in rendered
                rendered.encode("ascii")


@pytest.mark.parametrize("width", [1, 2, 10, 19, 20, 50, 60, 80, 100, 120])
@pytest.mark.parametrize("expand", [False, True])
@pytest.mark.parametrize("unicode_mode", [False, True])
def test_append_frame_leaves_terminal_last_column_free(frame, width, expand, unicode_mode):
    from peerhub.cli.monitor_view import render_grouped_frame, _width

    rendered = render_grouped_frame(frame, width=width, unicode=unicode_mode,
                                    color=unicode_mode, expand=expand)
    assert all(_width(line) <= width - 1 for line in rendered.splitlines())
    if 10 <= width < 20:
        assert "narrow" in rendered.lower()


@pytest.mark.parametrize("width", [50, 60])
@pytest.mark.parametrize("height", [None, 24])
def test_narrow_quota_keeps_identity_remaining_reset_and_state(frame, width, height):
    from peerhub.cli.monitor_view import render_grouped_frame

    frame["selected_groups"] = ["capacity"]
    item = frame["groups"]["capacity"]["data"]["observations"][0]
    item.update(pool_ref="quota:cx:X-7D", projection={"state": "exhausted"})
    item["measurements"]["reset_in_seconds"] = 90000
    rendered = render_grouped_frame(frame, width=width, height=height, unicode=False)
    header = next(line for line in rendered.splitlines() if "PEER" in line)
    for label in ("PEER", "POOL", "REM", "RESET-IN", "STATE"):
        assert label in header
    row = next(line for line in rendered.splitlines() if "X-7D" in line)
    for value in ("cx", "X-7D", "75%", "1d01h", "EXHAUSTED"):
        assert value in row
    assert "Columns omitted:" in rendered
    for label in ("bar", "exhaust-in", "age"):
        assert label in rendered


@pytest.mark.parametrize("expand, height, hidden", [(False, 5, 2), (True, 6, 3)])
def test_bounded_other_details_report_exact_hidden_rows(frame, expand, height, hidden):
    from peerhub.cli.monitor_view import render_grouped_frame

    frame["selected_groups"] = ["usage"]
    frame["groups"]["usage"]["data"] = {"sessions": [
        {"peer_id": f"worker{i}", "telemetry": {"normalized": {"provider_total_tokens": i}}}
        for i in range(1, 6)
    ]}
    rendered = render_grouped_frame(frame, width=100, height=height, unicode=False, expand=expand)
    assert f"+{hidden} rows hidden" in rendered
    assert "--expand" in rendered
    assert "worker1" in rendered
    if expand:
        assert "worker2" in rendered
    assert len(rendered.splitlines()) == height - 1


@pytest.mark.parametrize("width", [64, 80, 100, 120])
def test_omitted_groups_disclose_loaded_expanded_rows(frame, width):
    from peerhub.cli.monitor_view import render_grouped_frame

    frame["selected_groups"] = ["capacity", "usage"]
    frame["groups"]["usage"]["data"] = {"sessions": [
        {"peer_id": f"worker{i}"} for i in range(5)
    ]}
    rendered = render_grouped_frame(frame, width=width, height=6, unicode=False, expand=True)
    assert "1 groups omitted" in rendered
    assert "+5 rows hidden" in rendered or "+5r" in rendered
    assert "--expand" in rendered


def test_group_summaries_reserve_space_for_hidden_detail_disclosure(frame):
    from peerhub.cli.monitor_view import render_grouped_frame

    frame["selected_groups"] = ["usage", "context"]
    for group in frame["selected_groups"]:
        frame["groups"][group]["data"] = {"sessions": [
            {"peer_id": f"worker{i}"} for i in range(3)
        ]}
    rendered = render_grouped_frame(frame, width=100, height=6, unicode=False, expand=True)
    assert "worker1" in rendered
    assert "+4 rows hidden" in rendered or "+4r" in rendered
    assert "--expand" in rendered
    assert len(rendered.splitlines()) <= 5






def test_soonest_exhaust_header_says_exhausted_when_pool_is_empty():
    from peerhub.cli.monitor_view import _quota_label
    group = {"data": {"observations": [{"state": "MEASURED", "peer_id": "cx", "pool_ref": "quota:cx:X-5H",
                                        "projection": {"exhaustion_at": 100.0},
                                        "measurements": {"remaining_fraction": 0.0}}]}}
    label = _quota_label("OK", group, 100.0, 0.0)
    assert "cx X-5H 0% exhausted" in label and " in 0m" not in label


@pytest.mark.parametrize("rem,elapsed,expected", [(0.25, 0, "past forecast"),
                                                  (0.25, 60, "past forecast"),
                                                  (0.0, 0, "exhausted")])
def test_elapsed_forecast_is_not_measured_exhaustion(frame, rem, elapsed, expected):
    from peerhub.cli.monitor_view import render_grouped_frame

    frame["selected_groups"] = ["capacity"]
    frame["display_elapsed_seconds"] = elapsed
    item = frame["groups"]["capacity"]["data"]["observations"][0]
    item["measurements"]["remaining_fraction"] = rem
    item["projection"] = {"state": "threat", "exhaustion_at": 999.0}
    rendered = render_grouped_frame(frame, width=160, unicode=False)
    assert expected in next(line for line in rendered.splitlines() if "soonest exhaust:" in line)
    header = next(line for line in rendered.splitlines() if "EXHAUST-IN" in line)
    row = next(line for line in rendered.splitlines()[2:] if "5h" in line)
    assert row[header.index("EXHAUST-IN"):header.index("PACE")].strip() == expected


def _frame(remaining, credits=None):
    obs = [{"peer_id": "ag", "pool_ref": "quota:ag:G-7D", "state": "MEASURED",
            "measurements": {"remaining_fraction": remaining, "reset_in_seconds": 7200},
            "projection": {"state": "threat", "exhaustion_at": 1500.0}, "age_seconds": 30.0}]
    data = {"observations": obs}
    if credits:
        data["reset_credits"] = credits
    return {"status": "OK", "read_at": 1000.0, "selected_groups": ["capacity"],
            "groups": {"capacity": {"status": "OK", "data": data}}}


@pytest.mark.parametrize("remaining, shown", [(0.000001, "<1%"), (0.0049, "<1%"),
                                             (0.005, "<1%"), (0.005000001, "1%"),
                                             (0.0, "0%"), (0.01, "1%"), (1.0, "100%")])
def test_less_than_one_percent_is_never_shown_as_zero(remaining, shown):
    from peerhub.cli.monitor_view import render_grouped_frame
    rows = [ln for ln in render_grouped_frame(_frame(remaining), color=False, width=100, height=24).splitlines() if ln.startswith("ag ")]
    assert rows[0].split()[2] == shown
    assert f"ag G-7D {shown} " in render_grouped_frame(_frame(remaining), color=False, width=100, height=24).splitlines()[1]


@pytest.mark.parametrize("remaining,shown", [(0.0, "0%"), (0.000001, "<1%"), (0.0049, "<1%"),
                                           (0.005, "<1%"), (0.005000001, "1%"), (1.0, "100%")])
@pytest.mark.parametrize("width", [40, 80, 120])
@pytest.mark.parametrize("unicode_mode", [False, True])
def test_peer_and_capacity_remainders_agree_without_changing_raw_frame(remaining, shown, width, unicode_mode):
    import copy
    from peerhub.cli.monitor_view import _group_lines

    frame = _frame(remaining)
    peer_group = {"data": {"peers": [{"peer_id": "ag", "quota": {
        "state": "MEASURED", "remaining_fraction": remaining, "percent": round(remaining * 100),
    }}]}}
    frame["groups"]["peers"] = peer_group
    before = copy.deepcopy(frame)
    for group_id in ("peers", "capacity"):
        lines = _group_lines(group_id, frame["groups"][group_id], width=width, unicode=unicode_mode)
        assert shown in "\n".join(lines)
    assert frame == before


def test_reset_credits_line_uses_relative_time():
    from peerhub.cli.monitor_view import render_grouped_frame
    credit = {"peer_id": "cx", "available_count": 2, "nearest_expires_at": 1000.0 + 8 * 86400 + 22 * 3600, "state": "MEASURED"}
    out = render_grouped_frame(_frame(0.5, [credit]), color=False, width=100, height=24)
    assert "cx reset credits: 2 | soonest in 8d22h [MEASURED]" in out


@pytest.mark.parametrize("used,expected_state,alert", [(0.10, "on_pace", False), (0.60, "threat", True),
                                                       (1.0, "exhausted", True)])
def test_exhaustion_alert_requires_within_window_risk(used, expected_state, alert):
    import copy
    from peerhub.cli.monitor_view import render_grouped_frame
    from peerhub.extensions.quota_projection import project_exhaustion

    projection = project_exhaustion(used_fraction=used, window_start=0, resets_at=1000,
                                    measured_at=500, now=500, evidence_state="MEASURED")
    assert projection["state"] == expected_state
    frame = _frame(1 - used)
    frame["read_at"] = 500
    item = frame["groups"]["capacity"]["data"]["observations"][0]
    item["projection"] = projection
    item["measurements"]["resets_at"] = 1000
    before = copy.deepcopy(frame)
    rendered = render_grouped_frame(frame, width=160, unicode=False)
    assert ("soonest exhaust:" in rendered) == alert
    header = next(line for line in rendered.splitlines() if "EXHAUST-IN" in line)
    row = next(line for line in rendered.splitlines() if line.startswith("ag "))
    cell = row[header.index("EXHAUST-IN"):header.index("PACE")].strip()
    if not alert:
        assert cell == "-"
        assert projection["exhaustion_at"] == 5000
        assert projection["projected_at_reset"] == 20
    elif used == 1:
        assert cell == "exhausted"
    else:
        assert cell not in ("-", "exhausted")
    assert frame == before
