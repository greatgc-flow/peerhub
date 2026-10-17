"""Human grouped monitor presentation stays readable and faithful to the read model."""

from __future__ import annotations

import re

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.diag]


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
        assert group_id.upper() in rendered
    assert "Codex" in rendered
    assert "cx.standard" in rendered
    assert "75%" in rendered
    assert "SAFE" in rendered and "age 30s" in rendered
    assert "█" in rendered and "░" in rendered
    assert "delivery" in rendered.lower() and "terminal" in rendered.lower()
    assert "task success" not in rendered.lower()
    assert not re.search(r"^\s*[\[{]", rendered, re.MULTILINE)
    assert '"peer_id"' not in rendered


def test_plain_grouped_view_is_aligned_ascii_without_terminal_controls(frame):
    from peerhub.cli.monitor_view import render_grouped_frame

    rendered = render_grouped_frame(frame, view="plain", color=False, unicode=False, width=80)

    assert "OVERVIEW" in rendered and "CAPACITY" in rendered
    assert "#" in rendered and "." in rendered
    assert "\x1b" not in rendered and "\r" not in rendered
    rendered.encode("ascii")
    assert '"peer_id"' not in rendered


@pytest.mark.parametrize("width", [20, 40, 60, 80, 120, 200])
def test_compact_width_keeps_every_selected_group_visible(frame, width):
    from peerhub.cli.monitor_view import render_grouped_frame

    rendered = render_grouped_frame(frame, view="plain", color=False, unicode=False, width=width)

    assert all(group.upper() in rendered for group in frame["selected_groups"])
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
    assert all(group.upper() in rendered for group in frame["selected_groups"])
    assert all(_width(line) <= width for line in rendered.splitlines())
    if width >= 48:
        assert all(re.sub(r"\x1b\[[0-9;]*m", "", line).endswith("╮")
                   for line in rendered.splitlines() if "╭─ " in line)


def test_colored_heading_keeps_ansi_after_width_fitting(frame):
    from peerhub.cli.monitor_view import render_grouped_frame

    rendered = render_grouped_frame(frame, view="rich", color=True, unicode=True, width=80)
    assert "\x1b[1;36m╭─ OVERVIEW" in rendered
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
        assert "+9" in rendered and "--group all" in rendered
    elif height >= 3 and len(frame["selected_groups"]) > max(0, height - 3):
        hidden = max(0, len(frame["selected_groups"]) - max(0, (height - 3) // 2))
        assert f"+{hidden}" in rendered or f"{hidden} groups omitted" in rendered
        assert "--group all" in rendered


def test_height_budget_prioritizes_errors_and_preserves_catalog_order(frame):
    from peerhub.cli.monitor_view import render_grouped_frame

    frame["groups"]["coverage"]["status"] = "ERROR"
    frame["groups"]["execution"]["status"] = "PARTIAL"
    rendered = render_grouped_frame(frame, view="plain", unicode=False, width=80, height=7)
    visible = rendered.upper()

    assert visible.index("COVERAGE [ERROR]") < visible.index("EXECUTION [PARTIAL]")
    assert "7 GROUPS OMITTED" in visible


def test_full_severity_classes_keep_catalog_order_when_every_summary_fits(frame):
    from peerhub.cli.monitor_view import render_grouped_frame

    statuses = {"overview": "ERROR", "sessions": "UNAVAILABLE", "usage": "STALE",
                "context": "PARTIAL", "runtime": "UNKNOWN", "execution": "OK",
                "capacity": "OK", "coverage": "OK", "store": "OK"}
    for group_id, status in statuses.items():
        frame["groups"][group_id]["status"] = status

    rendered = render_grouped_frame(frame, view="plain", unicode=False, width=100, height=20)
    headings = [line.split(" [", 1)[0] for line in rendered.splitlines() if " [" in line]

    assert headings == [group.upper() for group in frame["selected_groups"]]


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

    assert len(rendered.splitlines()) > 20
    assert all(group.upper() in rendered for group in frame["selected_groups"])
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

    # Default (expand=False): 2-row cap + "+N not shown"
    default_rendered = render_grouped_frame(frame, view="plain", unicode=False, width=80)
    assert "+2 sessions not shown" in default_rendered
    assert "+2 quota observations not shown" in default_rendered
    assert "+2 runtime sessions not shown" in default_rendered
    assert "sess_test_1" not in default_rendered

    # Expanded (expand=True): ALL 4 rows rendered, no "+N not shown", session handle present
    expanded_rendered = render_grouped_frame(frame, view="plain", unicode=False, width=80, expand=True)
    assert "not shown" not in expanded_rendered
    assert "sess_test_1" in expanded_rendered
    assert "sess_test_4" in expanded_rendered
    # Widths must be <= width - 1 (79 columns)
    for line in expanded_rendered.splitlines():
        assert _width(line) <= 79, f"Line exceeds width - 1: {line}"


def test_default_output_byte_identical_when_expand_is_false(frame):
    from peerhub.cli.monitor_view import render_grouped_frame

    out_implicit = render_grouped_frame(frame, view="plain", unicode=False, width=80)
    out_explicit = render_grouped_frame(frame, view="plain", unicode=False, width=80, expand=False)
    assert out_implicit == out_explicit

    out_implicit_rich = render_grouped_frame(frame, view="rich", unicode=True, width=100)
    out_explicit_rich = render_grouped_frame(frame, view="rich", unicode=True, width=100, expand=False)
    assert out_implicit_rich == out_explicit_rich



