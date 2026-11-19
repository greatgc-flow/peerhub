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
# frame orders POOLS by class (out, low, binding, fast, the rest) and shows a 5-hour row under its 7-day pool only when
# it binds (test_monitor_activity_view.py). The tree pins below therefore run for --expand only.
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


@pytest.mark.parametrize("members", [
    {"cc.a": ["C-7D", "C-5H"], "cc.b": ["C-7D", "F-7D"], "cc.c": ["F-7D"]},
    {"cc.a": ["C-7D", "C-5H"], "cc.b": ["C-30D", "C-5H"]},
])
def test_crossing_or_ambiguous_peer_is_flat(members: dict[str, list[str]]) -> None:
    frame = frame_for(members)
    for expand in (True,):  # the tree is --expand only (#374 F5)
        screen = mv.render_grouped_frame(frame, view="plain", unicode=False, width=180, expand=expand)
        rows = pool_lines(screen)
        assert not any("|-" in row or "`-" in row for row in rows)
        expected = sorted({pool for pools in members.values() for pool in pools}, key=lambda p: mv._pool_order("cc", p))
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
    rich_rows = pool_lines(mv.render_grouped_frame(frame, view=_view(expand), unicode=True, width=180, expand=expand))
    for pool, value in (("C-30D", "10%"), ("C-5H", "20%"), ("C-7D", "30%")):
        row = next(row for row in ascii_rows if pool in row)
        assert value in row.split()
        assert value in next(row for row in rich_rows if pool in row).split()
    # P21: the compact rich rows carry the state glyph and the bar, so the width parity of the two glyph sets is
    # checked inside one view (the plain --expand rows are the same with either glyph set).
    same_view = pool_lines(mv.render_grouped_frame(frame, view=_view(expand), unicode=False, width=180, expand=expand))
    assert [mv._width(row) for row in rich_rows] == [mv._width(row) for row in same_view]
    if expand:
        assert same_view == ascii_rows


