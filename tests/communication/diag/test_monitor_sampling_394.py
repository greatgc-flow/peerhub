"""#394: sampled sections say they are samples, PEERS lists every peer, one pool order and one date label, and the
cc F-7D window is limited by its parent C-7D in every view (effective left = min, reset = the nearer reset when the
parent is lower, state = the worse of the two)."""
from __future__ import annotations

import re
from copy import deepcopy
from typing import Any

import pytest

from peerhub.cli import monitor_legend, monitor_view as mv
from tests.communication.diag.test_monitor_activity_view import NOW, pool
from tests.communication.diag.test_monitor_expand_rich import block, detail_payload, eight_pools_payload, strip
from tests.communication.diag.test_monitor_pool_dependencies import payload as cc_payload, pool_rows


@pytest.fixture(autouse=True)
def colour_env(monkeypatch):
    monkeypatch.delenv("NO_COLOR", raising=False)


def render(frame: dict[str, Any], view: str, **kw: Any) -> str:
    kw.setdefault("width", 140)
    kw.setdefault("unicode", False)
    return strip(mv.render_grouped_frame(deepcopy(frame), view=view, expand=True, **kw))


# ---- 1 and 3: a sample says so -------------------------------------------------------------------------------------
def sampled_payload() -> dict[str, Any]:
    frame = detail_payload()
    groups = frame["groups"]
    groups["execution"]["data"].update(sample_size=44, truncated=True, sample_total=2863)
    groups["sessions"]["data"].update(sample_size=100, truncated=True, sample_total=2001)
    groups["usage"]["data"].update(sample_size=100, truncated=True, sample_total=2001)
    groups["context"]["data"].update(sample_size=100, truncated=True, sample_total=2001)
    return frame


@pytest.mark.parametrize("view", ["plain", "rich"])
@pytest.mark.parametrize("section,marker", [("execution", "sample 44/2,863"), ("sessions", "sample 100/2,001"),
                                           ("usage", "sample 100/2,001"), ("context", "sample 100/2,001")])
def test_a_sampled_section_head_carries_sample_n_of_m(view, section, marker):
    screen = render(sampled_payload(), view)
    assert marker in block(screen, section)[0]


def test_a_section_that_is_not_a_sample_has_no_sample_marker():
    assert "sample " not in "\n".join(line for line in render(detail_payload(), "rich").splitlines()
                                       if line.startswith(("-- ", "── ")))


@pytest.mark.parametrize("view", ["plain", "rich"])
def test_execution_counts_are_labelled_a_sample_next_to_the_running_asks(view):
    frame = sampled_payload()
    running = frame["groups"]["activity"]["data"]["counts"]["running"]
    screen = render(frame, view)
    row = next(line for line in screen.splitlines() if "Delivery certainty" in line)
    assert "sample" in row and "started 2" in row
    assert f"Running asks now: {running}" in screen  # the leases, not the sample


def test_a_complete_read_prints_neither_marker_nor_the_running_line():
    assert "Running asks now" not in render(detail_payload(), "rich")


def test_usage_and_context_without_a_sample_total_say_how_many_were_read():
    frame = sampled_payload()
    for name in ("usage", "context"):
        frame["groups"][name]["data"].pop("sample_total")
    screen = render(frame, "rich")
    head = block(screen, "usage")[0]
    assert "sample 100 " in head + " " and "100/" not in head


# ---- 2: PEERS ------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("view", ["plain", "rich"])
@pytest.mark.parametrize("total,expected", [(10, "showing 8 of 10 peers"), (None, "showing 8 peers; more are not shown")])
def test_a_capped_peers_list_says_how_many_are_shown(view, total, expected):
    frame = detail_payload()
    names = ["ag", "ca", "cc", "cx", "ga", "oa", "p1", "p2"]
    data = frame["groups"]["peers"]["data"]
    data["peers"] = [{"peer_id": name} for name in names]
    data.update(sample_size=8, truncated=True, peer_sample_truncated=True)
    if total:
        data["peers_total"] = total
    assert expected in render(frame, view)


@pytest.mark.parametrize("view", ["plain", "rich"])
def test_a_complete_peers_list_has_no_showing_marker(view):
    assert "showing" not in render(detail_payload(), view)


# ---- 4: QUOTA detail order ------------------------------------------------------------------------------------------
def first_seen(lines: list[str], pattern: str) -> list[str]:
    seen: list[str] = []
    for line in lines:
        match = re.match(pattern, line)
        if match and match[1] not in seen:
            seen.append(match[1])
    return seen


@pytest.mark.parametrize("view", ["plain", "rich"])
def test_quota_details_keep_the_most_urgent_peer_first(view):
    frame = eight_pools_payload()
    lines = render(frame, view).splitlines()
    quota = re.escape(monitor_legend.FRAME_SECTIONS["capacity"])
    start = next(i for i, line in enumerate(lines) if re.match(rf"(?:--|──) {quota}(?= |$)", line))
    end = next((i for i in range(start + 1, len(lines)) if re.match(r"(?:--|──) ", lines[i])), len(lines))
    assert first_seen(lines[start + 2:end], r"\s*(ag|cc|cx) ") == ["cx", "ag", "cc"]  # X-5H is out


# ---- 5: two dates, two labels --------------------------------------------------------------------------------------
@pytest.mark.parametrize("view", ["plain", "rich"])
def test_the_defaults_date_and_the_version_check_date_have_different_labels(view):
    frame = detail_payload()
    data = frame["groups"]["peers"]["data"]
    data.update(defaults_revision="2026-10-16.1", observed_at="2026-11-10",
                facts_report={"status": "needs_recheck", "reasons": ["ag: version_drift"], "checked_at": "2026-11-14"})
    screen = " ".join(render(frame, view, width=200).split())  # the frame is read on 2026-11-14
    assert "Model facts: profile defaults rev 2026-10-16.1 (2026-11-10, 4d ago)" in screen
    assert "version check 2026-11-14" in screen
    assert "checked 2026-11-10" not in screen


