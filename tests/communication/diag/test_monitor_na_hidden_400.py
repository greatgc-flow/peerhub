"""Compact hides unavailable windows and expand explains them."""
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
from peerhub.cli.monitor_legend import POOL_LEGEND
from peerhub.cli.monitor_quota_view import HIDDEN_QUOTA_WINDOWS
from peerhub.cli.monitor_view import render_grouped_frame
from peerhub.extensions.observation_model import epoch_to_iso
from peerhub.extensions.quota_windows import FAMILIES
from tests.communication.diag.test_monitor_activity_view import NOW, pool
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


@pytest.mark.parametrize("age,reset", [(12 * 86400, -60), (31 * 86400, 86400)])
@pytest.mark.parametrize("expanded", [False, True])
def test_expired_reading_is_retired_and_does_not_age_header(age, reset, expanded):
    expired = pool("cx", "X-30D", .42, reset=reset)
    expired.update(state="STALE", age_seconds=age)
    payload = _payload([], [expired, pool("ag", "G-7D", .64)], selected_groups=["capacity"], read_at=NOW)
    screen = render_grouped_frame(payload, view="plain", width=100, expand=expanded)
    assert ("X-30D" in screen) == expanded
    assert ("cx X-30D n/a (quota window expired)" in screen) == expanded
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
    assert ("X-30D" in screen) == expanded
    assert ("cx X-30D n/a (quota window expired)" in screen) == expanded
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
    assert ("C-7D n/a" in screen) == expanded


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
    screen = strip(render_grouped_frame(payload, view=view, width=190, expand=True))
    header = next(line for line in screen.splitlines() if "POOLS 4 (+4 n/a) " in line)
    left_end = header.index("LEFT") + len("LEFT")
    for name, note in notes.items():
        rows = [line for line in screen.splitlines()
                if re.match(rf"^\s*(?:\w+ +)?(?:[└├]─ )?{name} {{2,}}", line)]
        assert len(rows) == 2, (name, screen)
        family = FAMILIES[name.split("-")[0]]
        assert all(row.index(f"{family}: ") < row.index(note) for row in rows)
        assert rows[0].index("%") + 1 == left_end
        assert re.search(re.escape(note) + r" +\d+s$", rows[-1]), rows[-1]
        assert screen.count(note) == len(rows)
    assert all(cell_width(line) < 190 for line in screen.splitlines())
    narrow = render_grouped_frame(payload, view="plain", width=65, unicode=False, expand=True)
    assert re.search(r"(?m)^ *\(X-5H,X-30D n/a\)$", narrow)
    assert re.search(r"(?m)^ *limited by C-7D \(own 14%\)$", narrow)


def test_rich_wide_header_aligns_with_cells_for_two_digit_pool_count():
    payload = na_payload(selected_groups=["capacity"])
    payload["groups"]["capacity"]["data"]["observations"].extend(
        absent("cx", f"X-{day}D") for day in range(10, 17))
    screen = strip(render_grouped_frame(payload, view="rich", width=190, expand=True))
    header, row, *_ = block(screen, "pools")
    assert header.startswith("── POOLS 2 (+9 n/a) ")
    glyphs = RICH_GLYPHS[True]
    bar = re.search(r"[█░|]+", row)
    assert bar and len(bar[0]) == BAR_MAX_CELLS
    assert header[bar.start():bar.end()] == glyphs["rule"] * BAR_MAX_CELLS
    assert header.index("STATE") == row.index(glyphs["ok"])
    assert header.index("LEFT") + len("LEFT") == row.index("%") + 1
    assert header.index("PACE(pp)") == row.index("-20.0")
    assert header.index("FAMILY") == row.index(f"{FAMILIES['C']}: ")


@pytest.mark.parametrize("width,bar_cells", [(96, BAR_CELLS), (106, BAR_MIDDLE_CELLS)])
def test_bar_keeps_its_room_when_family_and_notes_compete(width, bar_cells):
    screen = render_grouped_frame(wide_notes_payload(), view="plain", unicode=False, width=width, expand=True)
    rows = [line for line in screen.splitlines() if re.match(r"^ ?cc C-7D {2,}", line)]
    assert len(rows) == 2
    for row in rows:
        bar = re.search(r"[#.|]+", row)
        assert bar and len(bar[0]) == bar_cells
        assert re.search(r"claude: .*[+]\d+", row)
    assert re.search(r"(?m)^ *\(C-5H n/a\)$", screen)


@pytest.mark.parametrize("view,unicode", [("rich", True), ("rich", False), ("plain", False)])
@pytest.mark.parametrize("expand_mode", [False, True])
@pytest.mark.parametrize("height", [None, 40], ids=["append", "viewport"])
def test_pool_visibility_by_layout(view, unicode, expand_mode, height):
    payload = na_payload(selected_groups=["capacity"])
    observations = payload["groups"]["capacity"]["data"]["observations"]
    observations.append(absent("ag", "A-7D"))
    options = dict(view=view, unicode=unicode, width=140, height=height, expand=expand_mode)
    screen = render_grouped_frame(payload, **options)
    assert re.search(r"POOLS 2 \(\+3 n/a\)", screen)
    assert all((pool in screen) == expand_mode for pool in ("X-30D", "C-5H", "A-7D"))
    assert ("ag A-7D n/a not reported (plan prolite)" in screen) == expand_mode
    assert all(pool in screen for pool in ("C-7D", "X-7D"))


