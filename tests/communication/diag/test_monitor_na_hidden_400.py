"""Unavailable windows are hidden in the human frame unless quota notes are enabled."""
from __future__ import annotations

import re
from typing import Any

import pytest

from peerhub.cli.monitor_preferences import SHOW_QUOTA_NOTES_DEFAULT
from peerhub.cli.monitor_format import (
    BAR_CELLS,
    BAR_MAX_CELLS,
    BAR_MIDDLE_CELLS,
    FAMILIES,
)
from peerhub.cli.monitor_layout import (
    RICH_GLYPHS,
    cell_width,
)
from peerhub.cli.monitor_legend import POOL_LEGEND
from peerhub.cli.monitor_view import (
    _HIDDEN_QUOTA_WINDOWS,
    render_grouped_frame,
)
from tests.communication.diag.test_monitor_activity_view import pool
from tests.communication.diag.test_monitor_expand_rich import block, expand, strip
from tests.communication.diag.test_monitor_plain_golden import _payload, full_payload

PROFILES = [{"profile": name, "fields": {"pools": {"value": pools}}} for name, pools in (
    ("cc.effort", ["C-5H", "C-7D"]), ("cx.effort", ["X-7D", "X-30D"]))]


def absent(peer: str, name: str) -> dict[str, Any]:
    item = pool(peer, name, None)
    item.update(state="ABSENT", stored_state="ABSENT", measurements=None, last_measurements=None,
                reason="not reported by provider; plan=prolite")
    return item


def na_payload(**over: Any) -> dict[str, Any]:
    observations = [pool("cc", "C-7D", .63, pace=-20), absent("cc", "C-5H"),
                    pool("cx", "X-7D", .42, pace=3), absent("cx", "X-30D")]
    return _payload([], observations, configured_profiles=PROFILES, min_quota=None, **over)


def wide_notes_payload() -> dict[str, Any]:
    profiles = [*PROFILES, {"profile": "cc.fable", "fields": {"pools": {"value": ["C-7D", "F-7D"]}}},
                {"profile": "ag.deepthink", "fields": {"pools": {"value": ["3P-5H", "3P-7D"]}}}]
    payload = na_payload(selected_groups=["capacity"])
    payload["configured_profiles"] = profiles
    observations = payload["groups"]["capacity"]["data"]["observations"]
    observations[0] = pool("cc", "C-7D", .09, pace=-2)
    observations.extend([
        pool("cc", "F-7D", .14, pace=3), absent("cx", "X-5H"),
        pool("ag", "3P-7D", .48, pace=2), absent("ag", "3P-5H"),
    ])
    return payload


@pytest.mark.parametrize("view", ["plain", "rich"])
def test_wide_pool_notes_stay_after_family_in_both_tables(view):
    payload = wide_notes_payload()
    notes = {"C-7D": "(C-5H n/a)", "F-7D": "limited by C-7D (own 14%)",
             "X-7D": "(X-5H,X-30D n/a)", "3P-7D": "(3P-5H n/a)"}
    for expand_mode in (False, True):
        screen = strip(render_grouped_frame(payload, view=view, width=190, expand=expand_mode, show_quota_notes=True))
        header = next(line for line in screen.splitlines() if "POOLS 8 " in line)
        left_end = header.index("LEFT") + len("LEFT")
        for name, note in notes.items():
            rows = [line for line in screen.splitlines()
                    if re.match(rf"^\s*(?:\w+ +)?(?:[└├]─ )?{name} {{2,}}", line)]
            assert len(rows) == (2 if expand_mode else 1), (name, screen)
            family = FAMILIES[name.split("-")[0]]
            assert all(row.index(f"{family}: ") < row.index(note) for row in rows)
            assert rows[0].index("%") + 1 == left_end
            if expand_mode:
                assert re.search(re.escape(note) + r" +\d+s$", rows[-1]), rows[-1]
            assert screen.count(note) == len(rows)
        assert all(cell_width(line) < 190 for line in screen.splitlines())
    narrow = render_grouped_frame(payload, view="plain", width=65, unicode=False, show_quota_notes=True)
    assert re.search(r"(?m)^ *\(X-5H,X-30D n/a\)$", narrow)
    assert re.search(r"(?m)^ *limited by C-7D \(own 14%\)$", narrow)


