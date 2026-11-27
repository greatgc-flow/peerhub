"""Both monitor views hide unavailable windows."""
from __future__ import annotations

import re
from copy import deepcopy
from typing import Any

import pytest

from peerhub.cli.monitor_format import (
    BAR_CELLS,
    BAR_MAX_CELLS,
    BAR_MIDDLE_CELLS,
)
from peerhub.cli.monitor_layout import (
    RICH_GLYPHS,
    cell_width,
)
from peerhub.cli.monitor_view import render_grouped_frame
from peerhub.extensions.observation_model import epoch_to_iso
from peerhub.extensions.quota_windows import FAMILIES
from tests.communication.diag.test_monitor_activity_view import NOW, pool
from tests.communication.diag.test_monitor_expand_rich import block, strip
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


@pytest.mark.parametrize("age,reset", [(12 * 86400, -60), (31 * 86400, 86400)])
@pytest.mark.parametrize("expanded", [False, True])
def test_expired_reading_is_retired_and_does_not_age_header(age, reset, expanded):
    expired = pool("cx", "X-30D", .42, reset=reset)
    expired.update(state="STALE", age_seconds=age)
    payload = _payload([], [expired, pool("ag", "G-7D", .64)], selected_groups=["capacity"], read_at=NOW)
    screen = render_grouped_frame(payload, view="plain", width=100, expand=expanded)
    assert "X-30D" not in screen
    assert "quota age 10s" in screen and "quota 12d old" not in screen


@pytest.mark.parametrize("expanded", [False, True])
@pytest.mark.parametrize("mapped", [False, True])
def test_redraw_retires_exhaustion_alert_and_peer_summary(expanded, mapped):
    expired = pool("cx", "X-7D", 0, reset=20)
    observations = [expired, pool("cx", "X-30D", .64), pool("cc", "C-7D", 0)]
    payload = _payload([], observations, _human_observations=observations,
                       _human_peer_pools={"cx": ["X-7D", "X-30D"]} if mapped else {},
                       alerts=[{"code": "a3", "kind": "pool_out", "subject": f"{peer} {name}",
                                "age_s": None, "severity": "high"}
                               for peer, name in (("cc", "C-7D"), ("cx", "X-7D"))])
    cx = next(peer for peer in payload["groups"]["peers"]["data"]["peers"] if peer["peer_id"] == "cx")
    cx["quota"] = {"pool_ref": expired["pool_ref"], "state": "MEASURED", "remaining_fraction": 0}
    original = deepcopy(payload)

    def render(frame):
        return render_grouped_frame(frame, view="plain", width=120, expand=expanded)

    before = render(payload)
    assert "pool_out: cx X-7D" in before and "pool_out: cc C-7D" in before
    if expanded:
        assert "cx  quota: 0% (X-7D)" in before
    for frame in ({**payload, "display_elapsed_seconds": 21}, {**payload, "read_at": NOW + 21}):
        screen = render(frame)
        assert "pool_out: cc C-7D" in screen
        assert "pool_out: cx X-7D" not in screen
        if expanded:
            assert ("cx  quota: incomplete" if mapped else "cx  quota: 64% (X-30D)") in screen
    assert payload == original


@pytest.mark.parametrize("expanded", [False, True])
def test_stored_relative_reset_expires_from_observation_time(expanded):
    relative = pool("cx", "X-30D", .42)
    relative.pop("resets_at")
    relative.update(age_seconds=60, observed_at=epoch_to_iso(NOW - 100), captured_at=epoch_to_iso(NOW - 60),
                    last_measurements={"remaining_fraction": .42, "reset_in_seconds": 105})
    payload = _payload([], [relative, pool("ag", "G-7D", .64)], selected_groups=["capacity"])
    options = dict(view="plain", width=120, expand=expanded)
    before = render_grouped_frame(payload, **options)
    assert "X-30D" in before and "quota age 1m" in before
    payload["display_elapsed_seconds"] = 6
    screen = render_grouped_frame(payload, **options)
    assert "X-30D" not in screen
    assert "quota age 16s" in screen


@pytest.mark.parametrize("expanded", [False, True])
def test_hidden_duplicate_does_not_change_gap_or_parentage(expanded):
    duplicate = absent("cc", "C-7D")
    duplicate.update(pool_ref="quota:other-account:C-7D", observation_id="other-account-C-7D")
    observations = [duplicate, pool("cc", "C-5H", .30), pool("cc", "C-7D", .63, pace=-20),
                    pool("cx", "X-7D", .42, pace=3)]
    payload = _payload([], observations, selected_groups=["capacity"], configured_profiles=PROFILES)
    screen = render_grouped_frame(payload, view="plain", width=150, expand=expanded)
    assert "gap vs X-7D (pp): C-7D -23.0" in screen
    assert len(re.findall(r"(?m)^\s+└─ C-5H\s", screen)) == (2 if expanded else 1)
    assert "C-7D n/a" not in screen


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
def test_wide_parent_limit_stays_after_family_in_both_tables(view):
    payload = wide_notes_payload()
    note = "limited by C-7D (own 14%)"
    screen = strip(render_grouped_frame(payload, view=view, width=190, expand=True))
    header = next(line for line in screen.splitlines() if "POOLS 4 " in line)
    left_end = header.index("LEFT") + len("LEFT")
    rows = [line for line in screen.splitlines()
            if re.match(r"^\s*(?:\w+ +)?(?:[└├]─ )?F-7D {2,}", line)]
    assert len(rows) == 2, screen
    family = FAMILIES["F"]
    assert all(row.index(f"{family}: ") < row.index(note) for row in rows)
    assert rows[0].index("%") + 1 == left_end
    assert re.search(re.escape(note) + r" +\d+s$", rows[-1]), rows[-1]
    assert screen.count(note) == len(rows)
    assert all(cell_width(line) < 190 for line in screen.splitlines())
    narrow = render_grouped_frame(payload, view="plain", width=65, unicode=False, expand=True)
    assert re.search(r"(?m)^ *limited by C-7D \(own 14%\)$", narrow)


