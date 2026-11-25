"""POOLS shows the usable weekly families in use-first order."""
from __future__ import annotations

import re
from copy import deepcopy

import pytest

from peerhub.cli import monitor_format, monitor_layout, monitor_view as mv
from tests.communication.diag.test_monitor_activity_view import pool
from tests.communication.diag.test_monitor_plain_golden import _payload


@pytest.mark.parametrize("view", ["plain", "rich"])
@pytest.mark.parametrize("expand", [False, True], ids=["compact", "expand"])
@pytest.mark.parametrize("reverse", [False, True], ids=["configured", "reversed"])
def test_pools_use_parent_pace_keep_the_tree_and_cap_the_tail(view: str, expand: bool, reverse: bool, monkeypatch) -> None:
    if reverse:
        monkeypatch.setattr(monitor_format, "POOLS_USE_FIRST_ASCENDING", not monitor_format.POOLS_USE_FIRST_ASCENDING)
    # The binding C-5H puts its family in the out bucket, regardless of weekly PACE.
    known = [
        ("ag", "B-7D", .40, -20.24), ("ag", "A-7D", .40, -20.24), ("zz", "Z-7D", .40, -20.24),
        ("cx", "X-7D", .80, 15), ("ag", "G-7D", .02, 30),
    ]
    payload = _payload([], [pool(peer, name, left, pace=pace) for peer, name, left, pace in reversed(known)] + [
        pool("cc", "C-7D", .60, pace=-20.49), pool("cc", "F-7D", .30, pace=-60),
        pool("cc", "C-5H", 0), pool("ag", "U-7D", .90),
        pool("cx", "O-7D", 0, pace=-70), pool("ag", "E-7D", 0),
    ], configured_profiles=[
        {"profile": "cc.effort", "fields": {"pools": {"value": ["C-7D", "C-5H"]}}},
        {"profile": "cc.fable", "fields": {"pools": {"value": ["C-7D", "F-7D"]}}},
    ])
    payload["alerts"] = [{"code": "a3", "kind": "pool_out", "subject": "cc C-5H", "severity": "high"}]
    original = deepcopy(payload)
    render = {"view": view, "unicode": view == "rich", "expand": expand, "width": 100}
    screen = mv.render_grouped_frame(payload, **render)
    pools = screen.split(mv.FRAME_SECTIONS["suggest"], 1)[0].split(mv.FRAME_SECTIONS["pools"], 1)[1]
    direction = 1 if monitor_format.POOLS_USE_FIRST_ASCENDING else -1
    expected = [row[1] for row in sorted(known, key=lambda row: (direction * row[3], *row[:2]))]
    expected.extend(("U-7D", "E-7D", "C-7D", "C-5H", "F-7D", "O-7D"))
    shown = re.findall(r"(?m)^ +(?:\S+ +)?([A-Z]+-(?:7D|5H))(?= +)", pools)
    assert shown == (expected if expand else expected[:mv.POOLS_CAP])
    assert (re.findall(r"(?m)^\+(\d+) pools", pools) ==
            ([] if expand else [str(len(expected) - mv.POOLS_CAP)]))
    assert ("needs C-7D" in pools) == expand
    assert "ATTN 1" in screen and "pool_out: cc C-5H" in screen
    assert payload == original


@pytest.mark.parametrize("view", ["plain", "rich"])
@pytest.mark.parametrize("expand", [False, True], ids=["compact", "expand"])
def test_pools_hint_only_uses_spare_heading_width(view: str, expand: bool) -> None:
    payload = _payload([], [pool("cc", "C-7D", .60, pace=-20)])
    hint = mv.POOLS_USE_FIRST_HINT
    for width in (48, 100):
        screen = mv.render_grouped_frame(payload, view=view, expand=expand, width=width)
        heading = next(line for line in screen.splitlines() if mv.FRAME_SECTIONS["pools"] in line)
        assert (hint in heading) == (width == 100)
        assert screen.count(hint) == (width == 100)
        assert ("FAMILY" in heading) == (width == 100) and monitor_layout.cell_width(heading) < width  # G4


@pytest.mark.parametrize("expand", [False, True], ids=["compact", "expand"])
def test_a_monthly_pool_without_a_weekly_parent_sorts_by_its_own_pace(expand: bool) -> None:
    payload = _payload([], [pool("cx", "X-7D", .60, pace=-3), pool("ag", "A-7D", .90, pace=8),
                            pool("cx", "X-30D", .93, pace=5)])
    screen = mv.render_grouped_frame(payload, view="plain", expand=expand, width=100)
    pools = screen.split(mv.FRAME_SECTIONS["suggest"], 1)[0].split(mv.FRAME_SECTIONS["pools"], 1)[1]
    assert re.findall(r"(?m)^ +(?:\S+ +)?([A-Z]+-(?:30D|7D|5H))(?= +)", pools) == ["X-7D", "X-30D", "A-7D"]