def test_rich_wide_header_aligns_with_cells_for_two_digit_pool_count():
    payload = na_payload(selected_groups=["capacity"])
    payload["groups"]["capacity"]["data"]["observations"].extend(
        absent("cx", f"X-{day}D") for day in range(10, 17))
    for expand_mode in (False, True):
        screen = strip(render_grouped_frame(payload, view="rich", width=190, expand=expand_mode, show_quota_notes=True))
        header, row, *_ = block(screen, "pools")
        assert header.startswith("── POOLS 11 ")
        glyphs = RICH_GLYPHS[True]
        bar = re.search(r"[█░|]+", row)
        assert bar and len(bar[0]) == BAR_MAX_CELLS
        assert header[bar.start():bar.end()] == glyphs["rule"] * BAR_MAX_CELLS
        assert header.index("STATE") == row.index(glyphs["ok"])
        assert header.index("LEFT") + len("LEFT") == row.index("%") + 1
        assert header.index("PACE(pp)") == row.index("-20.0")
        assert header.index("FAMILY") == row.index(f"{FAMILIES['C']}: ")


@pytest.mark.parametrize("width,bar_cells", [(90, BAR_CELLS), (100, BAR_MIDDLE_CELLS)])
def test_bar_keeps_its_room_when_family_and_notes_compete(width, bar_cells):
    for expand_mode in (False, True):
        screen = render_grouped_frame(wide_notes_payload(), view="plain", unicode=False, width=width, expand=expand_mode,
                                      show_quota_notes=True)
        rows = [line for line in screen.splitlines() if re.match(r"^ ?cc C-7D {2,}", line)]
        assert len(rows) == (2 if expand_mode else 1)
        for row in rows:
            bar = re.search(r"[#.|]+", row)
            assert bar and len(bar[0]) == bar_cells
            assert re.search(r"claude: .*[+]\d+", row)
        assert re.search(r"(?m)^\(C-5H n/a\)$", screen)


@pytest.mark.parametrize("view,unicode", [("rich", True), ("rich", False), ("plain", False)])
@pytest.mark.parametrize("expand_mode", [False, True])
@pytest.mark.parametrize("height", [None, 40], ids=["append", "viewport"])
def test_default_pool_visibility_and_opt_in(view, unicode, expand_mode, height):
    payload = na_payload(selected_groups=["capacity"])
    observations = payload["groups"]["capacity"]["data"]["observations"]
    observations.append(absent("ag", "A-7D"))
    options = dict(view=view, unicode=unicode, width=140, height=height, expand=expand_mode)
    default = render_grouped_frame(payload, **options)
    screens = {notes: render_grouped_frame(payload, show_quota_notes=notes, **options) for notes in (False, True)}
    assert default == screens[SHOW_QUOTA_NOTES_DEFAULT]
    hidden, shown = screens[False], screens[True]
    assert re.search(r"POOLS 2\b", hidden)
    assert re.search(r"POOLS 5\b", shown)
    assert all(pool not in hidden for pool in ("X-30D", "C-5H", "A-7D"))
    assert "(X-30D n/a)" in shown and "(C-5H n/a)" in shown
    assert "ag A-7D n/a not reported (plan prolite)" in shown
    assert "A-7D" in next(line for line in shown.splitlines() if line.startswith("gap vs "))
    assert all(pool in hidden and pool in shown for pool in ("C-7D", "X-7D"))


@pytest.mark.parametrize("view", ["plain", "rich"])
@pytest.mark.parametrize("expanded", [False, True])
def test_all_unavailable_pools_have_no_empty_tables_or_legend(view, expanded):
    payload = _payload([], [absent("ag", "A-7D")], selected_groups=["capacity"])
    options = dict(view=view, width=150, expand=expanded)
    hidden = render_grouped_frame(payload, show_quota_notes=SHOW_QUOTA_NOTES_DEFAULT, **options)
    assert re.search(r"POOLS 0\b[^\n]*$", hidden, re.MULTILINE)
    assert _HIDDEN_QUOTA_WINDOWS in hidden
    assert all(text not in hidden for text in ("A-7D", "PACE(pp)", "PEER POOL", "gap vs ", "no quota evidence"))

    shown = render_grouped_frame(payload, show_quota_notes=True, **options)
    assert re.search(r"POOLS 1\b", shown)
    assert "(A-7D n/a)" in shown
    assert "ag A-7D n/a not reported (plan prolite)" in shown
    assert POOL_LEGEND in " ".join(shown.split())


