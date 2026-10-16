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