@pytest.mark.parametrize("view", ["plain", "rich"])
def test_all_unavailable_pools_have_no_empty_tables_or_legend(view):
    payload = _payload([], [absent("ag", "A-7D")], selected_groups=["capacity"])
    hidden = render_grouped_frame(payload, view=view, width=150)
    assert re.search(r"POOLS 0 \(\+1 n/a\)[^\n]*$", hidden, re.MULTILINE)
    assert HIDDEN_QUOTA_WINDOWS in hidden
    assert all(text not in hidden for text in ("A-7D", "PACE(pp)", "PEER POOL", "gap vs ", "no quota evidence"))

    shown = render_grouped_frame(payload, view=view, width=150, expand=True)
    assert re.search(r"POOLS 0 \(\+1 n/a\)", shown)
    assert "(A-7D n/a)" in shown
    assert "ag A-7D n/a not reported (plan prolite)" in shown
    assert POOL_LEGEND in " ".join(shown.split())


def test_plain_expand_quota_names_an_na_pool_after_the_peers_row():
    screen = render_grouped_frame(na_payload(), view="plain", unicode=False, width=120, expand=True)
    rows = [line for line in screen.split("QUOTA [", 1)[1].splitlines() if re.search(r"\b[CX]-\d+[HD]\b", line)]
    assert not any(re.match(r"^\s*(?:(?:cc|cx)\s+)?(?:\S+\s)?(C-5H|X-30D)\b", row) for row in rows if "not reported" not in row)
    assert any(row.startswith("cc") and "C-7D" in row and "(C-5H n/a)" in row for row in rows)
    assert any(row.startswith("cx") and "X-7D" in row and "(X-30D n/a)" in row for row in rows)


def test_rich_expand_counts_unavailable_windows_and_names_them_in_quota():
    screen = expand(na_payload(), width=120)
    plain = strip(screen)
    assert re.search(r"POOLS 2 \(\+2 n/a\)", plain)
    assert not any(re.match(r"^\s*(?:(?:cc|cx)\s+|[└├]─ )?(C-5H|X-30D) {2,}", line) for line in plain.splitlines() if "not reported" not in line)
    quota = block(screen, "capacity")
    for parent, note in (("C-7D", "(C-5H n/a)"), ("X-7D", "(X-30D n/a)")):
        index = next(i for i, line in enumerate(quota) if re.match(rf"^\s*(?:cc|cx) +{parent} +", line))
        assert note in " ".join(quota[index:index + 2])


def test_show_quota_notes_keeps_the_full_list():
    screen = render_grouped_frame(na_payload(), view="plain", unicode=False, width=120, expand=True)
    assert "cx X-30D n/a not reported (plan prolite)" in screen and "cc C-5H n/a not reported (plan prolite)" in screen


def test_retired_pools_join_the_peers_row_suffix_and_have_a_reason():
    frame = na_payload()
    observations = frame["groups"]["capacity"]["data"]["observations"]
    for item in observations:
        if item["pool_ref"].endswith(("C-5H", "X-30D")):
            item.update(display_retired=True, display_plan="prolite")
    screen = render_grouped_frame(frame, view="plain", unicode=False, width=120, expand=True)
    rows = [line for line in screen.split("QUOTA [", 1)[1].splitlines() if re.search(r"\b[CX]-\d+[HD]\b", line)]
    assert any(row.startswith("cx") and "(X-30D n/a)" in row for row in rows)
    assert any(row.startswith("cc") and "(C-5H n/a)" in row for row in rows)
    lines = [line for line in screen.splitlines() if line.startswith(("cx X-30D n/a", "cc C-5H n/a"))]
    assert len(lines) == 2 and all("not reported by plan prolite" in line for line in lines)


def test_an_na_parent_passes_its_child_to_the_peers_first_row():
    payload = na_payload()
    payload["groups"]["capacity"]["data"]["observations"] = [
        pool("cc", "C-5H", .30), absent("cc", "C-7D"), pool("cx", "X-7D", .42), absent("cx", "X-30D")]
    screen = render_grouped_frame(payload, view="plain", unicode=False, width=120, expand=True)
    lines = screen.splitlines()
    rows = [line for line in lines[next(i for i, line in enumerate(lines) if "QUOTA [" in line):]  # the QUOTA details
            if re.search(r"\bC-\d+[HD]\b", line) and "not reported" not in line]
    assert len(rows) == 1 and rows[0].startswith("cc") and "C-5H" in rows[0] and "(C-7D n/a)" in rows[0]


def test_aged_and_unknown_pools_stay_visible():
    payload = full_payload()
    payload["selected_groups"] = ["capacity"]
    screen = render_grouped_frame(payload, view="plain", unicode=False, width=100)
    assert "F-7D" in screen and "A-7D" in screen
    assert "(X-30D n/a)" not in screen
