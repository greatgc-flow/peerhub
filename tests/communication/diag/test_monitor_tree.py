"""Presentation-only quota trees, traffic lights and persistent ASCII selection."""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
import re
from typing import Any

import pytest

from peerhub.cli import monitor_view as mv
from peerhub.cli.monitor import register_monitor_parser, validate_monitor_args


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
    return [line for line in screen.splitlines() if re.search(r"\b(?:C|F|X)-\d+[HD]\b", line)
            and not line.lstrip().startswith(("Columns", "STATE", "cc STALE"))]


@pytest.mark.parametrize("expand", [False, True])
@pytest.mark.parametrize("unicode", [False, True])
def test_equal_sets_three_windows_and_equal_window_containment(expand: bool, unicode: bool) -> None:
    frame = frame_for({"cc.effort": ["C-5H", "C-7D", "C-30D"],
                       "cc.fable": ["C-5H", "C-7D", "C-30D", "F-7D"]})
    screen = mv.render_grouped_frame(frame, view="rich", unicode=unicode, width=180, expand=expand)
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


@pytest.mark.parametrize("members", [
    {"cc.a": ["C-7D", "C-5H"], "cc.b": ["C-7D", "F-7D"], "cc.c": ["F-7D"]},
    {"cc.a": ["C-7D", "C-5H"], "cc.b": ["C-30D", "C-5H"]},
])
def test_crossing_or_ambiguous_peer_is_flat(members: dict[str, list[str]]) -> None:
    frame = frame_for(members)
    for expand in (False, True):
        screen = mv.render_grouped_frame(frame, view="plain", unicode=False, width=180, expand=expand)
        rows = pool_lines(screen)
        assert not any("|-" in row or "`-" in row for row in rows)
        expected = sorted({pool for pools in members.values() for pool in pools}, key=lambda p: mv._pool_order("cc", p))
        assert [re.search(r"[CF]-\d+[HD]", row)[0] for row in rows] == expected  # type: ignore[index]


@pytest.mark.parametrize("expand", [False, True])
def test_equal_sets_equal_windows_are_roots_without_name_tiebreak(expand: bool) -> None:
    frame = frame_for({"cc.a": ["C-7D", "F-7D"]})
    rows = pool_lines(mv.render_grouped_frame(frame, unicode=False, expand=expand, width=180))
    assert len(rows) == 2 and all("|-" not in row and "`-" not in row for row in rows)


@pytest.mark.parametrize("expand", [False, True])
def test_tied_shortest_parents_make_the_whole_peer_flat(expand: bool) -> None:
    frame = frame_for({"cc.a": ["C-5H", "C-7D", "F-7D"]})
    rows = pool_lines(mv.render_grouped_frame(frame, unicode=False, expand=expand, width=180))
    assert len(rows) == 3 and all("|-" not in row and "`-" not in row for row in rows)


@pytest.mark.parametrize("expand", [False, True])
def test_minimal_profile_superset_wins_before_shorter_window(expand: bool) -> None:
    frame = frame_for({"cc.a": ["C-5H", "C-30D", "F-7D"], "cc.b": ["F-7D"]})
    rows = pool_lines(mv.render_grouped_frame(frame, unicode=False, expand=expand, width=180))
    assert [re.search(r"[CF]-\d+[HD]", row)[0] for row in rows] == ["C-30D", "C-5H", "F-7D"]  # type: ignore[index]
    assert "`- C-5H" in rows[1] and "`- F-7D" not in rows[2]


@pytest.mark.parametrize("expand", [False, True])
def test_flat_fallback_preserves_02410_cells(expand: bool) -> None:
    frame = frame_for({"cc.a": ["C-7D", "C-5H"], "cc.b": ["C-7D", "F-7D"], "cc.c": ["F-7D"]})
    rows = pool_lines(mv.render_grouped_frame(frame, view="plain", unicode=False, width=180, expand=expand))
    # Frozen 0.24.10 row cells, allowing the mandated first-row-only peer and
    # POOL padding and measurement-reset fallback changes. Capacities, pace
    # values and flat ordering stay unchanged.
    expected = (["C-5H low 10% 2h30m v-2 [#...|...] claude 10s a",
                 "C-7D ok 20% 2h30m v-2 [|#......] claude 10s a,b",
                 "F-7D ok 30% 2h30m v-2 [|#......] fable 10s b,c"] if not expand else
                ["C-5H ON_PACE 10% 2h30m v-2.5pp [#...|...] - 10s cc.a",
                 "C-7D ON_PACE 20% 2h30m v-2.5pp [|#......] - 10s cc.a,cc.b",
                 "F-7D ON_PACE 30% 2h30m v-2.5pp [|#......] - 10s cc.b,cc.c"])
    assert [" ".join(row.removeprefix("cc ").split()) for row in rows] == expected


