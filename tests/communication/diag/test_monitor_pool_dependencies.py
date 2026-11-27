"""Monitor-only pool dependencies preserve the provider's own measurements and read-only payload."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import re
import tomllib
from typing import Any

import pytest

from peerhub.cli import monitor_layout, monitor_view as mv
from tests.communication.diag.test_monitor_activity_view import NOW, pool
from tests.communication.diag.test_monitor_plain_golden import _payload


@pytest.fixture(autouse=True)
def colour_env(monkeypatch):
    monkeypatch.delenv("NO_COLOR", raising=False)


def payload(parent: float = 0.0, child: float = .26) -> dict[str, Any]:
    """The shipped Claude profile membership, not an independent Fable-only fixture."""
    defaults = tomllib.loads((Path(__file__).resolve().parents[3] /
                              "peerhub/config_data/model-defaults.toml").read_text(encoding="utf-8"))
    profiles = [{"profile": name, "fields": {"pools": {"value": settings["pools"]}}}
                for name, settings in defaults["profiles"].items() if name.startswith("cc.")]
    absent = pool("cc", "C-5H", None)
    absent.update(state="ABSENT", stored_state="ABSENT", measurements=None, last_measurements=None,
                  reason="not reported by provider; plan=pro")
    observations = [pool("cc", "C-7D", parent, pace=3, reset=19680),
                    pool("cc", "F-7D", child, pace=-23, reset=19680), absent]
    for item in observations[:2]:
        item["reset_in_seconds"] = 19680
    result = _payload([], observations, configured_profiles=profiles)
    result["selected_groups"] = ["capacity"]
    result["_human_observations"] = observations
    return result


def pool_rows(screen: str, name: str) -> list[str]:
    text = re.sub(r"\x1b\[[0-9;]*m", "", screen)
    pattern = rf"^\s*(?:cc\s+)?(?:(?:└─|├─|`-|\|-)\s+)?{re.escape(name)}\s+"
    return [line for line in text.splitlines() if re.match(pattern, line)]


@pytest.mark.parametrize("view,expand", [("plain", False), ("rich", False), ("rich", True)])
@pytest.mark.parametrize("unicode", [False, True])
def test_fable_is_nested_and_blocked_by_fresh_common_pool_showing_the_parents_left_not_rewriting_the_payload(
        view, expand, unicode):
    frame = payload()
    before = deepcopy(frame)
    screen = mv.render_grouped_frame(frame, view=view, expand=expand, unicode=unicode, color=True, width=160)
    rows = pool_rows(screen, "F-7D")
    assert len(rows) == (2 if expand else 1)  # POOLS and QUOTA use the same dependency state
    # #394: effective left = the lower of the two (0% of the parent); the pool's own 26% stays in the POOLS note
    assert all("blocked" in row.split() and "0%" in row.split() and "OUT/5h28m" in row.split() for row in rows)
    assert "(own 26%)" in rows[0]
    assert all(any(branch in row for branch in ("└─", "├─", "`-", "|-")) for row in rows)
    assert all("0%" in row.split() and "out" in row.split() for row in pool_rows(screen, "C-7D"))
    assert "C-7D out" in rows[0]
    if view == "rich":
        assert f"\x1b[{monitor_layout.RICH_SGR['out']}m{monitor_layout.RICH_GLYPHS[unicode]['blocked']} blocked" in screen
    assert frame == before


@pytest.mark.parametrize("parent,child,child_state", [(.8, .26, "ok"), (.8, 0.0, "out"), (0.0, 0.0, "out")])
def test_dependency_keeps_each_pools_own_state_and_remaining_when_not_a_parent_only_block(parent, child, child_state):
    frame = payload(parent, child)
    screen = mv.render_grouped_frame(frame, view="rich", expand=True, width=160)
    assert all(child_state in row.split() and f"{round(child * 100)}%" in row.split()
               for row in pool_rows(screen, "F-7D"))
    assert "blocked" not in " ".join(pool_rows(screen, "F-7D"))


@pytest.mark.parametrize("case", ["stale", "unknown", "reset", "missing", "other_account"])
def test_ambiguous_or_noncurrent_parent_never_claims_a_current_block(case):
    frame = payload()
    observations = frame["_human_observations"]
    parent = observations[0]
    if case == "stale":
        parent.update(state="STALE", age_seconds=600)
    elif case == "unknown":
        parent.update(state="UNKNOWN", measurements=None, last_measurements=None)
    elif case == "reset":
        parent["resets_at"] = NOW - 1
    elif case == "missing":
        observations.remove(parent)
    else:
        parent["pool_ref"] = "quota:other:C-7D"
    screen = mv.render_grouped_frame(frame, view="rich", expand=True, width=160)
    rows = pool_rows(screen, "F-7D")
    assert all("blocked" not in row and "26%" in row.split() for row in rows)
    if case in {"missing", "other_account"}:
        assert "cc F-7D" in rows[0]  # no invented parent in POOLS


def test_independent_profile_override_and_equal_consumer_sets_do_not_invent_parent():
    for pools in (["F-7D"], ["F-7D", "C-7D"]):
        frame = payload()
        frame["configured_profiles"] = [{"profile": "cc.fable", "fields": {"pools": {"value": pools}}}]
        screen = mv.render_grouped_frame(frame, view="rich", width=100)
        row = pool_rows(screen, "F-7D")[0]
        assert "cc F-7D" in row and "blocked" not in row


@pytest.mark.parametrize("elapsed,blocked", [(0, True), (289, True), (290, False), (19680, False)])
def test_dependency_is_recomputed_as_each_redraw_ages_or_resets_the_parent(elapsed, blocked):
    frame = payload()
    frame["display_elapsed_seconds"] = elapsed
    screen = mv.render_grouped_frame(frame, view="rich", expand=True, width=160)
    assert all(("blocked" in row.split()) == blocked for row in pool_rows(screen, "F-7D"))


@pytest.mark.parametrize("view,expand", [("plain", False), ("rich", False), ("plain", True), ("rich", True)])
@pytest.mark.parametrize("reason,expected", [("not reported by provider; plan=pro", "not reported"),
                                            ("disabled by provider", "disabled by provider")])
def test_hidden_unavailable_pools_preserve_blocked_dependency(view, expand, reason, expected):
    frame = payload()
    frame["_human_observations"][2]["reason"] = reason
    omitted = deepcopy(frame["_human_observations"][2])
    omitted.update(pool_ref="quota:cc:C-30D")
    frame["_human_observations"].append(omitted)
    before = deepcopy(frame)
    hidden = mv.render_grouped_frame(frame, view=view, expand=expand, width=180)
    assert expected not in hidden
    assert "C-30D" not in hidden
    if view == "rich" or not expand:
        assert "C-7D out" in hidden and "blocked" in pool_rows(hidden, "F-7D")[0]
    assert frame == before


@pytest.mark.parametrize("height", [7, 10, 16, 24])
@pytest.mark.parametrize("width", [40, 80, 100])
def test_short_frames_never_leave_a_scoped_child_without_its_parent(width, height):
    frame = payload()
    screen = mv.render_grouped_frame(frame, view="rich", width=width, height=height)
    assert len(screen.splitlines()) < height
    assert all(monitor_layout.cell_width(line) < width for line in screen.splitlines())
    if pool_rows(screen, "F-7D"):
        assert pool_rows(screen, "C-7D")


def test_fable_exhaustion_prioritizes_its_common_pool_group():
    frame = payload(.8, 0.0)
    frame["_human_observations"].append(pool("ag", "A-7D", .05))
    screen = mv.render_grouped_frame(frame, view="rich", width=100)
    assert screen.index(pool_rows(screen, "C-7D")[0]) < screen.index(pool_rows(screen, "F-7D")[0]) < screen.index("ag A-7D")


@pytest.mark.parametrize("view", ["plain", "rich"])
@pytest.mark.parametrize("measured", [False, True])
def test_expanded_peer_quota_keeps_summary_without_unavailable_window_notes(view, measured):
    frame = payload(.8)
    frame["selected_groups"].append("peers")
    quota = ({"pool_ref": "quota:cc:C-7D", "state": "MEASURED", "remaining_fraction": .8} if measured else
             {"pool_ref": "not_reported", "state": "UNKNOWN", "remaining_fraction": None})
    frame["groups"]["peers"]["data"]["peers"] = [{"peer_id": "cc", "quota": quota}]
    if not measured:
        frame["_human_peer_pools"] = {"cc": ["C-5H"]}
    screen = mv.render_grouped_frame(frame, view=view, expand=True, width=200)
    peer_row = next(line for line in screen.splitlines() if "cc  quota:" in line)
    assert ("26% (F-7D)" if measured else "? (not reported)") in peer_row
    assert "no 5h window" not in screen


@pytest.mark.parametrize("view", ["plain", "rich"])
def test_expand_does_not_turn_an_unavailable_peer_read_into_an_empty_read(view):
    frame = payload()
    frame["selected_groups"].append("peers")
    frame["groups"]["peers"] = {"status": "ERROR", "data": None}
    screen = mv.render_grouped_frame(frame, view=view, expand=True, width=180)
    assert "data unavailable" in screen and "No peer evidence" not in screen


def test_scoped_weekly_group_does_not_drop_an_observed_short_window_of_its_own():
    frame = payload(.8)
    frame["_human_observations"].append(pool("cc", "F-5H", .05))
    frame["_human_observations"].append(pool("ag", "A-7D", .2))
    screen = mv.render_grouped_frame(frame, view="rich", expand=True, width=160)
    top = "\n".join(screen.split("\ngap vs ", 1)[0].splitlines()[3:])  # the POOLS rows: after line 1, the title and the DB line
    assert "F-5H" in top and "└─ F-5H" in top  # #400: the n/a C-5H is no sibling row, so F-7D is its parent's last child
    assert top.index("cc C-7D") < top.index("F-5H") < top.index("ag A-7D")
    assert "5%" in next(line for line in top.splitlines() if "F-5H" in line)


def test_another_accounts_same_named_parent_does_not_break_the_cap():
    """The parent is matched by its account-scoped pool ref, so a second account's C-7D changes nothing (0.28.0 review)."""
    base = mv.render_grouped_frame(payload(), view="rich", expand=True, width=160)
    frame = payload()
    frame["_human_observations"].append({**frame["_human_observations"][0], "pool_ref": "quota:other:C-7D"})
    screen = mv.render_grouped_frame(frame, view="rich", expand=True, width=160)
    assert pool_rows(screen, "F-7D")[0] == pool_rows(base, "F-7D")[0]