@EXPAND_ONLY
def test_retired_pools_form_one_dim_note_per_peer(expand: bool, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NO_COLOR", raising=False)
    frame = frame_for({"cc.a": ["C-5H", "C-7D", "C-30D"]})
    for item in frame["groups"]["capacity"]["data"]["observations"]:
        if not item["pool_ref"].endswith("C-7D"):
            item.update(state="ABSENT", reason="not reported by provider", display_retired=True, display_plan="pro")
    screen = mv.render_grouped_frame(frame, show_quota_notes=True, view="rich", color=True, width=180, expand=expand)
    notes = [line for line in detail_lines(screen) if "not reported by plan" in line]
    assert len(notes) == 1 and "C-5H,C-30D" in notes[0]  # one note in the QUOTA details (POOLS has a row per pool)
    assert notes[0].strip().startswith(f"\x1b[{mv.RICH_SGR['dim']}m") and notes[0].endswith("\x1b[0m")
    plain = mv.render_grouped_frame(frame, show_quota_notes=True, view="plain", color=True, unicode=False, width=180, expand=expand)
    assert [line for line in plain.splitlines() if "not reported by plan" in line] == [
        "cc C-5H,C-30D n/a (not reported by plan pro)"]


@EXPAND_ONLY
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
    screen = mv.render_grouped_frame(frame, show_quota_notes=True, view="plain", unicode=False, width=180, expand=True)
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


@pytest.mark.parametrize("remaining,projection,samples,aged,word,code,colour", [
    (0, "threat", 2, False, "out", "1;31", "bad"),
    (.05, "threat", 2, False, "low", "31", "bad"),
    (.5, "threat", 2, False, "fast", "31", "bad"),
    (.5, "threat", 1, False, "ok", "31", "bad"),
    (.05, "insufficient", 2, False, "low", "33", "bad"),    # 10% or less left is bad, whatever the state's colour
    (.5, "watch", 2, False, "ok", "33", "warn"),
    (.8, "watch", 2, False, "ok", "33", "warn"),            # never milder than the state's colour
    (.5, "fast", 2, False, "ok", "33", "warn"),
    (.5, "insufficient", 2, False, "ok", None, "warn"),     # a valid pool takes the colour of what is left
    (.5, "on_pace", 2, False, "ok", "32", "warn"),
    (.8, "on_pace", 2, False, "ok", "32", "ok"),
    (.2, "on_pace", 2, False, "ok", "32", "hot"),
    (0, "threat", 2, True, "out", "2", "dim"),
    (.05, "threat", 2, True, "low", "2", "dim"),
])
def test_compact_colour_precedence_matches_expanded_without_changing_words(
        remaining: float, projection: str, samples: int, aged: bool, word: str,
        code: str | None, colour: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """`code` is what `_quota_color` decides first. P21: the rich POOLS row has one colour from the 256-colour palette
    (`colour`): `_quota_color` first (dim for aged evidence, bad for a threat), then what is left. The QUOTA details
    row of the rich --expand frame has the same state cell in the same colour, and names the forecast."""
    monkeypatch.delenv("NO_COLOR", raising=False)
    frame = frame_for({"cc.a": ["C-5H"]})
    item = frame["groups"]["capacity"]["data"]["observations"][0]
    item.update(age_seconds=400 if aged else 10, projection_samples=samples,
                reset_in_seconds=9000, projection={"state": projection, "pace_pt": 12.5})
    item["measurements"]["remaining_fraction"] = remaining
    before = deepcopy(frame)
    summary = mv._compact_pool(item, 1000, 0, {})
    assert mv._quota_color(str(summary["color_state"]), summary["remaining"], aged=bool(summary["aged"])) == code
    sgr = mv.RICH_SGR[colour]
    glyph = mv.RICH_GLYPHS[False].get(word, "o")
    for expand in (False, True):
        screen = mv.render_grouped_frame(frame, view="rich", color=True, unicode=False, width=180, expand=expand)
        rows = [line for line in screen.splitlines() if line.lstrip().startswith("cc ")]
        row = rows[0]  # the POOLS row: the same row in the compact frame and at the top of --expand
        assert word in mv._ANSI.sub("", row).split()
        assert f"\x1b[{sgr}m{glyph} {word}\x1b[0m" in row and f"\x1b[{sgr}m^\x1b[0m+12" in row
        assert re.search(r"\x1b\[" + sgr + r"m[#.]{20}\x1b\[0m", row)  # P21: the bar, in the row's colour
        assert set(re.findall(r"\x1b\[([0-9;]*)m", row)) == {sgr, mv.RICH_SGR["dim"], "0"}
        if not expand:
            assert len(rows) == 1
            continue
        detail = rows[1]
        assert f"\x1b[{sgr}m{glyph} {word}\x1b[0m" in detail and f"\x1b[{sgr}m^\x1b[0m+12.5pp" in detail
        assert ("insuff" if projection == "insufficient" else projection) in mv._ANSI.sub("", detail).split()
        assert set(re.findall(r"\x1b\[([0-9;]*)m", detail)) == {sgr, "0"} and "[" not in mv._ANSI.sub("", detail)
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
    screen = mv.render_grouped_frame(frame, view="rich", color=True, width=180, expand=expand)
    assert "\x1b[1;31m" not in screen and f"\x1b[{mv.RICH_SGR['bad']}m" not in screen
    # P21: the POOLS row is dim in the palette's dim, state cell and (empty) bar alike; so is the QUOTA details row
    assert screen.count(f"\x1b[{mv.RICH_SGR['dim']}m× out\x1b[0m") == (2 if expand else 1)
    assert f"\x1b[{mv.RICH_SGR['dim']}m{'░' * 20}\x1b[0m" in screen
    if expand:
        assert "STALE:window" not in screen.split("QUOTA [")[1].splitlines()[2]  # the state cell is the compact word
        assert "cc STALE:window; next: peerhub observation refresh --peers cc" in screen  # the note keeps the code


@pytest.mark.parametrize("reset,elapsed", [(0, 0), (10, 10), (-1, 0)])
def test_passed_measurement_reset_colour_matches_between_views(
        reset: int, elapsed: int, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NO_COLOR", raising=False)
    frame = frame_for({"cc.a": ["C-5H"]})
    item = frame["groups"]["capacity"]["data"]["observations"][0]
    item["measurements"]["reset_in_seconds"] = reset
    frame["display_elapsed_seconds"] = elapsed
    cell = f"\x1b[{mv.RICH_SGR['dim']}m○ reset\x1b[0m"
    for expand in (False, True):
        screen = mv.render_grouped_frame(frame, view="rich", color=True, width=180, expand=expand)
        # dim in the palette's dim: the POOLS row, and the QUOTA details row of --expand (the same state cell)
        assert screen.count(cell) == (2 if expand else 1)


@pytest.mark.parametrize("expand", [False, True])
def test_na_root_is_never_a_row_and_its_peer_gets_one_dim_note(expand: bool, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NO_COLOR", raising=False)
    frame = frame_for({"cc.a": ["C-5H"]})
    frame["groups"]["capacity"]["data"]["observations"][0].update(
        state="ABSENT", reason="not reported by provider", measurements=None)
    screen = mv.render_grouped_frame(frame, show_quota_notes=True, view="rich", color=True, width=180, expand=expand)
    # #400: no POOLS row and no QUOTA row for an n/a pool; the peer has no row left, so one dim note names the pool
    assert "POOLS 0" in mv._ANSI.sub("", screen)
    dim = mv.RICH_SGR["dim"]
    assert not any("○ n/a" in line for line in screen.splitlines())
    if not expand:
        assert "C-5H" not in screen
        return
    row = next(line for line in detail_lines(screen) if "C-5H" in line and "not reported" not in line)
    assert row == f" \x1b[{dim}mcc C-5H n/a\x1b[0m"


@pytest.mark.parametrize("expand", [False, True])
def test_pace_glyph_uses_row_colour_and_missing_pace_stays_dash(expand: bool, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NO_COLOR", raising=False)
    frame = frame_for({"cc.a": ["C-5H"]})
    item = frame["groups"]["capacity"]["data"]["observations"][0]
    item["measurements"]["remaining_fraction"] = .5
    colored = mv.render_grouped_frame(frame, view="rich", color=True, width=180, expand=expand)
    code = mv.RICH_SGR["warn"]  # P21: 50% left is the palette's warn, in the POOLS row and in the QUOTA details row
    assert colored.count(f"\x1b[{code}m▼\x1b[0m-") == (2 if expand else 1)
    assert f"\x1b[{code}m{'█' * 10}{'░' * 10}\x1b[0m" in colored
    assert "\x1b[32m" not in colored and "[█" not in colored  # no 8-cell bar and no second palette in the rich view
    item["projection"].pop("pace_pt")
    assert "▼" not in "\n".join(pool_lines(mv.render_grouped_frame(frame, view="rich", color=True, width=180,
                                                                   expand=expand)))
    monkeypatch.setenv("NO_COLOR", "1")
    assert "\x1b" not in mv.render_grouped_frame(frame, view="rich", color=True, width=180, expand=expand)


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
    assert all("`-" not in row and "|-" not in row for row in rows)
    assert "5%" in rows[1] and "87%" in rows[2]
    assert frame == before
    compact = mv.render_grouped_frame(frame, view="plain", unicode=False, width=180)  # each keeps its own value
    assert "5%" in compact and "87%" in compact and "10%" in compact
    rich = mv.render_grouped_frame(frame, view="rich", color=True, width=180, expand=expand)
    quota_rows = [row for row in detail_lines(rich) if "C-7D" in row]
    assert len(quota_rows) == 2
    for row, state, left, colour in zip(quota_rows, ("▼ low", "● ok"), ("5%", "87%"), ("bad", "ok")):
        assert re.search(rf"{state}.*{left}", mv._ANSI.sub("", row))
        assert f"\x1b[{mv.RICH_SGR[colour]}m{state}\x1b[0m" in row
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
    cell = f"\x1b[{mv.RICH_SGR['bad']}m○ ?\x1b[0m"  # P21: the palette's bad, in POOLS and in the QUOTA details
    assert screen.count(cell) == (2 if expand else 1)
    if expand:  # the compact state word hides the evidence state: a note names it
        assert "cc C-5H evidence ERROR; next: peerhub observation refresh --peers cc" in mv._ANSI.sub("", screen)
        plain = mv.render_grouped_frame(frame, view="plain", color=True, unicode=False, width=180, expand=True)
        assert "\x1b" not in plain and " ERROR " in next(line for line in plain.splitlines() if "C-5H" in line)


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
        for view in ("plain", "rich"):
            screen = mv.render_grouped_frame(frame, view=view, unicode=unicode, width=20, height=height, expand=expand)
            assert all(mv._width(line) < 20 for line in screen.splitlines()), (view, height)


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
    exact_height = len(roomy.splitlines()) + 1
    exact = mv.render_grouped_frame(frame, view="plain", unicode=unicode, width=width, height=exact_height, expand=True)
    assert exact == roomy
    for height in range(exact_height - 2, exact_height + 1):
        screen = mv.render_grouped_frame(frame, view="plain", unicode=unicode, width=width, height=height, expand=True)
        assert len(screen.splitlines()) < height
        if "\n\n" in screen:
            assert "rows hidden" not in screen and screen.count("-> latest session:") == 2
            assert "Model facts:" in screen
    assert frame == before


@pytest.mark.parametrize("height", [1, 2, 3])
def test_tiny_heights_never_overflow_and_announce_cuts_when_a_line_fits(height: int) -> None:
    frame = frame_for({"cc.deepthink": ["C-5H", "C-7D"], "cc.fable": ["F-7D", "C-7D"]})
    screen = mv.render_grouped_frame(frame, view="plain", unicode=False, width=100, height=height, expand=True)
    lines = screen.splitlines()
    assert len(lines) < max(height, 2)  # the cursor line stays free; height 1 renders nothing rather than overflow
    if height == 2:  # #374: --expand starts with the verdict line; one line holds only that
        assert lines == ["OK? (alerts not read)"]
    if height >= 3:
        assert "rows hidden" in screen
