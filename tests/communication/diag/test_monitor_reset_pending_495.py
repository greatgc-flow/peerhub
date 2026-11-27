"""A just-reset short window keeps a placeholder row under its weekly parent in both views (#495)."""
from __future__ import annotations

import re

import pytest

from peerhub.cli.monitor_view import render_grouped_frame
from tests.communication.diag.test_monitor_activity_view import pool
from tests.communication.diag.test_monitor_expand_rich import strip
from tests.communication.diag.test_monitor_plain_golden import _payload

RESET_AGO = -180.0  # the 5H window reset three minutes before the reading


def frame(*extra, reset=RESET_AGO):
    return _payload([], [pool("ag", "G-5H", .3, reset=reset), pool("ag", "G-7D", .5), *extra],
                    selected_groups=["capacity"])


def lines_of(screen):
    return strip(screen).splitlines()


@pytest.mark.parametrize("expand", [False, True])
@pytest.mark.parametrize("reset", [RESET_AGO, 0], ids=["after-reset", "at-reset"])
@pytest.mark.parametrize("view,unicode,branch,dot", [
    ("plain", True, "└─", "·"), ("plain", False, "`-", "|"), ("rich", True, "└─", "·"), ("rich", False, "`-", "|")])
def test_placeholder_row_follows_its_weekly_parent_without_bar_or_pace(view, unicode, branch, dot, expand, reset):
    screen = render_grouped_frame(frame(reset=reset), view=view, unicode=unicode, width=120, expand=expand)
    lines = lines_of(screen)
    (index,) = [i for i, line in enumerate(lines) if "refresh pending" in line]
    assert re.fullmatch(rf"\s+{re.escape(branch)} G-5H reset {re.escape(dot)} refresh pending", lines[index])
    parent = next(i for i, line in enumerate(lines) if re.search(r"\bag\s+G-7D\b", line))
    assert index == parent + 1
    assert "50%" in lines[parent] and "G-5H" not in lines[parent] and "limited by" not in lines[parent]
    assert re.search(r"POOLS 2\b", screen)
    assert screen.count("G-5H") == 1


def test_expand_shows_the_placeholder_only_in_pools():
    screen = render_grouped_frame(frame(), view="plain", width=120, expand=True)
    pools, quota = screen.split("QUOTA [", 1)
    assert "(G-5H n/a)" not in pools and pools.count("refresh pending") == 1
    assert "refresh pending" not in quota
    assert "G-5H" not in quota


def test_placeholder_survives_a_narrow_terminal():
    screen = render_grouped_frame(frame(), view="plain", unicode=False, width=60)
    assert "G-5H reset | refresh pending" in screen


def test_a_new_reading_replaces_the_placeholder():
    payload = _payload([], [pool("ag", "G-5H", .9), pool("ag", "G-7D", .5)], selected_groups=["capacity"])
    screen = render_grouped_frame(payload, view="plain", width=120, expand=True)
    assert "refresh pending" not in screen and re.search(r"└─ G-5H\s+ok\b", screen)


@pytest.mark.parametrize("expand", [False, True])
def test_a_window_without_a_weekly_parent_stays_hidden(expand):
    payload = _payload([], [pool("ag", "G-5H", .3, reset=RESET_AGO), pool("cx", "X-7D", .42)],
                       selected_groups=["capacity"])
    screen = render_grouped_frame(payload, view="plain", width=120, expand=expand)
    assert "refresh pending" not in screen


@pytest.mark.parametrize("expand", [False, True])
def test_reset_placeholder_matches_the_weekly_parents_account(expand):
    child = {**pool("ag", "G-5H", .3, reset=RESET_AGO), "pool_ref": "quota:ag:account-a:G-5H"}
    other_parent = {**pool("ag", "G-7D", .8, pace=-10), "pool_ref": "quota:ag:account-b:G-7D"}
    own_parent = {**pool("ag", "G-7D", .5, pace=10), "pool_ref": "quota:ag:account-a:G-7D"}
    options = {"view": "plain", "width": 120, "expand": expand}
    screen = render_grouped_frame(_payload([], [child, other_parent], selected_groups=["capacity"]), **options)
    assert "refresh pending" not in screen

    payload = _payload([], [child, other_parent, own_parent], selected_groups=["capacity"])
    lines = render_grouped_frame(payload, **options).splitlines()
    (index,) = [i for i, line in enumerate(lines) if "refresh pending" in line]
    assert "80%" in lines[index - 2] and "50%" in lines[index - 1]


@pytest.mark.parametrize("expand", [False, True])
def test_short_window_retired_by_age_has_no_reset_placeholder(expand):
    window_seconds = 5 * 3600
    child = {**pool("ag", "G-5H", .3, reset=60), "age_seconds": window_seconds + 1}
    payload = _payload([], [child, pool("ag", "G-7D", .5)], selected_groups=["capacity"])
    screen = render_grouped_frame(payload, view="plain", width=120, expand=expand)
    assert "refresh pending" not in screen
    assert "G-5H" not in screen
