"""Presentation-only quota trees, traffic lights and persistent ASCII selection."""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
import re
from typing import Any

import pytest

from peerhub._console import ANSI_ESCAPE
from peerhub.cli import monitor_format, monitor_layout, monitor_links, monitor_view as mv
from peerhub.cli.monitor import register_monitor_parser, validate_monitor_args
from peerhub.core.quota_policy import LOW_QUOTA_FRACTION


def frame_for(members: dict[str, list[str]]) -> dict[str, Any]:
    pools = sorted({pool for names in members.values() for pool in names})
    return {"status": "OK", "read_at": 1000, "selected_groups": ["capacity"],
            "configured_profiles": [{"profile": name, "fields": {"pools": {"value": names}}}
                                    for name, names in members.items()],
            "groups": {"capacity": {"status": "OK", "data": {"observations": [
                {"peer_id": "cc", "pool_ref": f"quota:cc:{pool}", "state": "MEASURED",
                 "age_seconds": 10, "ttl_seconds": 300,
                 "measurements": {"remaining_fraction": (i + 1) / 10, "reset_in_seconds": 9000},
                 "projection": {"state": "on_pace", "pace_pt": -2.5}}
                for i, pool in enumerate(pools)]}}}}


def pool_lines(screen: str) -> list[str]:
    """The pool lines of a frame; in an --expand frame the QUOTA details (below the compact POOLS block, #412)."""
    lines = screen.splitlines()
    start = next((i for i, line in enumerate(lines) if "QUOTA [" in line), 0)
    return [line for line in lines[start:] if re.search(r"^\s*(?:\w+ +)?(?:[└├]─ |[`|]- )?(?:C|F|X)-\d+[HD] {2,}", ANSI_ESCAPE.sub("", line))
            and not line.lstrip().startswith(("Columns", "STATE", "cc STALE"))]


def _view(expand: bool) -> str:
    """The view of a pin that does not name one: the grouped --expand layout (the profile tree, the 8-cell bar) is the
    plain view's; a compact frame keeps the default rich view. The rich --expand frame starts with the compact POOLS
    block and has its own pins here and in test_monitor_expand_rich.py."""
    return "plain" if expand else "rich"


def detail_lines(screen: str) -> list[str]:
    """The pool lines of the QUOTA details of a rich --expand frame (the lines below its `QUOTA [` rule)."""
    lines = screen.splitlines()
    start = next(i for i, line in enumerate(lines) if "QUOTA [" in line)
    return pool_lines("\n".join(lines[start + 1:]))


# #374 F5: the profile-containment tree, folded n/a suffixes and blank lines between peers stay in --expand. The compact
# POOLS shows a 5-hour row under its 7-day pool in compact mode when it binds (test_monitor_activity_view.py).
# The QUOTA tree pins below run for --expand only.
EXPAND_ONLY = pytest.mark.parametrize("expand", [True])


@EXPAND_ONLY
@pytest.mark.parametrize("unicode", [False, True])
def test_equal_sets_three_windows_and_equal_window_containment(expand: bool, unicode: bool) -> None:
    frame = frame_for({"cc.effort": ["C-5H", "C-7D", "C-30D"],
                       "cc.fable": ["C-5H", "C-7D", "C-30D", "F-7D"]})
    screen = mv.render_grouped_frame(frame, view="plain", unicode=unicode, width=180, expand=expand)
    rows = pool_lines(screen)
    assert len(rows) == 4
    assert [re.search(r"[CF]-\d+[HD]", row)[0] for row in rows] == ["C-30D", "C-7D", "F-7D", "C-5H"]  # type: ignore[index]
    tee, end = ("├─", "└─") if unicode else ("|-", "`-")
    assert end + " C-7D" in rows[1] and "   " + tee + " F-7D" in rows[2]
    assert "   " + end + " C-5H" in rows[3]
    assert rows[0].startswith("cc ") and all(not row.startswith("cc ") for row in rows[1:])
    # Independent rule oracle: the shown F-7D parent must be C-7D, not its sibling C-5H.
    assert rows[2].index("F-7D") == rows[3].index("C-5H") > rows[1].index("C-7D")
    expected_parent = {"C-30D": None, "C-7D": "C-30D", "F-7D": "C-7D", "C-5H": "C-7D"}
    stack: list[str] = []
    root_column = rows[0].index("C-30D")
    for row in rows:
        match = re.search(r"[CF]-\d+[HD]", row)
        assert match is not None
        depth = (match.start() - root_column) // 3
        assert (stack[depth - 1] if depth else None) == expected_parent[match[0]]
        stack[depth:] = [match[0]]


