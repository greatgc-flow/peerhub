"""Human grouped monitor presentation stays readable and faithful to the read model."""

from __future__ import annotations

from datetime import datetime, timezone
import re
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.diag]
# #374: frames from the read model always carry alert_sources; without them --expand adds `source alerts: not read`
READ_SOURCES = {"activity": "read", "capacity": "read", "accounts": "read", "facts": "read"}


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
    # C1: supply the anchors behind P-3H's forecast so both modes run the shared calculation.
    at = 970
    start = at - 1830 * (.99 / .01)
    observations[2]["observed_at"] = datetime.fromtimestamp(at, timezone.utc).isoformat()
    observations[2]["measurements"].update(window_started_at=start, resets_at=8200)
    return {"status": "OK", "read_at": 1000.0, "selected_groups": ["capacity"], "alert_sources": dict(READ_SOURCES),
            "groups": {"capacity": {"status": "OK", "data": {"observations": observations}}}}


@pytest.mark.parametrize("color", [False, True])
def test_quota_visual_snapshot_at_120_columns(quota_visual_frame, color, monkeypatch):
    from peerhub.cli.monitor_layout import cell_width
    from peerhub.cli.monitor_legend import POOL_LEGEND
    from peerhub.cli.monitor_expanded import render_expanded_frame as render_grouped_frame

    monkeypatch.delenv("NO_COLOR", raising=False)
    # The grouped layout is the plain view's and is never painted, whatever the colour flag (the rich --expand frame
    # and its palette are pinned in test_monitor_expand_rich.py).
    # C1, M3–M5 and G: both tables share the state, bar, signed pace and duration pair.
    # M9/G8 keep the shared gap and legend with the POOLS block.
    rendered = render_grouped_frame(quota_visual_frame, view="plain", color=color, width=120, height=60)
    snapshot = Path(__file__).with_name("fixtures") / "quota_s2_color_off.txt"
    assert rendered == snapshot.read_text(encoding="utf-8")
    assert all(cell_width(line) <= 119 for line in rendered.splitlines())
    assert rendered.count("cc PROBE:") == 1
    assert len(re.findall(r"(?m)^ag .*STALE:window; next: peerhub observation refresh --peers ag", rendered)) == 1
    assert "soonest exhaust: ag P-4H 0% exhausted" in rendered  # C1: a fresh zero is already out.
    assert "duplicate" not in rendered
    assert POOL_LEGEND in " ".join(rendered.split())
    assert "\x1b" not in rendered


def test_quota_color_suppression_preserves_identical_text(quota_visual_frame, monkeypatch):
    from peerhub.cli.monitor_expanded import render_expanded_frame as render_grouped_frame

    monkeypatch.delenv("NO_COLOR", raising=False)
    options = {"width": 80, "height": 24, "unicode": False}
    off = render_grouped_frame(quota_visual_frame, view="plain", color=False, **options)
    assert off.splitlines()[0] == "OK" and "\x1b" not in off
    assert render_grouped_frame(quota_visual_frame, view="plain", color=True, **options) == off
    # The rich view: colour changes no text but the pill's padding, and NO_COLOR (even empty) removes every SGR code.
    bare_rich = render_grouped_frame(quota_visual_frame, view="rich", color=False, **options)
    on = render_grouped_frame(quota_visual_frame, view="rich", color=True, **options)
    bare = re.sub(r"\x1b\[[0-9;]*m", "", on)
    assert "\x1b[" in on and bare.splitlines()[1:] == bare_rich.splitlines()[1:]
    assert bare.splitlines()[0].split() == bare_rich.splitlines()[0].split() == ["OK", "00:16Z"]
    assert bare.splitlines()[0].startswith(" OK ")  # P21: line 1 is a pill, with its padding
    monkeypatch.setenv("NO_COLOR", "")
    assert render_grouped_frame(quota_visual_frame, view="rich", color=True, **options) == bare_rich


@pytest.mark.parametrize("unicode_mode", [False, True])
def test_quota_bar_boundaries_and_monotonic_fill(unicode_mode):
    from peerhub.cli.monitor_format import _bar_quota

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
    from peerhub.cli.monitor_expanded import render_expanded_frame as render_grouped_frame

    observations = quota_visual_frame["groups"]["capacity"]["data"]["observations"]
    observations[3]["state"] = "STALE"  # C1: the stale zero cannot win the caption.
    quota_visual_frame["display_elapsed_seconds"] = 60
    rendered = render_grouped_frame(quota_visual_frame, view="plain", width=80, height=60)
    assert rendered.splitlines()[0].startswith("OK")  # #374: line 1 is the verdict, then the expand header
    assert rendered.splitlines()[1].startswith("PeerHub Monitor")
    assert "soonest exhaust: ag P-3H 1% in 29m" in render_grouped_frame(quota_visual_frame, view="plain", width=120)
    rich = render_grouped_frame(quota_visual_frame, view="rich", width=120)  # the same label in the rich QUOTA rule
    assert "── QUOTA [OK] · measured remaining · soonest exhaust: ag P-3H 1% in 29m ─" in rich
    assert "P-1H" in rendered and "P-6H" in rendered


@pytest.mark.parametrize(("state", "seconds", "expected"), [
    ("MEASURED", 17880, "4h58m"), ("MEASURED", None, None),
    ("STALE", 17880, "4h58m"), ("UNKNOWN", 17880, "4h58m"),  # C1/G3: reset is independent
])
@pytest.mark.parametrize("elapsed", [0, 60])
def test_quota_reset_countdown_plain_and_tty_parity(frame, state, seconds, expected, elapsed):
    from peerhub.cli.monitor_expanded import render_expanded_frame as render_grouped_frame
    from peerhub.cli.monitor_format import missing_pool_fact

    frame["selected_groups"] = ["capacity"]
    item = frame["groups"]["capacity"]["data"]["observations"][0]
    item.update(state=state, reset_in_seconds=seconds)
    frame["display_elapsed_seconds"] = elapsed
    if expected == "4h58m" and elapsed:
        expected = "4h57m"
    for view, unicode_mode in [("plain", False), ("plain", True), ("rich", True)]:
        rendered = render_grouped_frame(frame, view=view, unicode=unicode_mode,
                                        width=120, height=24)
        lines = rendered.splitlines()
        quota = lines[next(i for i, line in enumerate(lines) if "PEER POOL" in line):]
        row = next(line for line in quota if "cx 5h" in line)
        marker = missing_pool_fact(unicode_mode)
        assert f"{marker}/{marker if expected is None else expected}" in row  # G3