@pytest.mark.parametrize("expand", [False, True])
def test_tree_keeps_each_pools_own_capacity_and_encoding_width(expand: bool) -> None:
    frame = frame_for({"cc.a": ["C-5H", "C-7D", "C-30D"]})
    ascii_rows = pool_lines(mv.render_grouped_frame(frame, view="plain", unicode=False, width=180, expand=expand))
    rich_rows = pool_lines(mv.render_grouped_frame(frame, unicode=True, width=180, expand=expand))
    for pool, value in (("C-30D", "10%"), ("C-5H", "20%"), ("C-7D", "30%")):
        row = next(row for row in ascii_rows if pool in row)
        assert value in row.split()
    assert [mv._width(row) for row in rich_rows] == [mv._width(row) for row in ascii_rows]


@pytest.mark.parametrize("expand", [False, True])
def test_retired_pools_form_one_dim_note_per_peer(expand: bool, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NO_COLOR", raising=False)
    frame = frame_for({"cc.a": ["C-5H", "C-7D", "C-30D"]})
    for item in frame["groups"]["capacity"]["data"]["observations"]:
        if not item["pool_ref"].endswith("C-7D"):
            item.update(state="ABSENT", reason="not reported by provider", display_retired=True, display_plan="pro")
    screen = mv.render_grouped_frame(frame, color=True, width=180, expand=expand)
    notes = [line for line in screen.splitlines() if "not reported by plan" in line]
    assert len(notes) == 1 and "C-5H,C-30D" in notes[0]
    assert notes[0].startswith("\x1b[2m") and notes[0].endswith("\x1b[0m")


@pytest.mark.parametrize("expand", [False, True])
@pytest.mark.parametrize("width", [20, 40, 60, 80, 100])
def test_folded_na_combines_suffix_after_last_column_without_losing_numbers(expand: bool, width: int) -> None:
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
    assert "(F-7D,C-5H n/a)" in screen
    assert "`- C-5H" not in screen and "`- F-7D" not in screen
    assert "30%" in screen if width >= 60 else True  # narrower expanded tables may drop REM whole
    assert all(mv._width(line) < width for line in screen.splitlines())
    assert json.dumps(frame, sort_keys=True) == before


@pytest.mark.parametrize("expand", [False, True])
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
    full = mv.render_grouped_frame(frame, view="plain", unicode=False, width=100, height=200, expand=expand)
    lines = full.splitlines()
    cx_index = next(i for i, row in enumerate(lines) if row.startswith("cx "))
    assert lines[cx_index - 1] == ""
    tight = mv.render_grouped_frame(frame, view="plain", unicode=False, width=100, height=len(lines) - 1, expand=expand)
    assert "\n\n" not in tight
    assert all(pool in tight for pool in ("C-7D", "C-5H", "X-7D", "X-5H"))
    assert sum(bool(re.match(r"^cc\s+[CFX]-\d+[HD]\b", line)) for line in pool_lines(tight)) == 1
    for width, height in [(w, h) for w in (81, 82, 83, 84, 100) for h in range(2, len(lines) + 3)]:
        screen = mv.render_grouped_frame(frame, view="plain", unicode=False, width=width, height=height, expand=expand)
        assert len(screen.splitlines()) < height
        if "\n\n" in screen:
            assert "rows hidden" not in screen
            assert all(pool in screen for pool in ("C-7D", "C-5H", "X-7D", "X-5H"))
            if with_peers and expand:
                assert screen.count("-> latest session:") == 2 and "Model facts:" in screen


@pytest.mark.parametrize("reason,detail", [
    ("disabled by provider", "disabled by provider"),
    ("not reported by provider; plan=pro", "not reported (plan pro)"),
])
def test_expanded_folded_pool_keeps_identity_and_reason(reason: str, detail: str) -> None:
    frame = frame_for({"cc.a": ["C-7D", "C-5H"], "cc.b": ["C-7D", "C-5H", "F-7D"]})
    item = next(item for item in frame["groups"]["capacity"]["data"]["observations"]
                if item["pool_ref"].endswith("F-7D"))
    item.update(state="ABSENT", reason=reason, measurements=None)
    screen = mv.render_grouped_frame(frame, view="plain", unicode=False, width=180, expand=True)
    assert "(F-7D n/a)" in screen
    assert f"cc F-7D n/a {detail}" in screen


@pytest.mark.parametrize("state,remaining,aged,expected", [
    ("OUT", .8, False, "1;31"), ("EXHAUSTED", .8, False, "1;31"),
    ("THREAT", 0, False, "1;31"), ("THREAT", .05, False, "31"),
    ("INSUFF", .10, False, "33"), ("FAST", .8, False, "33"),
    ("INSUFF", .8, False, None), ("OK", .8, False, "32"),
    ("ON_PACE", .8, False, "32"), ("SAFE", .8, False, "32"),
    ("OUT", 0, True, "2"), ("STALE", 0, False, "2"),
    ("AGED", 0, False, "2"), ("n/a", 0, False, "2"),
    ("UNKNOWN", 0, False, "2"), ("reset", 0, False, "2"),
])
def test_colour_precedence(state: str, remaining: float, aged: bool, expected: str | None) -> None:
    assert mv._quota_color(state, remaining, aged=aged) == expected


@pytest.mark.parametrize("remaining,projection,samples,aged,word,code", [
    (0, "threat", 2, False, "out", "1;31"),
    (.05, "threat", 2, False, "low", "31"),
    (.5, "threat", 2, False, "fast", "31"),
    (.5, "threat", 1, False, "ok", "31"),
    (.05, "insufficient", 2, False, "low", "33"),
    (.5, "watch", 2, False, "ok", "33"),
    (.5, "fast", 2, False, "ok", "33"),
    (.5, "insufficient", 2, False, "ok", None),
    (.5, "on_pace", 2, False, "ok", "32"),
    (0, "threat", 2, True, "out", "2"),
    (.05, "threat", 2, True, "low", "2"),
])
def test_compact_colour_precedence_matches_expanded_without_changing_words(
        remaining: float, projection: str, samples: int, aged: bool, word: str,
        code: str | None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NO_COLOR", raising=False)
    frame = frame_for({"cc.a": ["C-5H"]})
    item = frame["groups"]["capacity"]["data"]["observations"][0]
    item.update(age_seconds=400 if aged else 10, projection_samples=samples,
                reset_in_seconds=9000, projection={"state": projection, "pace_pt": 12.5})
    item["measurements"]["remaining_fraction"] = remaining
    before = deepcopy(frame)
    for expand in (False, True):
        screen = mv.render_grouped_frame(frame, color=True, unicode=False, width=180, expand=expand)
        row = next(line for line in screen.splitlines() if line.startswith("cc "))
        state_word = projection.upper() if expand else word
        if state_word == "INSUFFICIENT":
            state_word = "INSUFF"
        assert state_word in mv._ANSI.sub("", row).split()
        if code is None:
            assert "\x1b" not in row
        else:
            assert f"\x1b[{code}m{state_word}\x1b[0m" in row
            assert f"\x1b[{code}m^\x1b[0m" in row
            assert f"\x1b[{code}m[" in row
    assert frame == before


@pytest.mark.parametrize("state", ["mystery", "UNAVAILABLE"])
def test_unknown_evidence_is_dim_even_with_zero_remaining(state: str) -> None:
    assert mv._quota_color(state, 0) == "2"


@pytest.mark.parametrize("expand", [False, True])
def test_stale_out_is_dim_even_before_ttl(expand: bool, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NO_COLOR", raising=False)
    frame = frame_for({"cc.a": ["C-5H"]})
    item = frame["groups"]["capacity"]["data"]["observations"][0]
    item.update(state="STALE", last_measurements={"remaining_fraction": 0})
    screen = mv.render_grouped_frame(frame, color=True, width=180, expand=expand)
    assert "\x1b[1;31m" not in screen
    assert "\x1b[2mSTALE:window" in screen if expand else "\x1b[2mout" in screen


@pytest.mark.parametrize("reset,elapsed", [(0, 0), (10, 10), (-1, 0)])
def test_passed_measurement_reset_colour_matches_between_views(
        reset: int, elapsed: int, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NO_COLOR", raising=False)
    frame = frame_for({"cc.a": ["C-5H"]})
    item = frame["groups"]["capacity"]["data"]["observations"][0]
    item["measurements"]["reset_in_seconds"] = reset
    frame["display_elapsed_seconds"] = elapsed
    for expand, word in ((False, "reset"), (True, "ON_PACE")):
        screen = mv.render_grouped_frame(frame, color=True, width=180, expand=expand)
        assert f"\x1b[2m{word}\x1b[0m" in screen


@pytest.mark.parametrize("expand", [False, True])
def test_na_root_is_a_dim_full_row(expand: bool, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NO_COLOR", raising=False)
    frame = frame_for({"cc.a": ["C-5H"]})
    frame["groups"]["capacity"]["data"]["observations"][0].update(
        state="ABSENT", reason="not reported by provider", measurements=None)
    screen = mv.render_grouped_frame(frame, color=True, width=180, expand=expand)
    row = next(line for line in screen.splitlines() if "C-5H" in line and "not reported" not in line)
    assert row.startswith("\x1b[2mcc ") and row.endswith("\x1b[0m")


@pytest.mark.parametrize("expand", [False, True])
def test_pace_glyph_uses_row_colour_and_missing_pace_stays_dash(expand: bool, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NO_COLOR", raising=False)
    frame = frame_for({"cc.a": ["C-5H"]})
    item = frame["groups"]["capacity"]["data"]["observations"][0]
    item["measurements"]["remaining_fraction"] = .5
    colored = mv.render_grouped_frame(frame, view="rich", color=True, width=180, expand=expand)
    assert "\x1b[32m▼\x1b[0m-" in colored
    assert "\x1b[32m[" in colored
    item["projection"].pop("pace_pt")
    assert "▼" not in "\n".join(pool_lines(mv.render_grouped_frame(frame, color=True, width=180, expand=expand)))
    monkeypatch.setenv("NO_COLOR", "1")
    assert "\x1b" not in mv.render_grouped_frame(frame, color=True, width=180, expand=expand)


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
            assert all(mv._width(line) < width for line in screen.splitlines())
            assert "C-7D" not in screen or "C-30D" in screen
            assert "C-5H" not in screen or "C-7D" in screen
    assert frame == before


@pytest.mark.parametrize("expand", [False, True])
def test_duplicate_pool_names_keep_each_observations_capacity(expand: bool) -> None:
    frame = frame_for({"cc.a": ["C-7D", "C-5H"]})
    observations = frame["groups"]["capacity"]["data"]["observations"]
    original = next(item for item in observations if item["pool_ref"].endswith("C-7D"))
    observations.append({**deepcopy(original), "pool_ref": "other:cc:C-7D",
                         "measurements": {"remaining_fraction": .87, "reset_in_seconds": 3000}})
    # Skipped probe rows must not shift the source association of table rows.
    observations.insert(0, {"peer_id": "cc", "pool_ref": "quota", "state": "ERROR"})
    before = deepcopy(frame)
    screen = mv.render_grouped_frame(frame, view="plain", unicode=False, width=180, expand=expand)
    rows = pool_lines(screen)
    assert len(rows) == 3
    assert all("`-" not in row and "|-" not in row for row in rows)
    assert "20%" in rows[1] and "87%" in rows[2]
    assert frame == before


@pytest.mark.parametrize("expand", [False, True])
def test_error_evidence_keeps_alarm_colour(expand: bool, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NO_COLOR", raising=False)
    frame = frame_for({"cc.a": ["C-5H"]})
    frame["groups"]["capacity"]["data"]["observations"][0].update(state="ERROR", measurements=None)
    screen = mv.render_grouped_frame(frame, color=True, width=180, expand=expand)
    word = "ERROR" if expand else "?"
    assert f"\x1b[1;31m{word}\x1b[0m" in screen


def test_fast_and_low_intentionally_share_warning_colour() -> None:
    assert mv._quota_color("FAST", .5) == mv._quota_color("LOW", .05) == "33"


@pytest.mark.parametrize("remaining,left,code", [(.05, "5%", "33"), (.10, "10%", "33"), (.11, "11%", "32")])
def test_remaining_is_a_fraction_before_colour_threshold(remaining: float, left: str, code: str) -> None:
    frame = frame_for({"cc.a": ["C-5H"]})
    item = frame["groups"]["capacity"]["data"]["observations"][0]
    item["measurements"]["remaining_fraction"] = remaining
    summary = mv._compact_pool(item, 1000, 0, {})
    assert summary["remaining"] == remaining and summary["left"] == left
    assert mv._quota_color(summary["color_state"], summary["remaining"]) == code


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
        screen = mv.render_grouped_frame(frame, unicode=unicode, width=20, height=height, expand=expand)
        assert all(mv._width(line) < 20 for line in screen.splitlines())


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
    roomy = mv.render_grouped_frame(frame, unicode=unicode, width=width, height=200, expand=True)
    assert "\n\n" in roomy and "rows hidden" not in roomy
    exact_height = len(roomy.splitlines()) + 1
    exact = mv.render_grouped_frame(frame, unicode=unicode, width=width, height=exact_height, expand=True)
    assert exact == roomy
    for height in range(exact_height - 2, exact_height + 1):
        screen = mv.render_grouped_frame(frame, unicode=unicode, width=width, height=height, expand=True)
        assert len(screen.splitlines()) < height
        if "\n\n" in screen:
            assert "rows hidden" not in screen and screen.count("-> latest session:") == 2
            assert "Model facts:" in screen
    assert frame == before