@pytest.mark.parametrize("members,expected", [
    ({"cc.a": ["C-7D", "C-5H"], "cc.b": ["C-7D", "F-7D"], "cc.c": ["F-7D"]}, ["C-7D", "C-5H", "F-7D"]),
    ({"cc.a": ["C-7D", "C-5H"], "cc.b": ["C-30D", "C-5H"]}, ["C-30D", "C-7D", "C-5H"]),
])
def test_crossing_or_ambiguous_peer_is_flat(members: dict[str, list[str]], expected: list[str]) -> None:
    frame = frame_for(members)
    for expand in (True,):  # the tree is --expand only (#374 F5)
        screen = mv.render_grouped_frame(frame, view="plain", unicode=False, width=180, expand=expand)
        rows = pool_lines(screen)
        assert not any("|-" in row or "`-" in row for row in rows)
        assert [re.search(r"[CF]-\d+[HD]", row)[0] for row in rows] == expected  # type: ignore[index]


@pytest.mark.parametrize("expand", [False, True])
def test_equal_sets_equal_windows_are_roots_without_name_tiebreak(expand: bool) -> None:
    frame = frame_for({"cc.a": ["C-7D", "F-7D"]})
    rows = pool_lines(mv.render_grouped_frame(frame, view=_view(expand), unicode=False, expand=expand, width=180))
    assert len(rows) == 2 and all("|-" not in row and "`-" not in row for row in rows)


@EXPAND_ONLY
def test_tied_shortest_parents_make_the_whole_peer_flat(expand: bool) -> None:
    frame = frame_for({"cc.a": ["C-5H", "C-7D", "F-7D"]})
    rows = pool_lines(mv.render_grouped_frame(frame, view=_view(expand), unicode=False, expand=expand, width=180))
    assert len(rows) == 3 and all("|-" not in row and "`-" not in row for row in rows)


@EXPAND_ONLY
def test_minimal_profile_superset_wins_before_shorter_window(expand: bool) -> None:
    frame = frame_for({"cc.a": ["C-5H", "C-30D", "F-7D"], "cc.b": ["F-7D"]})
    rows = pool_lines(mv.render_grouped_frame(frame, view=_view(expand), unicode=False, expand=expand, width=180))
    assert [re.search(r"[CF]-\d+[HD]", row)[0] for row in rows] == ["C-30D", "C-5H", "F-7D"]  # type: ignore[index]
    assert "`- C-5H" in rows[1] and "`- F-7D" not in rows[2]


@EXPAND_ONLY
def test_g_flat_fallback_uses_shared_cells(expand: bool) -> None:
    frame = frame_for({"cc.a": ["C-7D", "C-5H"], "cc.b": ["C-7D", "F-7D"], "cc.c": ["F-7D"]})
    rows = pool_lines(mv.render_grouped_frame(frame, view="plain", unicode=False, width=180, expand=expand))
    # C1/G: the unanchored fixture has reserve and reset. M5 lists configured profiles.
    expected = [("C-7D", "ok", "20%", "claude: cc.a,cc.b"),
                ("C-5H", "low", "10%!", "claude: cc.a"),
                ("F-7D", "ok", "30%", "fable: cc.b,cc.c")]
    assert len(rows) == len(expected)
    for row, (pool, state, left, family) in zip(rows, expected):
        assert {pool, state, left, f"{monitor_format.missing_pool_fact(False)}/2h30m"} <= set(row.split()) and family in row


