"""#374 H2: one budget function owns the size of the compact monitor frame (width, height, 24 lines, 8,192 bytes)."""
from __future__ import annotations

from typing import Any

import pytest

from peerhub.cli.monitor_view import (
    COMPACT_MAX_LINES, FRAME_HEADS, FRAME_SECTION_HEADS, FRAME_TRAILING_ROWS,
    CompactFrame, fit_compact_frame, render_grouped_frame, _width,
)
from tests.communication.diag.test_monitor_activity_view import NOW, activity_data, ask, pool, frame  # noqa: F401


def parts(**over: Any) -> CompactFrame:
    base: dict[str, Any] = dict(
        verdict="OK | 9 running | 1 lead",
        header=["PeerHub Monitor 2026-11-14 01:46Z | DB:env | quota age 10s"],
        running=["RUNNING 9 | cc 9", *[f" {i}m  cc.effort  lead  request {i}  task-{i}" for i in range(5)]],
        running_total=9,
        recent=["RECENT 4", " ok  1m  cc.effort  lead  reply a  done-a", " ok  2m  cc.effort  lead  reply b  done-b"],
        suggest=["suggest: eco=cc.standard normal=cc.effort", "  quality=cc.deepthink top=cc.fable"],
        pools=["POOLS 10  STATE LEFT", *[f" cc P{i}-7D  ok  50%" for i in range(8)]],
        pools_total=10)
    base.update(over)
    return CompactFrame(**base)


def fitted(height: int | None, width: int = 100, **over: Any) -> list[str]:
    return fit_compact_frame(parts(**over), width=width - 1, height=height, unicode=True).splitlines()


def test_unbounded_frame_keeps_every_section_within_24_lines():
    lines = fitted(None)
    assert len(lines) == COMPACT_MAX_LINES
    assert "+2 pools (--expand)" in lines and "+4 more (--expand)" in lines
    assert [line.split()[0] for line in lines if not line.startswith((" ", "+"))][2:] == [
        head for head in FRAME_HEADS if head not in {"ALERTS", "LEADS"}]
    assert lines.count("RECENT 4") == 1 and len([line for line in lines if line.startswith(" ok ")]) == 2


@pytest.mark.parametrize("height", range(9))
def test_small_heights_keep_the_header_and_running(height):
    lines = fitted(height)
    assert len(lines) <= height
    if height > FRAME_TRAILING_ROWS:
        assert lines[0].startswith("OK | 9 running")
    if height > FRAME_TRAILING_ROWS + 1:
        assert lines[1].startswith("PeerHub Monitor")
    if any(line.startswith("RUNNING") for line in lines):
        assert any("task-" in line for line in lines)
    assert "RECENT 4" not in lines


def test_zero_budget_prints_nothing():
    assert fit_compact_frame(parts(), width=99, height=1, unicode=True) == ""
    assert fit_compact_frame(parts(), width=99, height=0, unicode=True) == ""


def test_each_step_removes_in_the_single_order():
    previous = None
    seen: list[tuple[int, int, int, int, bool, bool, bool, int]] = []
    for height in range(25, 1, -1):
        lines = fitted(height)
        assert len(lines) <= height - 1
        running = sum(line.startswith(" ") and "task-" in line for line in lines)
        recent = sum("done-" in line for line in lines)
        pools = sum(line.startswith(" cc P") for line in lines)
        suggest = sum(line.startswith(("suggest:", "  quality")) for line in lines)
        state = (recent, running, pools, suggest, any(l.startswith("RECENT") for l in lines),
                 any(l.startswith("POOLS") for l in lines), any(l.startswith("RUNNING") for l in lines), len(lines))
        seen.append(state)
        if previous is not None:
            assert all(now <= before for now, before in zip(state, previous)), (height, lines)
        previous = state
    assert any(state[0] == 0 and state[1] == 5 for state in seen)
    assert all(state[1] == 5 for state in seen if state[0])


def test_first_removal_that_creates_an_overflow_line_removes_two_rows():
    rows = [f" row {i}" for i in range(5)]
    base = dict(running=["RUNNING 5", *rows], running_total=5, recent=None, suggest=[], pools=None, pools_total=0)
    full = fitted(None, **base)
    assert len(full) == 8 and "+" not in full[-1]
    lines = fitted(len(full), **base)  # one line over: the overflow line costs one more
    assert lines[-1] == "+2 more (--expand)" and lines[-2] == " row 2"
    assert len(lines) == len(full) - 1


@pytest.mark.parametrize("key,total", [("running", 4), ("pools", 5)])
def test_first_cut_above_the_floor_keeps_the_overflow_count(key, total):
    # K5: one row over the budget. The first cut takes two rows to leave room for the overflow line.
    head = f"{FRAME_SECTION_HEADS[key]} {total}"
    rows = [f" row {i}" for i in range(total)]
    base = {"running": None, "pools": None, "recent": None, "suggest": [], key: [head, *rows],
            f"{key}_total": total}
    full = fitted(None, **base)
    lines = fitted(len(full), **base)  # one line over
    noun = "more" if key == "running" else "pools"
    assert f"+2 {noun} (--expand)" in lines and sum(line.startswith(" row") for line in lines) == total - 2
    assert len(lines) == len(full) - 1