@pytest.fixture
def frame():
    return {
        "status": "PARTIAL",
        "read_at": 1000.0,
        "alert_sources": dict(READ_SOURCES),  # the read model always reports them (#374)
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
    from peerhub.cli.monitor_expanded import render_expanded_frame as render_grouped_frame

    rendered = render_grouped_frame(frame, view="rich", color=False, unicode=True, width=100)

    for group_id in (g for g in frame["selected_groups"] if g not in {"overview", "coverage"}):
        group_label = "QUOTA" if group_id == "capacity" else group_id.upper()
        assert group_label in rendered
    assert "OVERVIEW [" not in rendered
    assert "cx.standard" in rendered
    assert "75%" in rendered
    assert "PACE(pp)" in rendered.split("QUOTA [")[1] and "30s" in rendered  # C1/G
    assert "SAFE" not in rendered and "[█" not in rendered  # no upper-case state and no 8-cell bar in the rich view
    assert "█" in rendered and "░" in rendered
    assert "delivery" in rendered.lower() and "terminal" in rendered.lower()
    assert "task success" not in rendered.lower()
    assert not re.search(r"^\s*[\[{]", rendered, re.MULTILINE)
    assert '"peer_id"' not in rendered


def test_plain_grouped_view_is_aligned_ascii_without_terminal_controls(frame):
    from peerhub.cli.monitor_expanded import render_expanded_frame as render_grouped_frame

    rendered = render_grouped_frame(frame, view="plain", color=False, unicode=False, width=80)

    assert "OVERVIEW" not in rendered and "QUOTA" in rendered
    assert "#" in rendered and "." in rendered
    assert "\x1b" not in rendered and "\r" not in rendered
    rendered.encode("ascii")
    assert '"peer_id"' not in rendered


@pytest.mark.parametrize("width", [20, 40, 60, 80, 120, 200])
def test_compact_width_keeps_every_selected_group_visible(frame, width):
    from peerhub.cli.monitor_expanded import render_expanded_frame as render_grouped_frame

    rendered = render_grouped_frame(frame, view="plain", color=False, unicode=False, width=width)

    assert all(group.upper() in rendered or (group == "capacity" and "QUOTA" in rendered) for group in frame["selected_groups"] if group not in {"overview", "coverage"})
    assert "coverage" in rendered.lower()
    assert all(len(line) <= width for line in rendered.splitlines())


def test_grouped_view_reports_stale_unknown_and_partial_states_explicitly(frame):
    from peerhub.cli.monitor_expanded import render_expanded_frame as render_grouped_frame

    frame["groups"]["context"]["data"]["sessions"][0]["context"].update(
        percent=None, state="UNKNOWN", reason="no_context_measurement")
    frame["groups"]["capacity"]["status"] = "PARTIAL"
    frame["groups"]["capacity"]["coverage"] = {
        "read": "partial", "sample_size": 101, "truncated": True, "reason": "sample_cap"}

    rendered = render_grouped_frame(frame, view="plain", color=False, unicode=False, width=100)

    assert "CONTEXT [" not in rendered
    assert "no_context_measurement" not in rendered
    assert "PARTIAL" in rendered
    assert "Coverage: partial" in rendered


def test_coverage_group_does_not_call_empty_scope_set_complete(frame):
    from peerhub.cli.monitor_expanded import render_expanded_frame as render_grouped_frame

    frame["groups"]["coverage"]["data"]["scopes"] = []
    rendered = render_grouped_frame(frame, view="plain", unicode=False)
    assert "No scope evidence" in rendered
    assert "Scopes complete: 0/0" not in rendered


def test_human_renderer_is_isolated_from_schema_11_adapter():
    import ast
    from pathlib import Path

    cli = Path(__file__).resolve().parents[3] / "peerhub" / "cli"
    renderer = ("monitor_view", "monitor_compact", "monitor_expanded", "monitor_sections",
                "monitor_activity_view", "monitor_running_view", "monitor_recent_view", "monitor_leads_view",
                "monitor_alert_view", "monitor_quota_view")
    for name in renderer:
        tree = ast.parse((cli / f"{name}.py").read_text(encoding="utf-8"))
        imported = [node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
        imported.extend(alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names)
        assert all("monitor_schema11" not in module for module in imported), name


@pytest.mark.parametrize("width", [32, 48, 80, 120])
def test_rich_frame_respects_terminal_width_without_losing_groups(frame, width):
    from peerhub.cli.monitor_layout import cell_width
    from peerhub.cli.monitor_expanded import render_expanded_frame as render_grouped_frame

    rendered = render_grouped_frame(frame, view="rich", color=True, unicode=True, width=width)
    assert all(group.upper() in rendered or (group == "capacity" and "QUOTA" in rendered) for group in frame["selected_groups"] if group not in {"overview", "coverage"})
    assert all(cell_width(line) <= width for line in rendered.splitlines())
    if width >= 48:  # P21: the rule's line and its title are separate spans
        assert any(re.sub(r"\x1b\[[0-9;]*m", "", line).startswith("── ") for line in rendered.splitlines())


def test_colored_heading_keeps_ansi_after_width_fitting(frame, monkeypatch):
    from peerhub.cli.monitor_layout import RICH_SGR
    from peerhub.cli.monitor_expanded import render_expanded_frame as render_grouped_frame

    monkeypatch.delenv("NO_COLOR", raising=False)
    rendered = render_grouped_frame(frame, view="rich", color=True, unicode=True, width=80)
    dim, title = RICH_SGR["dim"], RICH_SGR["title"]  # P21: a dim line and a bright title, as in the compact frame
    assert f"\x1b[{dim}m──\x1b[0m \x1b[{title}mEXECUTION [OK]\x1b[0m \x1b[{dim}m─" in rendered
    assert "EXECUTION [OK]" in rendered
    assert "\x1b[1;36m" not in rendered


@pytest.mark.parametrize("width", [15, 20, 40, 80, 120, 200])
@pytest.mark.parametrize("height", [1, 2, 3, 4, 5, 8, 24, 60])
def test_height_aware_frame_stays_inside_viewport_and_discloses_overflow(frame, width, height):
    from peerhub.cli.monitor_expanded import render_expanded_frame as render_grouped_frame

    rendered = render_grouped_frame(frame, view="plain", unicode=False, width=width, height=height)

    assert len(rendered.splitlines()) <= height - 1
    assert all(len(line) <= width - 1 for line in rendered.splitlines())
    if height == 2:  # #374: one line holds only line 1, the verdict
        assert rendered.startswith("OK") and len(rendered.splitlines()) == 1
    elif height == 3:
        assert rendered.splitlines()[1].startswith("PeerHub")
    elif height == 5:
        assert ("POOLS 1" in rendered) == (width >= 40)
        assert "gap vs " not in rendered
    elif height >= 4 and len(frame["selected_groups"]) > max(0, height - 3):
        assert "omitted" in rendered or "hidden" in rendered or any(f"+{i}" in rendered for i in range(1, 10))
        if width >= 64 and height > 4:
            assert any(f"peerhub monitor --snapshot --group {group} --expand" in rendered
                       for group in ("capacity", "all"))
        else:
            assert "--expand" in rendered


def test_a_bounded_height_cuts_the_sections_from_the_bottom_up_in_catalog_order(frame):
    """#412: the sections go from the bottom up with a count (the old severity allocation is gone); a tall frame has them
    all, in the one order."""
    from peerhub.cli.monitor_expanded import render_expanded_frame as render_grouped_frame

    frame["selected_groups"] = ["overview", "execution", "coverage", "store"]
    frame["groups"]["store"]["status"] = "ERROR"
    frame["groups"]["execution"]["status"] = "PARTIAL"
    tall = render_grouped_frame(frame, view="plain", unicode=False, width=80, height=60)
    assert tall.upper().index("EXECUTION [PARTIAL]") < tall.upper().index("STORE [ERROR]")
    rendered = render_grouped_frame(frame, view="plain", unicode=False, width=80, height=7)
    assert len(rendered.splitlines()) <= 6 and "rows hidden" in rendered
    assert "OVERVIEW [" not in rendered.upper() and "COVERAGE [" not in rendered.upper()


def test_full_severity_classes_keep_catalog_order_when_every_summary_fits(frame):
    from peerhub.cli.monitor_expanded import render_expanded_frame as render_grouped_frame

    statuses = {"overview": "ERROR", "sessions": "UNAVAILABLE", "usage": "STALE",
                "context": "PARTIAL", "runtime": "UNKNOWN", "execution": "OK",
                "capacity": "OK", "coverage": "OK", "store": "OK"}
    for group_id, status in statuses.items():
        frame["groups"][group_id]["status"] = status

    rendered = render_grouped_frame(frame, view="plain", unicode=False, width=100, height=28)
    headings = [line.split(" [", 1)[0].lstrip("-─ ").strip() for line in rendered.splitlines() if line.startswith(("-- ", "── ")) and " [" in line]

    assert headings == ["QUOTA", "EXECUTION", "SESSIONS", "USAGE", "CONTEXT", "RUNTIME", "STORE"]


def test_store_provenance_and_safe_read_error_are_visible(frame):
    from peerhub.cli.monitor_expanded import render_expanded_frame as render_grouped_frame

    frame["store_selection"] = {"path": "D:/users/alice/private/core.db", "source": "discovered"}
    frame["errors"] = {"sessions": "OperationalError: secret-token SELECT * FROM private"}
    frame["groups"]["sessions"]["status"] = "ERROR"
    rendered = render_grouped_frame(frame, view="plain", unicode=False, width=100, height=60)

    assert "discovered" in rendered
    assert "core.db" in rendered  # #412: the store line is near the end of the frame
    assert "OperationalError" in rendered or "database read failed" in rendered.lower()
    assert "secret-token" not in rendered and "SELECT *" not in rendered


def test_narrow_store_header_retains_source_and_wraps_path(frame):
    from peerhub.cli.monitor_expanded import render_expanded_frame as render_grouped_frame

    frame["store_selection"] = {"path": "D:/a/very/long/location/core.db", "source": "workspace"}
    rendered = render_grouped_frame(frame, view="plain", unicode=False, width=40, height=80)

    assert "workspace" in rendered
    assert "D:/a/very/long/location/core.db" in rendered  # #338: wrapped, never elided
    assert all(len(line) <= 40 for line in rendered.splitlines())


def test_dependent_error_groups_show_safe_root_reason(frame):
    from peerhub.cli.monitor_expanded import render_expanded_frame as render_grouped_frame

    frame["groups"]["sessions"]["status"] = "ERROR"
    frame["errors"] = {"execution": "OperationalError: SELECT secret from user"}
    rendered = render_grouped_frame(frame, view="plain", unicode=False, width=100, height=24)

    assert "operationalerror: database read failed" in rendered.lower()
    assert "SELECT secret" not in rendered


def test_display_width_ignores_combining_and_joiner_and_sanitizes_bidi(frame):
    from peerhub.cli.monitor_layout import cell_width
    from peerhub.cli.monitor_expanded import render_expanded_frame as render_grouped_frame

    rendered = render_grouped_frame(frame, view="plain", unicode=True, width=20, height=8)
    assert cell_width("e\u0301") == 1
    assert cell_width("👩\u200d💻") <= 4
    assert "\u202e" not in rendered


def test_cjk_combining_and_joined_emoji_fit_actual_viewport(frame):
    from peerhub.cli.monitor_layout import cell_width
    from peerhub.cli.monitor_expanded import render_expanded_frame as render_grouped_frame

    frame["selected_groups"] = ["sessions"]
    frame["groups"]["sessions"]["data"]["items"][0]["state"] = "\u202e\u200b東京 cafe\u0301 👩\u200d💻\u0378"
    rendered = render_grouped_frame(frame, view="rich", unicode=True, width=20, height=8)

    assert all(cell_width(line) <= 19 for line in rendered.splitlines())
    assert "\u202e" not in rendered
    assert "�" in rendered


def test_unicode_line_separators_cannot_inflate_physical_frame_height(frame):
    from peerhub.cli.monitor_expanded import render_expanded_frame as render_grouped_frame

    frame["groups"]["overview"]["data"]["peers"][0]["display_name"] = "A\u2028B\u2029C"
    rendered = render_grouped_frame(frame, view="plain", unicode=True, width=80, height=8)
    physical_lines = rendered.rstrip("\n").split("\n")

    assert len(physical_lines) <= 7
    assert "\u2028" not in rendered and "\u2029" not in rendered


def test_refresh_warning_reason_is_bounded_and_sanitized(frame):
    from peerhub.cli.monitor_expanded import render_expanded_frame as render_grouped_frame

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
    from peerhub.cli.monitor_expanded import render_expanded_frame as render_grouped_frame

    frame["refresh_state"] = "STOPPED · AGY TOKEN USE"
    rendered = render_grouped_frame(frame, view="plain", unicode=False, width=100, height=24)

    assert "--allow-agy-token-use" in rendered


def test_append_only_renderer_keeps_full_legacy_human_frame(frame):
    from peerhub.cli.monitor_expanded import render_expanded_frame as render_grouped_frame

    rendered = render_grouped_frame(frame, view="plain", unicode=False, width=80)

    assert len(rendered.splitlines()) > 10
    assert all(group.upper() in rendered or (group == "capacity" and "QUOTA" in rendered) for group in frame["selected_groups"] if group not in {"overview", "coverage"})
    assert "D:/" not in rendered


def test_plain_ascii_monitor_replaces_middle_dot_cleanly_without_question_marks(frame):
    from peerhub.cli.monitor_expanded import render_expanded_frame as render_grouped_frame

    frame["refresh_state"] = "REFRESH PARTIAL · 1 warnings"
    rendered = render_grouped_frame(frame, view="plain", unicode=False, width=100, height=80)

    assert "·" not in rendered
    # Delimiters should be ASCII | rather than replacing · with ?
    assert " | " in rendered
    assert "REFRESH PARTIAL | 1 warnings" in rendered  # the title of the compact frame keeps the live state as it is
    assert "1.50s avg | 2 samples" in rendered
    assert "Schema 7 | database" in rendered
    # Ensure no ? is produced from middle dot replacement
    for line in rendered.splitlines():
        if "cx 5h" not in line:  # G/M5: an unknown family is a real fact.
            assert " ? " not in line


def test_expand_renders_all_loaded_detail_rows_and_session_handle(frame):
    from peerhub.cli.monitor_layout import cell_width
    from peerhub.cli.monitor_expanded import render_expanded_frame as render_grouped_frame

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
        assert cell_width(line) <= 79, f"Line exceeds width - 1: {line}"


def test_expanded_sessions_show_uses_after_gen_and_compact_view_does_not(frame):
    from peerhub.cli.monitor_expanded import render_expanded_frame as render_grouped_frame

    frame["groups"]["sessions"]["data"]["items"] = [
        {"peer_id": "cx", "state": "ACTIVE", "generation": 1, "resumable": True, "handle": "sess_a", "uses": 3,
         "configured_binding": {"profile": "cx.standard", "model": "gpt-test", "effort": "medium"}},
        {"peer_id": "cx", "state": "ACTIVE", "generation": 2, "resumable": True, "handle": "sess_b", "uses": None,
         "configured_binding": {"profile": "cx.standard", "model": "gpt-test", "effort": "medium"}},
    ]
    expanded = render_grouped_frame(frame, view="plain", unicode=False, width=120, expand=True)
    assert "sess_a | gen 1 | uses 3 | cx.standard" in expanded
    assert "sess_b | gen 2 | uses ? | cx.standard" in expanded
    compact = render_grouped_frame(frame, view="plain", unicode=False, width=120)
    assert "uses" not in compact


def test_narrow_sessions_drop_trailing_fields_whole_for_every_row(frame):
    from peerhub.cli.monitor_expanded import render_expanded_frame as render_grouped_frame

    frame["groups"]["sessions"]["data"]["items"] = [
        {"peer_id": "cx", "state": "ACTIVE", "generation": n, "resumable": True, "handle": f"sess_{n}", "uses": n,
         "configured_binding": {"profile": "cx.standard", "model": "gpt-test", "effort": "medium"}}
        for n in (1, 2)]
    full = render_grouped_frame(frame, view="plain", unicode=False, width=120, expand=True)
    assert "sess_1 | gen 1 | uses 1 | cx.standard / gpt-test (medium) | resumable" in full
    assert "Columns omitted" not in full
    narrow = render_grouped_frame(frame, view="plain", unicode=False, width=80, expand=True)
    rows = [line for line in narrow.splitlines() if "sess_" in line]
    assert len(rows) == 2 and "~" not in narrow
    assert all(line.endswith("cx.standard / gpt-test (medium)") for line in rows)
    assert "resumable" not in narrow.split("SESSIONS [")[1].split("Columns omitted:")[0]
    assert "Columns omitted: resumable" in narrow


def test_limited_height_drops_other_groups_before_clipping_quota(frame):
    """When terminal height is limited, other groups are dropped before clipping quota rows."""
    from peerhub.cli.monitor_expanded import render_expanded_frame as render_grouped_frame

    frame["groups"]["capacity"]["data"]["observations"] = [
        {"peer_id": "cx", "pool_ref": f"quota:cx:{i}h", "state": "MEASURED",
         "measurements": {"remaining_fraction": 0.2 * i},
         "projection": {"state": "safe", "pace_pt": 5.0}, "age_seconds": 10.0}
        for i in range(1, 5)
    ]
    # Height 14 is too tight for every section: the top (line 1, the title, POOLS) stays whole, the rest is counted (#412)
    rendered = render_grouped_frame(frame, view="plain", unicode=False, width=80, height=14)
    assert "rows hidden" in rendered
    for i in range(1, 5):  # all 4 pools are retained unclipped
        assert f"{i}h" in rendered
    assert len(rendered.splitlines()) <= 13


def test_default_output_byte_identical_when_expand_is_false(frame):
    from peerhub.cli.monitor_expanded import render_expanded_frame as render_grouped_frame

    out_implicit = render_grouped_frame(frame, view="plain", unicode=False, width=80)
    out_explicit = render_grouped_frame(frame, view="plain", unicode=False, width=80, expand=False)
    assert out_implicit == out_explicit

    out_implicit_rich = render_grouped_frame(frame, view="rich", unicode=True, width=100)
    out_explicit_rich = render_grouped_frame(frame, view="rich", unicode=True, width=100, expand=False)
    assert out_implicit_rich == out_explicit_rich


def test_detail_loop_stopping_early_emits_hidden_by_terminal_height(frame):
    """When a detail loop stops early due to viewport height, emit '+N rows hidden by terminal height'."""
    from peerhub.cli.monitor_expanded import render_expanded_frame as render_grouped_frame

    frame["selected_groups"] = ["capacity"]
    frame["groups"]["capacity"]["data"]["observations"] = [
        {"peer_id": "cx", "pool_ref": f"quota:cx:{i}h", "state": "MEASURED",
         "measurements": {"remaining_fraction": 0.1 * i},
         "projection": {"state": "safe", "pace_pt": 5.0}, "age_seconds": 10.0}
        for i in range(1, 7)
    ]
    # Height 7: verdict + title + the POOLS header, a row or two, and the count of what is hidden (#412)
    rendered = render_grouped_frame(frame, view="plain", unicode=False, width=80, height=7)
    assert "rows hidden" in rendered and "--expand" in rendered
    assert rendered.splitlines()[0].startswith("OK") and "POOLS" in rendered
    assert len(rendered.splitlines()) <= 6


def test_warning_and_next_stop_overhead_accounted_for_without_clipping_quota(frame):
    """lines_used accounts for warning and Next: stop lines so quota is not clipped while other groups are dropped."""
    from peerhub.cli.monitor_expanded import render_expanded_frame as render_grouped_frame

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
    from peerhub.cli.monitor_expanded import render_expanded_frame as render_grouped_frame

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

    rendered = render_grouped_frame(frame, view="plain", unicode=False, width=120, height=80)
    cap_idx = rendered.index("QUOTA [OK]")
    first_pool_idx = rendered.index("g-5h", cap_idx)  # POOLS above names the pools too
    second_pool_idx = rendered.index("5h", cap_idx)
    runtime_idx = rendered.index("RUNTIME [OK]")
    store_idx = rendered.index("STORE [OK]")

    # Contiguous inside QUOTA before RUNTIME and STORE
    assert cap_idx < first_pool_idx < second_pool_idx < runtime_idx < store_idx
    # 100% remaining has full # bar; 0% remaining has empty . bar
    assert "100%" in rendered and "0%" in rendered
    assert "FAMILY" in rendered  # M5
    assert "2h05m" in rendered
    assert "30m" in rendered
    assert "cx provider-reported tokens: 1,250 | sessions 1 with/0 without usage | cache 80%" in rendered


def test_monitor_capacity_renders_argon_a_7d_pool_row_like_other_pools(frame):
    """#149: A-7D pool row renders in QUOTA like other pools without special casing."""
    from peerhub.cli.monitor_expanded import render_expanded_frame as render_grouped_frame

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
    from peerhub.cli.monitor_layout import cell_width
    from peerhub.cli.monitor_expanded import render_expanded_frame as render_grouped_frame

    monkeypatch.delenv("NO_COLOR", raising=False)
    t0 = 1760790000.0  # 2025-10-18T12:20:00Z
    fixture = {
        "status": "OK",
        "read_at": t0,
        "alert_sources": dict(READ_SOURCES),
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
        for view, unicode_mode in [("plain", True), ("plain", False)]:  # the grouped layout is the plain view's
            rendered = render_grouped_frame(fixture, view=view, color=color, unicode=unicode_mode, width=width, height=height)
            assert "\x1b" not in rendered
            lines = rendered.rstrip("\n").split("\n")
            assert len(lines) <= height - 1, f"Height exceeded: {len(lines)} > {height - 1} at {width}x{height} {view}"
            for line in lines:
                assert cell_width(re.sub(r"\x1b\[[0-9;]*m", "", line)) <= width - 1, f"Line width exceeded at {width}x{height}: {line}"

            # #412: line 1, the title and POOLS (the quota) are always at the top, whatever the height
            assert lines[0].startswith("OK") and lines[1].startswith("PeerHub Monitor")
            assert any(line.startswith("POOLS ") for line in lines[:4])
            assert rendered.count("Coverage:") <= 1

            if height >= 40:  # the QUOTA details fit: whole columns go from the right and are named
                assert "QUOTA [OK]" in rendered
                if width >= 120:
                    assert "EXH/RESET" in rendered and "FAMILY" in rendered  # G4/M5
                else:
                    assert "PACE(pp)" in rendered  # G4

            if (width, height) == (80, 12):
                assert "rows hidden" in rendered
                assert "--expand" in rendered
                assert "STORE [" not in rendered

            if not unicode_mode:
                assert "·" not in rendered
                assert "█" not in rendered
                rendered.encode("ascii")


@pytest.mark.parametrize("width", [1, 2, 10, 19, 20, 50, 60, 80, 100, 120])
@pytest.mark.parametrize("expand", [False, True])
@pytest.mark.parametrize("unicode_mode", [False, True])
@pytest.mark.parametrize("view", ["plain", "rich"])
def test_append_frame_leaves_terminal_last_column_free(frame, width, expand, unicode_mode, view):
    from peerhub.cli.monitor_layout import cell_width
    from peerhub.cli.monitor_expanded import render_expanded_frame as render_grouped_frame

    rendered = render_grouped_frame(frame, view=view, width=width, unicode=unicode_mode,
                                    color=unicode_mode, expand=expand)
    assert all(cell_width(line) <= width - 1 for line in rendered.splitlines())  # #412: both views fit every line


@pytest.mark.parametrize("width", [50, 60])
@pytest.mark.parametrize("height", [None, 24])
def test_narrow_quota_keeps_leftmost_columns_and_names_the_dropped_ones(frame, width, height):
    from peerhub.cli.monitor_expanded import render_expanded_frame as render_grouped_frame

    frame["selected_groups"] = ["capacity"]
    item = frame["groups"]["capacity"]["data"]["observations"][0]
    item.update(pool_ref="quota:cx:X-7D", projection={"state": "exhausted"})
    item["measurements"]["reset_in_seconds"] = 90000
    rendered = render_grouped_frame(frame, view="plain", width=width, height=height, unicode=False)
    header = next(line for line in rendered.splitlines() if "PEER" in line)
    assert header.split()[:5] == ["PEER", "POOL", "STATE", "LEFT", "PACE(pp)"]  # G4
    if "Columns omitted:" in rendered:
        omitted = rendered.split("Columns omitted:", 1)[1].splitlines()[0]
        assert "pace" not in omitted and "state" not in omitted and "left" not in omitted


def test_g_expand_quota_bar_tick_and_painted_pace_preserve_plain_facts(monkeypatch):
    from peerhub.cli import monitor_format, monitor_layout, monitor_view as mv
    from tests.communication.diag.test_monitor_pool_health import frame, pool_cells, reading

    monkeypatch.delenv("NO_COLOR", raising=False)
    payload = frame(reading("ag", "G-7D", .50, 12.5))
    plain = mv.render_grouped_frame(payload, view="plain", color=True, unicode=False, width=120, expand=True)
    compact, quota = pool_cells(plain, "G-7D")
    assert compact == quota[:-1]
    bar = compact[1]
    assert monitor_format.BAR_CELLS <= len(bar) <= monitor_format.BAR_MAX_CELLS
    assert bar == monitor_format._bar_quota(.5, len(bar), unicode=False, even_burn=.625)[1:-1]
    assert (compact[0], *compact[2:4]) == ("ok", "50%", "+12.50")
    assert "\x1b" not in plain
    rich = mv.render_grouped_frame(payload, view="rich", color=True, width=120, expand=True)
    assert rich.count(f"\x1b[{monitor_layout.RICH_SGR['ok']}m+12.50\x1b[0m") == 2  # M1
    monkeypatch.setenv("NO_COLOR", "1")
    assert "\x1b" not in mv.render_grouped_frame(payload, view="rich", color=True, width=120, expand=True)


@pytest.mark.parametrize("height", [7, 9])
def test_a_bounded_frame_counts_exactly_the_lines_it_hides(frame, height):
    """#412 (the per-section allocation of the old plain frame is gone): the count of the hint is the lines cut."""
    from peerhub.cli.monitor_expanded import render_expanded_frame as render_grouped_frame

    frame["selected_groups"] = ["usage"]
    frame["groups"]["usage"]["data"] = {"sessions": [
        {"peer_id": f"worker{i}", "telemetry": {"normalized": {"provider_total_tokens": i}}}
        for i in range(1, 6)
    ]}
    whole = render_grouped_frame(frame, view="plain", width=100, height=200, unicode=False, expand=True).splitlines()
    rendered = render_grouped_frame(frame, view="plain", width=100, height=height, unicode=False, expand=True).splitlines()
    hint = next(line for line in rendered if "rows hidden" in line)
    shown = rendered.index(hint)
    assert int(re.match(r"\+(\d+) rows hidden", hint)[1]) == len(whole) - 1 - shown  # the coverage line is not counted
    assert "--expand" in hint and len(rendered) <= height - 1






def test_c1_soonest_exhaust_header_says_exhausted_for_a_fresh_zero():
    from peerhub.cli.monitor_expanded import render_expanded_frame

    label = next(line for line in render_expanded_frame(_frame(0), view="plain", width=120).splitlines()
                 if "QUOTA [" in line)
    assert "ag G-7D 0% exhausted" in label


@pytest.mark.parametrize("rem,elapsed,expected", [(0.25, 0, "past forecast"),
                                                  (0.25, 60, "past forecast"),
                                                  (0.0, 0, "exhausted")])
def test_elapsed_forecast_is_not_measured_exhaustion(frame, rem, elapsed, expected):
    from peerhub.cli.monitor_expanded import render_expanded_frame as render_grouped_frame

    frame["selected_groups"] = ["capacity"]
    frame["display_elapsed_seconds"] = elapsed
    item = frame["groups"]["capacity"]["data"]["observations"][0]
    item["measurements"]["remaining_fraction"] = rem
    item["observed_at"] = datetime.fromtimestamp(970, timezone.utc).isoformat()
    item["measurements"].update(window_started_at=883, resets_at=1100)  # C1: a positive forecast passed at 999.
    rendered = render_grouped_frame(frame, view="plain", width=160, unicode=False)
    assert expected in next(line for line in rendered.splitlines() if "soonest exhaust:" in line)
    lines = rendered.splitlines()
    at = next(i for i, line in enumerate(lines) if "PEER POOL" in line)
    row = next(line for line in lines[at + 1:] if "cx 5h" in line)
    assert ("past est./" if rem else "OUT/") in row  # M4


def _frame(remaining, credits=None):
    obs = [{"peer_id": "ag", "pool_ref": "quota:ag:G-7D", "state": "MEASURED",
            "measurements": {"remaining_fraction": remaining, "reset_in_seconds": 7200},
            "projection": {"state": "threat", "exhaustion_at": 1500.0}, "age_seconds": 30.0}]
    data = {"observations": obs}
    if credits:
        data["reset_credits"] = credits
    return {"status": "OK", "read_at": 1000.0, "selected_groups": ["capacity"], "alert_sources": dict(READ_SOURCES),
            "groups": {"capacity": {"status": "OK", "data": data}}}


@pytest.mark.parametrize("remaining, shown", [(0.000001, "<1%"), (0.0049, "<1%"),
                                             (0.005, "<1%"), (0.005000001, "1%"),
                                             (0.0, "0%"), (0.01, "1%"), (1.0, "100%")])
def test_less_than_one_percent_is_never_shown_as_zero(remaining, shown):
    from peerhub.cli.monitor_expanded import render_expanded_frame as render_grouped_frame
    plain = render_grouped_frame(_frame(remaining), view="plain", width=100, height=24).splitlines()
    rows = [ln for ln in plain if ln.startswith("ag ")]  # the QUOTA row (a POOLS row starts with a space)
    assert shown in [cell.rstrip("!") for cell in rows[0].split()]  # G7
    rich = render_grouped_frame(_frame(remaining), view="rich", color=False, width=100, height=24).splitlines()
    pools, detail = [ln.split() for ln in rich if ln.startswith(" ag ")]
    assert pools[5].rstrip("!") == detail[5].rstrip("!") == shown


@pytest.mark.parametrize("remaining,shown", [(0.0, "0%"), (0.000001, "<1%"), (0.0049, "<1%"),
                                           (0.005, "<1%"), (0.005000001, "1%"), (1.0, "100%")])
@pytest.mark.parametrize("width", [40, 80, 120])
@pytest.mark.parametrize("unicode_mode", [False, True])
def test_peer_and_capacity_remainders_agree_without_changing_raw_frame(remaining, shown, width, unicode_mode):
    import copy
    from peerhub.cli.monitor_sections import group_lines

    frame = _frame(remaining)
    peer_group = {"data": {"peers": [{"peer_id": "ag", "quota": {
        "state": "MEASURED", "remaining_fraction": remaining, "percent": round(remaining * 100),
    }}]}}
    frame["groups"]["peers"] = peer_group
    before = copy.deepcopy(frame)
    for group_id in ("peers", "capacity"):
        lines, _ = group_lines(group_id, frame["groups"][group_id], width=width, unicode=unicode_mode)
        assert shown in "\n".join(lines)
    assert frame == before


def test_reset_credits_line_uses_relative_time():
    from peerhub.cli.monitor_expanded import render_expanded_frame as render_grouped_frame
    credit = {"peer_id": "cx", "available_count": 2, "nearest_expires_at": 1000.0 + 8 * 86400 + 22 * 3600, "state": "MEASURED"}
    out = render_grouped_frame(_frame(0.5, [credit]), view="plain", width=100, height=24)
    assert "cx reset credits: 2 | soonest in 8d22h [MEASURED]" in out
    rich = render_grouped_frame(_frame(0.5, [credit]), view="rich", color=False, width=100, height=24)
    assert " cx reset credits: 2 · soonest in 8d22h [MEASURED]" in rich.splitlines()


@pytest.mark.parametrize("used,expected_state,alert", [(0.10, "on_pace", False), (0.60, "threat", True),
                                                       (1.0, "exhausted", True)])
def test_exhaustion_alert_requires_within_window_risk(used, expected_state, alert):
    import copy
    from peerhub.cli.monitor_expanded import render_expanded_frame as render_grouped_frame
    from peerhub.extensions.quota_projection import project_exhaustion

    projection = project_exhaustion(used_fraction=used, window_start=0, resets_at=1000,
                                    measured_at=500, now=500, evidence_state="MEASURED")
    assert projection["state"] == expected_state
    frame = _frame(1 - used)
    frame["read_at"] = 500
    item = frame["groups"]["capacity"]["data"]["observations"][0]
    item["projection"] = projection
    item["observed_at"] = datetime.fromtimestamp(500, timezone.utc).isoformat()
    item["measurements"].update(window_started_at=0, resets_at=1000)  # C1
    before = copy.deepcopy(frame)
    rendered = render_grouped_frame(frame, view="plain", width=160, unicode=False)
    assert ("soonest exhaust:" in rendered) == alert
    row = next(line for line in rendered.splitlines() if line.startswith("ag G-7D"))
    pair = next(cell for cell in row.split() if "/" in cell)
    if used == 1:
        assert pair.startswith("OUT/")
    else:
        assert pair.endswith("!") == alert  # M4: compare the two durations, including a later forecast.
    assert frame == before


def test_unified_monitor_human_contract(frame):
    from peerhub.cli.monitor_expanded import render_expanded_frame as render_grouped_frame
    frame["store_selection"] = {"path": "/workspace/.peerhub/core.db", "source": "explicit"}
    for height in (None, 60):
        rendered = render_grouped_frame(frame, view="plain", width=180, height=height)
        assert "OVERVIEW [" not in rendered
        assert rendered.count("Coverage:") == 1
        assert "/workspace/.peerhub/core.db" in rendered
        assert "context: unknown" not in rendered


def test_capacity_short_codes_profiles_and_staleness(quota_visual_frame):
    from peerhub.cli.monitor_expanded import render_expanded_frame as render_grouped_frame
    from peerhub.cli.lessons import lookup_lessons
    quota_visual_frame["groups"]["peers"] = {"data": {"peers": [
        {"peer_id": "ag", "profile": {"name": "ag.argon", "pools": ["P-8H"]}}],
        "facts_report": {"status": "needs_recheck", "reasons": ["ag: version_drift", "cc: version_drift", "cx: version_drift"]}}}
    quota_visual_frame["selected_groups"].append("peers")
    quota_visual_frame["groups"]["capacity"]["data"]["observations"][-3]["age_seconds"] = 8 * 86400
    rich = render_grouped_frame(quota_visual_frame, view="rich", width=180)
    # C1/G: one state vocabulary and shared columns; evidence and drift still have detail notes.
    for code in ("STALE:window", "PROBE:8d", "DRIFT:ag,cc,cx", "peerhub explain <code>", "FAMILY"):
        assert code in rich and code in render_grouped_frame(quota_visual_frame, view="plain", width=180)
    assert "ag P-5H evidence ERROR; next: peerhub observation refresh --peers ag" in rich
    assert lookup_lessons("STALE:window")


def test_drift_footer_includes_unknown_pool_reason(quota_visual_frame):
    """Every facts_report reason is drift, including 'ag: unknown_pool' (no 'drift' substring)."""
    from peerhub.cli.monitor_expanded import render_expanded_frame as render_grouped_frame
    quota_visual_frame["groups"]["peers"] = {"data": {"peers": [],
        "facts_report": {"status": "needs_recheck", "reasons": ["ag: unknown_pool"]}}}
    quota_visual_frame["selected_groups"].append("peers")
    for view in ("plain", "rich"):
        lines = render_grouped_frame(quota_visual_frame, view=view, width=180).splitlines()
        footer = [line for line in lines if line.startswith("Coverage:")]
        assert len(footer) == 1 and "DRIFT:ag" in footer[0]  # the plain frame's `Guide:` line (#414) follows it


@pytest.mark.parametrize("height", [None, 40])
def test_stored_escape_sequences_never_reach_the_terminal_even_when_colour_is_on(quota_visual_frame, height, monkeypatch):
    from peerhub.cli.monitor_expanded import render_expanded_frame as render_grouped_frame

    monkeypatch.delenv("NO_COLOR", raising=False)
    quota_visual_frame["groups"]["peers"] = {"status": "OK", "data": {"peers": [],
        "facts_report": {"status": "needs_recheck", "reasons": ["cc: \x1b[31m\x1b]0;x\x07"]}}}
    quota_visual_frame["selected_groups"].append("peers")
    plain = render_grouped_frame(quota_visual_frame, view="plain", color=True, width=140, height=height)
    assert "\x1b" not in plain and "\x07" not in plain
    colored = render_grouped_frame(quota_visual_frame, view="rich", color=True, width=140, height=height)
    from peerhub.cli.monitor_layout import PILL_SGR, RICH_SGR
    assert f"\x1b[{RICH_SGR['ok']}m● ok\x1b[0m" in colored  # the palette still paints the quota cells
    ours = "|".join(re.escape(code) for code in ("0", *RICH_SGR.values(), *PILL_SGR.values()))  # one palette (P21)
    assert not re.sub(rf"\x1b\[(?:{ours})m", "", colored).count("\x1b") and "\x1b[1;36m" not in colored
    assert "�[31m�]0;x�" in colored if height is None else True
    assert "\x07" not in colored and "\x1b]" not in colored


NOT_REPORTED = "not reported by provider; plan=prolite"


def _cx_plan_frame(absent_state="ABSENT", peer_quota=None):
    quota = peer_quota or {"state": "MEASURED", "pool_ref": "quota:cx:X-7D", "remaining_fraction": 0.89,
                           "percent": 89, "age_seconds": 30.0, "scope": "mapped"}
    return {"status": "OK", "read_at": 1000.0, "selected_groups": ["capacity", "peers"], "groups": {
        "capacity": {"status": "OK", "data": {"observations": [
            {"peer_id": "cx", "pool_ref": "quota:cx:X-7D", "state": "MEASURED", "age_seconds": 30.0,
             "measurements": {"remaining_fraction": 0.89, "reset_in_seconds": 7200}, "projection": {"state": "safe"}},
            {"peer_id": "cx", "pool_ref": "quota:cx:X-5H", "state": absent_state, "stored_state": "ABSENT",
             "age_seconds": 30.0, "measurements": None, "reason": NOT_REPORTED},
        ]}},
        "peers": {"status": "OK", "data": {"peers": [{"peer_id": "cx", "quota": quota,
                                                      "profile": {"name": "cx.standard", "source": "packaged"}}]}},
    }}


def test_not_reported_pool_renders_na_with_plan_never_zero_or_incomplete():
    """X3/X5: a window the provider does not report is n/a (plan shown); the peer line keeps the reported pool's %."""
    from peerhub.cli.monitor_view import render_grouped_frame
    rendered = render_grouped_frame(_cx_plan_frame(), view="plain", unicode=False, width=120, expand=True, show_quota_notes=True)
    assert "cx X-5H n/a not reported (plan prolite)" in rendered
    row = next(line for line in rendered.splitlines() if line.startswith("cx") and " X-5H " in line and "PROFILES" not in line)
    assert "STALE" not in row and "ABSENT" not in row and "n/a" in row
    assert "quota: 89% (X-7D) [plan prolite: no 5h window]" in rendered
    assert "incomplete" not in rendered


def test_all_pools_not_reported_is_question_mark_never_full():
    from peerhub.cli.monitor_view import render_grouped_frame
    unknown = {"state": "UNKNOWN", "pool_ref": "not_reported", "remaining_fraction": None, "percent": None,
               "age_seconds": None, "scope": "mapped"}
    rendered = render_grouped_frame(_cx_plan_frame(peer_quota=unknown), view="plain", unicode=False, width=120, expand=True, show_quota_notes=True)
    assert "quota: ? (not reported)" in rendered
    assert "100%" not in rendered and "incomplete" not in rendered


def test_aged_not_reported_pool_is_question_mark_with_refresh_hint():
    from peerhub.cli.monitor_view import render_grouped_frame
    rendered = render_grouped_frame(_cx_plan_frame(absent_state="STALE"), view="plain", unicode=False, width=120, expand=True, show_quota_notes=True)
    assert "cx X-5H ? not reported (plan prolite), aged; next: peerhub observation refresh --peers cx" in rendered
    assert "STALE:window" not in rendered


def test_g_even_burn_ignores_a_zero_length_window():
    from peerhub.cli.monitor_view import render_grouped_frame
    from tests.communication.diag.test_monitor_pool_health import frame, pool_cells, reading

    item = reading("cx", "X-0H", .5, 0)
    item["payload"]["window_started_at"] = item["payload"]["resets_at"]
    rows = pool_cells(render_grouped_frame(frame(item), width=120, expand=True), "X-0H")
    assert len(rows) == 2 and all("|" not in row[1] for row in rows)