@pytest.mark.parametrize("order", ["strict_first", "strict_last"])
def test_a_second_peer_reporting_the_same_parent_keeps_the_strictest_reading(order):
    """#498: the same account-scoped C-7D from a second peer is one parent; its lower reading still caps F-7D."""
    base = mv.render_grouped_frame(payload(), view="rich", expand=True, width=160)
    frame = payload()
    observations = frame["_human_observations"]
    loose = {**observations[0], "observation_id": "cc2-C-7D", "peer_id": "cc2",
             "measurements": {"remaining_fraction": .8}, "last_measurements": {"remaining_fraction": .8}}
    observations.insert(0 if order == "strict_last" else 1, loose)
    screen = mv.render_grouped_frame(frame, view="rich", expand=True, width=160)
    assert all("blocked" in row.split() for row in pool_rows(screen, "F-7D"))
    assert pool_rows(screen, "F-7D")[0] == pool_rows(base, "F-7D")[0]


def test_the_consumers_of_every_report_establish_the_dependency_before_the_reading_is_picked():
    """#498 review: the strict report may come from a peer without the consumer list; F-7D is still capped."""
    from peerhub.extensions.quota_windows import scoped_weekly_parents
    loose = {"pool_ref": "quota:cc:C-7D", "profiles": ("cc.effort", "cc.fable"), "retired": False, "left": 80}
    strict = {"pool_ref": "quota:cc:C-7D", "profiles": (), "retired": False, "left": 0}
    child = {"pool_ref": "quota:cc:F-7D", "profiles": ("cc.fable",), "retired": False, "left": 26}
    twin = {**child}
    parents = scoped_weekly_parents([loose, strict, child, twin], strictness=lambda row: row["left"])
    assert parents == {id(child): strict, id(twin): strict}  # a child reported twice keeps the cap too