def test_an_updated_overflow_line_is_refitted_to_the_width():
    rows = [f" r{i}" for i in range(12)]
    base = dict(running=["RUNNING 12", *rows[:5]], running_total=12, recent=None, suggest=[], pools=None, pools_total=0)
    for width in (19, 17, 9, 4):
        for height in range(3, 12):
            lines = fit_compact_frame(parts(**base), width=width, height=height, unicode=False).splitlines()
            assert all(_width(line) <= width for line in lines), (width, height, lines)
            shown = sum(line.startswith(" r") for line in lines)
            overflow = [line for line in lines if line.startswith("+")]
            hidden = 12 - shown
            if shown:  # the overflow line names exactly the rows that are not shown, as long as rows remain
                assert overflow == [next(text for text in (f"+{hidden} more (--expand)", f"+{hidden} more", f"+{hidden}")
                                         if _width(text) <= width)], (width, height, lines)
            else:
                assert overflow == []
    # "+11 more (--expand)" needs 19 columns; at 18 the hint gives way, the number never does
    lines = fit_compact_frame(parts(**base), width=18, height=6, unicode=False).splitlines()
    assert lines[-1] == "+11 more"
    assert all(_width(line) <= 18 for line in lines)


def test_wide_and_zero_width_characters_fit_the_width():
    wide = "가" * 70
    zero = "e" + "́" * 40
    lines = fitted(None, width=80, verdict="OK | " + wide, header=["PeerHub Monitor " + zero * 3],
                   running=["RUNNING 1", f" 1m  {wide}  {zero}"], running_total=1)
    assert all(_width(line) <= 79 for line in lines)
    assert lines[0].endswith("…") and _width(lines[0]) in (78, 79)


def test_byte_ceiling_removes_lines_in_order_then_cuts_line_1():
    heavy = "a" + "́" * 300  # one column, 601 bytes
    lines = fitted(None, pools=["POOLS 10", *[f" {heavy} {i}" for i in range(8)]])
    assert len("\n".join(lines).encode("utf-8")) + 1 <= 8192
    assert lines[0].startswith("OK |") and any(line.startswith("POOLS") for line in lines)
    only = fit_compact_frame(parts(verdict="OK " + heavy * 20, header=[heavy * 20]), width=99, height=None,
                             unicode=True)
    assert len(only.encode("utf-8")) <= 8192 and only.count("\n") == 1 and only.startswith("OK ")


@pytest.mark.parametrize("height", range(0, 7))
def test_rendered_frame_with_10_pools_and_9_running_asks_at_small_heights(frame, height):  # noqa: F811
    frame["groups"]["activity"]["data"] = activity_data(
        [ask(name=f"t-{i}", elapsed=60.0 * (i + 1), ask_line="요청 " * 30) for i in range(9)]
        + [ask("ok", name="done", age=60, exact=True, reply_line="fine")])
    screen = render_grouped_frame(frame, view="plain", unicode=True, width=100, height=height)
    lines = screen.splitlines()
    assert len(lines) <= max(0, height - FRAME_TRAILING_ROWS)
    if height >= 2:
        assert lines[0].startswith("OK | 9 running")
    if height >= 3:
        assert lines[1].startswith("PeerHub Monitor")
    assert not any(line.startswith("RECENT") for line in lines)


@pytest.mark.parametrize("width", [80, 100])
def test_worst_case_frame_stays_within_the_bounds(frame, width):  # noqa: F811
    observations = frame["groups"]["capacity"]["data"]["observations"]
    for o in observations:
        o["last_measurements"] = {"remaining_fraction": .05}
    frame["groups"]["activity"]["data"] = activity_data(
        [ask(name="이름" * 20, elapsed=60.0 * (i + 1), ask_line="요청" * 60, lead=("리드" * 20, "cc", f"{i:08x}"))
         for i in range(9)]
        + [ask("ok", name=f"done-{i}", age=60, exact=True, reply_line="答" * 80) for i in range(4)])
    frame["alerts"] = [{"code": "a1", "kind": "empty", "subject": "가" * 120, "age_s": 1.0, "severity": "high"}] * 5
    screen = render_grouped_frame(frame, view="rich", unicode=True, width=width)
    lines = screen.splitlines()
    assert len(lines) <= 24 and len(screen.encode("utf-8")) <= 8192
    assert all(_width(line) <= min(100, width) - 1 for line in lines)


def test_ascii_frame_is_at_most_2400_bytes(frame):  # noqa: F811
    frame["groups"]["capacity"]["data"]["observations"].extend(pool("zz", f"F-{i}D", .5) for i in (1, 2))
    frame["groups"]["activity"]["data"] = activity_data(
        [ask(name=f"task-name-{i}", elapsed=60.0 * (i + 1), ask_line="r" * 60) for i in range(9)]
        + [ask("ok", name=f"done-{i}", age=60, exact=True, reply_line="v" * 60) for i in range(3)])
    screen = render_grouped_frame(frame, view="plain", unicode=False, width=100)
    assert screen.isascii() and len(screen.splitlines()) <= 24
    assert len(screen.encode("utf-8")) <= 2400