@pytest.mark.parametrize("expand", [False, True])
def test_tree_keeps_each_pools_own_capacity_and_encoding_width(expand: bool) -> None:
    frame = frame_for({"cc.a": ["C-5H", "C-7D", "C-30D"]})
    ascii_rows = pool_lines(mv.render_grouped_frame(frame, view="plain", unicode=False, width=180, expand=expand))
    rich_rows = pool_lines(mv.render_grouped_frame(frame, view=_view(expand), unicode=True, width=180, expand=expand))
    for pool, value in (("C-30D", "10%"), ("C-5H", "20%"), ("C-7D", "30%")):
        row = next(row for row in ascii_rows if pool in row)
        assert value in [cell.rstrip("!") for cell in row.split()]
        assert value in [cell.rstrip("!") for cell in next(row for row in rich_rows if pool in row).split()]
    # P21: the compact rich rows carry the state glyph and the bar, so the width parity of the two glyph sets is
    # checked inside one view (the plain --expand rows are the same with either glyph set).
    same_view = pool_lines(mv.render_grouped_frame(frame, view=_view(expand), unicode=False, width=180, expand=expand))
    assert [monitor_layout.cell_width(row) for row in rich_rows] == [monitor_layout.cell_width(row) for row in same_view]
    if expand:
        assert same_view == ascii_rows