def test_a_parent_reading_that_cannot_cap_never_wins_over_one_that_can():
    """#498 review: a stale 0% report loses to a current 5% report of the same parent."""
    from peerhub.extensions.quota_read_model import project_pool_rows
    from tests.communication.diag.test_monitor_activity_view import NOW
    from peerhub.extensions.quota_read_model import monitor_reading
    frame = payload()
    current, child = frame["_human_observations"][0], frame["_human_observations"][1]
    current = {**current, "measurements": {"remaining_fraction": .05}, "last_measurements": {"remaining_fraction": .05}}
    stale = {**current, "peer_id": "cc2", "age_seconds": 10_000, "measurements": {"remaining_fraction": 0.0},
             "last_measurements": {"remaining_fraction": 0.0}}
    readings = [{"peer": str(item["peer_id"]), "pool": str(item["pool_ref"]).rpartition(":")[2],
                 "pool_ref": str(item["pool_ref"]), "reading": monitor_reading(item, NOW, 0),
                 "profiles": (), "retired": False} for item in (stale, current, child)]
    rows = project_pool_rows(readings, NOW, require_consumers=False)  # type: ignore[arg-type]
    assert rows[1].own_health["stale"] is False and rows[0].own_health["stale"] is True
    assert rows[2].parent is readings[1]