@pytest.mark.parametrize("view,unicode", [("rich", True), ("plain", False)])
def test_the_suggestion_still_skips_an_na_pool(view, unicode):
    screen = render_grouped_frame(na_payload(), view=view, unicode=unicode, width=100, height=None)
    assert "normal=cx.effort|cc.effort" in screen or "normal=cc.effort|cx.effort" in screen


def test_plain_expand_quota_names_an_na_pool_after_the_peers_row():
    screen = render_grouped_frame(na_payload(), view="plain", unicode=False, width=120, expand=True,
                                  show_quota_notes=True)
    rows = [line for line in screen.split("QUOTA [", 1)[1].splitlines() if re.search(r"\b[CX]-\d+[HD]\b", line)]
    assert not any(re.match(r"^\s*(?:(?:cc|cx)\s+)?(?:\S+\s)?(C-5H|X-30D)\b", row) for row in rows if "not reported" not in row)
    assert any(row.startswith("cc") and "C-7D" in row and "(C-5H n/a)" in row for row in rows)
    assert any(row.startswith("cx") and "X-7D" in row and "(X-30D n/a)" in row for row in rows)


def test_rich_expand_counts_unavailable_windows_and_names_them_in_quota():
    screen = expand(na_payload(), width=120, show_quota_notes=True)
    plain = strip(screen)
    assert re.search(r"POOLS 4\b", plain)
    assert not any(re.match(r"^\s*(?:(?:cc|cx)\s+)?\S*\s*(C-5H|X-30D)\b.*\bn/a\b", line) for line in plain.splitlines() if "not reported" not in line)
    quota = block(screen, "capacity")
    assert any("C-7D" in line and "(C-5H n/a)" in line for line in quota)
    assert any("X-7D" in line and "(X-30D n/a)" in line for line in quota)


def test_show_quota_notes_keeps_the_full_list():
    screen = render_grouped_frame(na_payload(), view="plain", unicode=False, width=120, expand=True,
                                  show_quota_notes=True)
    assert "cx X-30D n/a not reported (plan prolite)" in screen and "cc C-5H n/a not reported (plan prolite)" in screen


@pytest.mark.parametrize("notes", [False, True])
def test_retired_pools_join_the_peers_row_suffix_and_have_a_reason(notes):
    frame = na_payload()
    observations = frame["groups"]["capacity"]["data"]["observations"]
    for item in observations:
        if item["pool_ref"].endswith(("C-5H", "X-30D")):
            item.update(display_retired=True, display_plan="prolite")
    screen = render_grouped_frame(frame, view="plain", unicode=False, width=120, expand=True, show_quota_notes=notes)
    rows = [line for line in screen.split("QUOTA [", 1)[1].splitlines() if re.search(r"\b[CX]-\d+[HD]\b", line)]
    assert any(row.startswith("cx") and "(X-30D n/a)" in row for row in rows) == notes
    assert any(row.startswith("cc") and "(C-5H n/a)" in row for row in rows) == notes
    lines = [line for line in screen.splitlines() if line.startswith(("cx X-30D n/a", "cc C-5H n/a"))]
    assert len(lines) == (2 if notes else 0) and all("not reported by plan prolite" in line for line in lines)


def test_an_na_parent_passes_its_child_to_the_peers_first_row():
    payload = na_payload()
    payload["groups"]["capacity"]["data"]["observations"] = [
        pool("cc", "C-5H", .30), absent("cc", "C-7D"), pool("cx", "X-7D", .42), absent("cx", "X-30D")]
    screen = render_grouped_frame(payload, view="plain", unicode=False, width=120, expand=True, show_quota_notes=True)
    lines = screen.splitlines()
    rows = [line for line in lines[next(i for i, line in enumerate(lines) if "QUOTA [" in line):]  # the QUOTA details
            if re.search(r"\bC-\d+[HD]\b", line) and "not reported" not in line]
    assert len(rows) == 1 and rows[0].startswith("cc") and "C-5H" in rows[0] and "(C-7D n/a)" in rows[0]


def test_aged_and_unknown_pools_stay_visible():
    payload = full_payload()
    payload["selected_groups"] = ["capacity"]
    screen = render_grouped_frame(payload, view="plain", unicode=False, width=100)
    assert "F-7D" in screen and "A-7D" in screen
    assert ("(X-30D n/a)" in screen) == SHOW_QUOTA_NOTES_DEFAULT