@EXPAND_ONLY
def test_retired_pools_are_hidden_in_quota(expand: bool, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NO_COLOR", raising=False)
    frame = frame_for({"cc.a": ["C-5H", "C-7D", "C-30D"]})
    for item in frame["groups"]["capacity"]["data"]["observations"]:
        if not item["pool_ref"].endswith("C-7D"):
            item.update(state="ABSENT", reason="not reported by provider", display_retired=True, display_plan="pro")
    screen = mv.render_grouped_frame(frame, view="rich", color=True, width=180, expand=expand)
    assert all(name not in screen for name in ("C-5H", "C-30D", "not reported by plan"))


@EXPAND_ONLY
@pytest.mark.parametrize("width", [20, 40, 60, 80, 100])
def test_hidden_unavailable_children_preserve_quota_numbers(expand: bool, width: int) -> None:
    frame = frame_for({"cc.a": ["C-7D", "C-5H"], "cc.b": ["C-7D", "C-30D"]})
    # Disjoint child sets; both have the one inclusion-minimal parent C-7D.
    frame["configured_profiles"][1]["fields"]["pools"]["value"][-1] = "F-7D"
    observations = frame["groups"]["capacity"]["data"]["observations"]
    for item in observations:
        if item["pool_ref"].endswith("C-30D"):
            item["pool_ref"] = "quota:cc:F-7D"
        if not item["pool_ref"].endswith("C-7D"):
            item.update(state="ABSENT", reason="not reported by provider", measurements=None)
    before = json.dumps(frame, sort_keys=True)
    screen = mv.render_grouped_frame(frame, view="plain", unicode=False, width=width, expand=expand)
    assert "C-5H" not in screen and "F-7D" not in screen
    assert "30%" in screen if width >= 60 else True  # narrower expanded tables may drop REM whole
    assert all(monitor_layout.cell_width(line) < width for line in screen.splitlines())
    assert json.dumps(frame, sort_keys=True) == before


@EXPAND_ONLY
@pytest.mark.parametrize("with_peers", [False, True])
def test_peer_spacing_yields_before_data_and_labels_only_first_row(expand: bool, with_peers: bool) -> None:
    frame = frame_for({"cc.a": ["C-5H", "C-7D"]})
    rows = frame["groups"]["capacity"]["data"]["observations"]
    rows.extend([{**item, "peer_id": "cx", "pool_ref": item["pool_ref"].replace("cc:C", "cx:X")} for item in rows[:]])
    frame["configured_profiles"].append({"profile": "cx.a", "fields": {"pools": {"value": ["X-5H", "X-7D"]}}})
    if with_peers:
        frame["selected_groups"].append("peers")
        frame["groups"]["peers"] = {"status": "OK", "data": {"peers": [
            {"peer_id": peer} for peer in ("cc", "cx")]}}
    # #412: the blank line between peers belongs to the QUOTA details; POOLS (the compact block above) has none, and a
    # bounded height cuts the frame from the bottom, never a separator out of the middle of a table
    full = mv.render_grouped_frame(frame, view="plain", unicode=False, width=100, height=200, expand=expand)
    lines = [line for line in full.splitlines() if not line.startswith(f"{monitor_links.GUIDE_TEXT}: ")]
    cx_index = next(i for i, row in enumerate(lines) if row.startswith("cx "))
    assert lines[cx_index - 1] == "" and "\n\n" not in "\n".join(lines[:next(i for i, l in enumerate(lines) if "QUOTA [" in l)])
    for height in range(len(lines) + 2, 3, -1):
        screen = mv.render_grouped_frame(frame, view="plain", unicode=False, width=100, height=height, expand=expand)
        assert len(screen.splitlines()) < height
        if 5 + monitor_layout.FRAME_TRAILING_ROWS < height <= len(lines):
            assert "rows hidden" in screen
        kept = [line for line in screen.splitlines() if not line.startswith(("+", "Coverage:", f"{monitor_links.GUIDE_TEXT}: "))]
        original = iter(lines)
        assert all(line in original for line in kept if line)  # optional top rows can shrink


@pytest.mark.parametrize("reason,detail", [
    ("disabled by provider", "disabled by provider"),
    ("not reported by provider; plan=pro", "not reported (plan pro)"),
])
def test_expanded_unavailable_child_is_hidden(reason: str, detail: str) -> None:
    frame = frame_for({"cc.a": ["C-7D", "C-5H"], "cc.b": ["C-7D", "C-5H", "F-7D"]})
    item = next(item for item in frame["groups"]["capacity"]["data"]["observations"]
                if item["pool_ref"].endswith("F-7D"))
    item.update(state="ABSENT", reason=reason, measurements=None)
    screen = mv.render_grouped_frame(frame, view="plain", unicode=False, width=180, expand=True)
    assert "F-7D" not in screen and detail not in screen


@pytest.mark.parametrize("state", ["mystery", "UNAVAILABLE"])
def test_c1_unmeasured_zero_uses_unknown_state_and_dim_colour(state: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NO_COLOR", raising=False)
    frame = frame_for({"cc.a": ["C-5H"]})
    frame["groups"]["capacity"]["data"]["observations"][0].update(
        state=state, last_measurements={"remaining_fraction": 0})
    screen = mv.render_grouped_frame(frame, view="rich", color=True, width=180, expand=True)
    assert screen.count(f"\x1b[{monitor_layout.RICH_SGR['dim']}m○ ?\x1b[0m") == 2


@pytest.mark.parametrize("expand", [False, True])
def test_c1_explicit_stale_without_ttl_keeps_the_stale_reserve(expand: bool, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NO_COLOR", raising=False)
    frame = frame_for({"cc.a": ["C-5H"]})
    item = frame["groups"]["capacity"]["data"]["observations"][0]
    item.update(state="STALE", ttl_seconds=None, last_measurements={"remaining_fraction": 0})
    screen = mv.render_grouped_frame(frame, view="rich", color=True, width=180, expand=expand)
    assert f"\x1b[{monitor_layout.RICH_SGR['out']}m" not in screen
    assert screen.count(f"\x1b[{monitor_layout.RICH_SGR['dim']}m○ aged\x1b[0m") == (2 if expand else 1)
    if expand:
        assert "cc STALE:window; next: peerhub observation refresh --peers cc" in screen


@pytest.mark.parametrize("reset,elapsed", [(0, 0), (10, 10)])
def test_measurement_at_reset_time_colour_matches_between_views(
        reset: int, elapsed: int, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NO_COLOR", raising=False)
    frame = frame_for({"cc.a": ["C-5H", "C-7D"]})
    item = frame["groups"]["capacity"]["data"]["observations"][0]
    item["measurements"]["reset_in_seconds"] = reset
    frame["display_elapsed_seconds"] = elapsed
    cell = f"\x1b[{monitor_layout.RICH_SGR['dim']}mreset · refresh pending\x1b[0m"
    for expand in (False, True):
        screen = mv.render_grouped_frame(frame, view="rich", color=True, width=180, expand=expand)
        assert screen.count(cell) == screen.count("C-5H") == 1


@pytest.mark.parametrize("expand", [False, True])
def test_unavailable_root_has_no_marker(expand: bool, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NO_COLOR", raising=False)
    frame = frame_for({"cc.a": ["C-5H"]})
    frame["groups"]["capacity"]["data"]["observations"][0].update(
        state="ABSENT", reason="not reported by provider", measurements=None)
    screen = mv.render_grouped_frame(frame, view="rich", color=True, width=180, expand=expand)
    assert "POOLS 0" in ANSI_ESCAPE.sub("", screen) and "C-5H" not in screen


def parse_monitor(argv: list[str]) -> tuple[argparse.ArgumentParser, argparse.Namespace]:
    parser = argparse.ArgumentParser()
    register_monitor_parser(parser.add_subparsers(dest="command"))
    return parser, parser.parse_args(["monitor", *argv])


@pytest.mark.parametrize("view", ["auto", "rich", "plain"])
def test_persistent_view_default_and_explicit_override(view: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PEERHUB_MONITOR_VIEW", view)
    parser, args = parse_monitor([])
    validate_monitor_args(parser, args)
    assert args.view == view
    parser, args = parse_monitor(["--view", "rich"])
    validate_monitor_args(parser, args)
    assert args.view == "rich"


def test_bad_view_env_is_usage_error_naming_variable(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setenv("PEERHUB_MONITOR_VIEW", "broken")
    with pytest.raises(SystemExit) as exc:
        parser, args = parse_monitor([])
        validate_monitor_args(parser, args)
    assert exc.value.code == 2 and "PEERHUB_MONITOR_VIEW" in capsys.readouterr().err
    parser, args = parse_monitor(["--view", "plain"])
    validate_monitor_args(parser, args)
    assert args.view == "plain"


def test_bad_explicit_view_is_choice_error_naming_option(
        monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setenv("PEERHUB_MONITOR_VIEW", "broken")
    with pytest.raises(SystemExit) as exc:
        parse_monitor(["--view", "bogus"])
    error = capsys.readouterr().err
    assert exc.value.code == 2
    assert "argument --view: invalid choice: 'bogus'" in error
    assert "PEERHUB_MONITOR_VIEW" not in error


@pytest.mark.parametrize("expand", [False, True])
def test_tree_height_sweep_keeps_bounds_and_never_shows_child_without_parent(expand: bool) -> None:
    frame = frame_for({"cc.a": ["C-30D", "C-7D", "C-5H"]})
    before = deepcopy(frame)
    for width in (20, 40, 60, 80, 100):
        for height in range(2, 35):
            screen = mv.render_grouped_frame(frame, view="plain", unicode=False, width=width, height=height, expand=expand)
            assert len(screen.splitlines()) < height
            assert all(monitor_layout.cell_width(line) < width for line in screen.splitlines())
            pools, _, quota = screen.partition("QUOTA [")
            assert "C-7D" not in quota or "C-30D" in quota
            assert "C-5H" not in pools or "C-7D" in pools
            assert "C-5H" not in quota or "C-7D" in quota
    assert frame == before


@EXPAND_ONLY
def test_duplicate_pool_names_keep_each_observations_capacity(expand: bool, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NO_COLOR", raising=False)
    frame = frame_for({"cc.a": ["C-7D", "C-5H"]})
    observations = frame["groups"]["capacity"]["data"]["observations"]
    original = next(item for item in observations if item["pool_ref"].endswith("C-7D"))
    original["measurements"]["remaining_fraction"] = .05
    observations.append({**deepcopy(original), "pool_ref": "other:cc:C-7D",
                         "measurements": {"remaining_fraction": .87, "reset_in_seconds": 3000}})
    # Skipped probe rows must not shift the source association of table rows.
    observations.insert(0, {"peer_id": "cc", "pool_ref": "quota", "state": "ERROR"})
    before = deepcopy(frame)
    screen = mv.render_grouped_frame(frame, view="plain", unicode=False, width=180, expand=expand)
    rows = pool_lines(screen)
    assert len(rows) == 3
    assert "`- C-5H" in rows[1]
    first, second = [row for row in rows if "C-7D" in row]
    assert "5%" in first and "87%" in second
    assert frame == before
    compact = mv.render_grouped_frame(frame, view="plain", unicode=False, width=180)  # each keeps its own value
    assert "5%" in compact and "87%" in compact and "10%" in compact
    rich = mv.render_grouped_frame(frame, view="rich", color=True, width=180, expand=expand)
    quota_rows = [row for row in detail_lines(rich) if "C-7D" in row]
    assert len(quota_rows) == 2
    for row, state, left, colour in zip(quota_rows, ("▼ low", "● ok"), ("5%", "87%"), ("warn", "ok")):
        assert re.search(rf"{state}.*{left}", ANSI_ESCAPE.sub("", row))
        assert f"\x1b[{monitor_layout.RICH_SGR[colour]}m{state}\x1b[0m" in row
    assert frame == before


def test_compact_pools_have_no_blank_lines_between_peers() -> None:
    frame = frame_for({"cc.a": ["C-5H", "C-7D"]})
    rows = frame["groups"]["capacity"]["data"]["observations"]
    rows.extend([{**item, "peer_id": "cx", "pool_ref": item["pool_ref"].replace("cc:C", "cx:X")} for item in rows[:]])
    screen = mv.render_grouped_frame(frame, view="plain", unicode=False, width=100)
    assert "\n\n" not in screen and all(pool in screen for pool in ("C-7D", "C-5H", "X-7D", "X-5H"))


@pytest.mark.parametrize("expand", [False, True])
def test_error_evidence_keeps_alarm_colour(expand: bool, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NO_COLOR", raising=False)
    frame = frame_for({"cc.a": ["C-5H"]})
    frame["groups"]["capacity"]["data"]["observations"][0].update(state="ERROR", measurements=None)
    screen = mv.render_grouped_frame(frame, view="rich", color=True, width=180, expand=expand)
    cell = f"\x1b[{monitor_layout.RICH_SGR['bad']}m○ ?\x1b[0m"  # P21: the palette's bad, in POOLS and in the QUOTA details
    assert screen.count(cell) == (2 if expand else 1)
    if expand:  # the compact state word hides the evidence state: a note names it
        assert "cc C-5H evidence ERROR; next: peerhub observation refresh --peers cc" in ANSI_ESCAPE.sub("", screen)
        plain = mv.render_grouped_frame(frame, view="plain", color=True, unicode=False, width=180, expand=True)
        assert "\x1b" not in plain
        assert "cc C-5H evidence ERROR" in plain  # C1 keeps the evidence in the QUOTA notes.


@pytest.mark.parametrize("remaining,low", [(.05, True), (LOW_QUOTA_FRACTION, True), (LOW_QUOTA_FRACTION + .01, False)])
def test_m1_remaining_fraction_marks_reserve_at_the_shared_threshold(remaining: float, low: bool) -> None:
    frame = frame_for({"cc.a": ["C-5H"]})
    item = frame["groups"]["capacity"]["data"]["observations"][0]
    item["measurements"]["remaining_fraction"] = remaining
    screen = mv.render_grouped_frame(frame, view="plain", width=180, expand=True)
    assert all((f"{remaining:.0%}" + ("!" if low else "")) in row.split() for row in pool_lines(screen))


def test_empty_view_env_defaults_to_auto(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PEERHUB_MONITOR_VIEW", "")
    parser, args = parse_monitor([])
    validate_monitor_args(parser, args)
    assert args.view == "auto"


@pytest.mark.parametrize("expand", [False, True])
@pytest.mark.parametrize("unicode", [False, True])
def test_deep_tree_two_column_fallback_stays_in_public_viewport(expand: bool, unicode: bool) -> None:
    frame = frame_for({"cc.a": [f"C-{days}D" for days in range(1, 9)]})
    for height in (None, 40):
        for view in ("plain", "rich"):
            screen = mv.render_grouped_frame(frame, view=view, unicode=unicode, width=20, height=height, expand=expand)
            assert all(monitor_layout.cell_width(line) < 20 for line in screen.splitlines()), (view, height)


@pytest.mark.parametrize("width", [82, 83])
@pytest.mark.parametrize("unicode", [False, True])
def test_expanded_separators_show_at_exact_full_fit_and_yield_below_it(width: int, unicode: bool) -> None:
    frame = frame_for({"cc.a": ["C-5H", "C-7D"]})
    observations = frame["groups"]["capacity"]["data"]["observations"]
    observations.extend({**item, "peer_id": "cx", "pool_ref": item["pool_ref"].replace("cc:C", "cx:X")}
                        for item in observations[:])
    frame["configured_profiles"].append({"profile": "cx.a", "fields": {"pools": {"value": ["X-5H", "X-7D"]}}})
    frame["selected_groups"].append("peers")
    frame["groups"]["peers"] = {"status": "OK", "data": {"peers": [{"peer_id": p} for p in ("cc", "cx")]}}
    before = deepcopy(frame)
    roomy = mv.render_grouped_frame(frame, view="plain", unicode=unicode, width=width, height=200, expand=True)
    assert "\n\n" in roomy and "rows hidden" not in roomy
    data_lines = [line for line in roomy.splitlines() if not line.startswith(f"{monitor_links.GUIDE_TEXT}: ")]
    exact_height = len(data_lines) + monitor_layout.FRAME_TRAILING_ROWS
    exact = mv.render_grouped_frame(frame, view="plain", unicode=unicode, width=width, height=exact_height, expand=True)
    assert exact.splitlines() == data_lines
    for height in range(exact_height - 2, exact_height):  # below it the bottom goes, with a count (#412)
        screen = mv.render_grouped_frame(frame, view="plain", unicode=unicode, width=width, height=height, expand=True)
        assert len(screen.splitlines()) < height and "rows hidden" in screen
        assert "\n\n" in screen  # the separators of the QUOTA details stay
    assert frame == before


@pytest.mark.parametrize("height", [1, 2, 3])
def test_tiny_heights_keep_the_verdict_and_title_when_they_fit(height: int) -> None:
    frame = frame_for({"cc.deepthink": ["C-5H", "C-7D"], "cc.fable": ["F-7D", "C-7D"]})
    screen = mv.render_grouped_frame(frame, view="plain", unicode=False, width=100, height=height, expand=True)
    lines = screen.splitlines()
    assert len(lines) < max(height, 2)  # the cursor line stays free; height 1 renders nothing rather than overflow
    if height == 2:  # #374: --expand starts with the verdict line; one line holds only that
        assert lines == ["OK? (alerts not read)"]
    if height >= 3:
        assert lines[1].startswith("PeerHub Monitor")