def test_rich_wide_header_aligns_with_cells_for_two_digit_pool_count():
    payload = na_payload(selected_groups=["capacity"])
    payload["groups"]["capacity"]["data"]["observations"].extend(
        pool("cx", f"X-{day}D", .5) for day in range(10, 18))
    screen = strip(render_grouped_frame(payload, view="rich", width=190, expand=True))
    header, *rows = block(screen, "pools")
    assert header.startswith("── POOLS 10 ")
    row = next(line for line in rows if "C-7D" in line)
    glyphs = RICH_GLYPHS[True]
    bar = re.search(r"[█░|]+", row)
    assert bar and len(bar[0]) == BAR_MAX_CELLS
    assert header[bar.start():bar.end()] == glyphs["rule"] * BAR_MAX_CELLS
    assert header.index("STATE") == row.index(glyphs["ok"])
    assert header.index("LEFT") + len("LEFT") == row.index("%") + 1
    assert header.index("PACE(pp)") == row.index("-20.0")
    assert header.index("FAMILY") == row.index(f"{FAMILIES['C']}: ")


@pytest.mark.parametrize("width,bar_cells", [(91, BAR_CELLS), (106, BAR_MIDDLE_CELLS)])
def test_bar_keeps_its_room_at_intermediate_widths(width, bar_cells):
    screen = render_grouped_frame(wide_notes_payload(), view="plain", unicode=False, width=width, expand=True)
    rows = [line for line in screen.splitlines() if re.match(r"^ ?cc C-7D {2,}", line)]
    assert len(rows) == 2
    for row in rows:
        bar = re.search(r"[#.|]+", row)
        assert bar and len(bar[0]) == bar_cells
        assert re.search(r"claude: .*[+]\d+", row)


@pytest.mark.parametrize("view,unicode", [("rich", True), ("rich", False), ("plain", False)])
@pytest.mark.parametrize("expand_mode", [False, True])
@pytest.mark.parametrize("height", [None, 40], ids=["append", "viewport"])
def test_pool_visibility_by_layout(view, unicode, expand_mode, height):
    payload = na_payload(selected_groups=["capacity"])
    observations = payload["groups"]["capacity"]["data"]["observations"]
    observations.extend([absent("ag", "A-7D"), absent("cx", "X-5H")])
    options = dict(view=view, unicode=unicode, width=140, height=height, expand=expand_mode)
    screen = render_grouped_frame(payload, **options)
    assert re.search(r"POOLS 2\b", screen)
    assert "(+" not in screen.split("POOLS 2", 1)[1].splitlines()[0]
    assert all(name not in screen for name in ("X-30D", "C-5H", "A-7D", "X-5H"))
    assert all(name in screen for name in ("C-7D", "X-7D"))
    gap = next(line for line in screen.splitlines() if line.startswith("gap vs "))
    assert "C-7D -23.0" in gap and "A-7D" not in gap


@pytest.mark.parametrize("expanded", [False, True])
def test_gap_skips_weekly_absence_even_after_it_ages(expanded):
    payload = na_payload(selected_groups=["capacity"])
    aged_absence = absent("ag", "A-7D")
    aged_absence.update(state="STALE", age_seconds=3600)
    payload["groups"]["capacity"]["data"]["observations"].append(aged_absence)
    screen = render_grouped_frame(payload, view="plain", width=120, expand=expanded)
    assert any("A-7D" in line and "aged" in line for line in screen.splitlines())
    gap = next(line for line in screen.splitlines() if line.startswith("gap vs "))
    assert "C-7D -23.0" in gap and "A-7D" not in gap


@pytest.mark.parametrize("view", ["plain", "rich"])
@pytest.mark.parametrize("expanded", [False, True])
def test_all_unavailable_pools_have_no_empty_tables_or_legend(view, expanded):
    payload = _payload([], [absent("ag", "A-7D")], selected_groups=["capacity"])
    screen = render_grouped_frame(payload, view=view, width=150, expand=expanded)
    assert "POOLS 0" in screen
    assert all(text not in screen for text in ("A-7D", "PACE(pp)", "PEER POOL", "gap vs ", "no quota evidence"))


def test_measured_child_is_visible_without_its_unavailable_parent():
    payload = na_payload()
    payload["groups"]["capacity"]["data"]["observations"] = [
        pool("cc", "C-5H", .30), absent("cc", "C-7D"), pool("cx", "X-7D", .42), absent("cx", "X-30D")]
    screen = render_grouped_frame(payload, view="plain", unicode=False, width=120, expand=True)
    quota = screen.split("QUOTA [", 1)[1]
    rows = [line for line in quota.splitlines() if "C-5H" in line]
    assert len(rows) == 1 and rows[0].startswith("cc C-5H")
    assert "C-7D" not in quota


def test_aged_and_unknown_pools_stay_visible():
    payload = full_payload()
    payload["selected_groups"] = ["capacity"]
    screen = render_grouped_frame(payload, view="plain", unicode=False, width=100)
    assert "F-7D" in screen and "A-7D" in screen
    assert "(X-30D n/a)" not in screen