def test_an_ok_facts_report_uses_the_same_version_check_label():
    frame = detail_payload()
    frame["groups"]["peers"]["data"]["facts_report"] = {"status": "ok", "checked_at": "2026-11-18"}
    assert "Facts: ok (version check 2026-11-18)" in render(frame, "plain", width=200)


# ---- 6: n/a is the plan's answer -----------------------------------------------------------------------------------
def test_the_legend_says_that_na_is_the_plan_and_that_diag_quota_may_keep_an_older_value():
    assert "n/a plan no longer reports it (diag quota may keep an older value)" in " ".join(monitor_legend.MONITOR_LEGEND)
    assert "n/a plan no longer reports it" in " ".join(monitor_legend.MONITOR_LEGEND)


# ---- 7: the scoped weekly window is limited by its parent ----------------------------------------------------------
def scoped(parent: float, child: float, parent_reset: float = 19680, child_reset: float = 19680) -> dict[str, Any]:
    frame = cc_payload(parent, child)
    parent_obs, child_obs = frame["_human_observations"][:2]
    parent_obs.update(resets_at=NOW + parent_reset, reset_in_seconds=parent_reset)
    child_obs.update(resets_at=NOW + child_reset, reset_in_seconds=child_reset)
    return frame


def cells(screen: str, name: str) -> list[list[str]]:
    return [row.split() for row in pool_rows(screen, name)]


@pytest.mark.parametrize("view,expand", [("plain", False), ("rich", False), ("plain", True), ("rich", True)])
def test_parent_lower_the_child_shows_the_parent_left_the_nearer_reset_and_the_worse_state(view, expand):
    frame = scoped(.08, .26, parent_reset=19680, child_reset=90000)  # C-7D 8% (low) resets first; F-7D 26% (ok) later
    screen = strip(mv.render_grouped_frame(frame, view=view, expand=expand, unicode=False, width=160))
    rows = cells(screen, "F-7D")
    pools_shown = True  # #412: --expand (plain too) has POOLS on top of the QUOTA details
    assert len(rows) == (2 if expand else 1)
    for row in rows:
        assert "8%!" in row and "26%" not in row  # effective left = the lower of the two
        assert any(cell.rstrip("!").endswith("/5h28m") for cell in row) and not {"1d1h", "1d01h"} & set(row)  # the nearer reset: the parent's
        assert "low" in [word.lower() for word in row]  # the worse state of the two
    if pools_shown:
        assert "own 26%" in screen  # the pool's own number stays visible
    for row in cells(screen, "C-7D"):
        assert "8%!" in row


@pytest.mark.parametrize("view,expand", [("plain", False), ("rich", True)])
def test_parent_lower_with_the_childs_reset_nearer_keeps_the_childs_reset(view, expand):
    screen = strip(mv.render_grouped_frame(scoped(.08, .26, parent_reset=90000, child_reset=19680), view=view,
                                           expand=expand, unicode=False, width=160))
    assert all(any(cell.rstrip("!").endswith("/5h28m") for cell in row) for row in cells(screen, "F-7D"))


@pytest.mark.parametrize("view,expand", [("plain", False), ("rich", False), ("plain", True), ("rich", True)])
def test_parent_higher_the_child_keeps_its_own_left_reset_and_state(view, expand):
    frame = scoped(.80, .26, parent_reset=19680, child_reset=90000)
    screen = strip(mv.render_grouped_frame(frame, view=view, expand=expand, unicode=False, width=160))
    for row in cells(screen, "F-7D"):
        assert "26%" in row and any(word.endswith("/1d01h") for word in row)
        assert "ok" in [word.lower() for word in row] or "insuff" in [word.lower() for word in row]
    assert "own" not in " ".join(pool_rows(screen, "F-7D"))


@pytest.mark.parametrize("view", ["plain", "rich"])
def test_both_views_show_the_same_tree_parent_then_child(view):
    for frame in (scoped(.08, .26), scoped(.80, .26)):
        screen = strip(mv.render_grouped_frame(frame, view=view, expand=True, unicode=False, width=160))
        quota = screen[screen.index("QUOTA"):]
        assert quota.index("C-7D") < quota.index("F-7D")
        assert re.search(r"(`-|\|-) F-7D", quota)


def test_the_suggestion_uses_the_effective_values():
    low = mv.render_grouped_frame(scoped(.03, .26), view="plain", unicode=False, width=160)
    assert "top=cc.fable" not in low  # the parent is below the 5% floor, whatever the child says
    high = mv.render_grouped_frame(scoped(.80, .26), view="plain", unicode=False, width=160)
    assert "top=cc.fable" in high


def test_an_aged_parent_never_limits_the_child():
    frame = scoped(.08, .26)
    frame["_human_observations"][0].update(state="STALE", age_seconds=600)
    screen = strip(mv.render_grouped_frame(frame, view="plain", unicode=False, width=160))
    assert all("26%" in row for row in cells(screen, "F-7D"))


def test_rendering_a_limited_child_does_not_change_the_payload():
    frame = scoped(.08, .26)
    before = deepcopy(frame)
    mv.render_grouped_frame(frame, view="rich", expand=True, width=160)
    assert frame == before


def test_the_legend_defines_the_limit():
    screen = " ".join(strip(mv.render_grouped_frame(scoped(.08, .26), view="rich", expand=True, unicode=False,
                                                    width=160)).split())
    assert "limited by C-7D (own 26%)" in screen  # M5
    assert "LEFT and the bar keep this pool's own remaining quota" not in screen
