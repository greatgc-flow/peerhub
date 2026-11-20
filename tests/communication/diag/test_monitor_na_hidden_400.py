"""#400: a pool whose state is n/a is never drawn as a row in POOLS or QUOTA, in any view.

The head count is the rows shown. A hidden n/a pool is named after the peer's row in QUOTA (`(C-5H n/a)`); the suggestion
line, PEERS, `--show-quota-notes` and the JSON are untouched. An aged, unknown or reset pool is not n/a and stays."""
from __future__ import annotations

import re
from typing import Any

import pytest

from peerhub.cli.monitor_view import render_grouped_frame
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


@pytest.mark.parametrize("view,unicode", [("rich", True), ("rich", False), ("plain", False)])
@pytest.mark.parametrize("notes", [False, True])
def test_compact_pools_never_draw_an_na_pool(view, unicode, notes):
    screen = render_grouped_frame(na_payload(), view=view, unicode=unicode, width=100, height=None,
                                  show_quota_notes=notes)
    assert "X-30D" not in screen and "C-5H" not in screen and "n/a" not in screen.replace("n/a plan no longer", "")
    assert re.search(r"POOLS 2\b", screen)
    assert "C-7D" in screen and "X-7D" in screen


@pytest.mark.parametrize("view,unicode", [("rich", True), ("plain", False)])
def test_the_suggestion_still_skips_an_na_pool(view, unicode):
    screen = render_grouped_frame(na_payload(), view=view, unicode=unicode, width=100, height=None)
    assert "normal=cx.effort|cc.effort" in screen or "normal=cc.effort|cx.effort" in screen


def test_plain_expand_quota_names_an_na_pool_after_the_peers_row():
    screen = render_grouped_frame(na_payload(), view="plain", unicode=False, width=120, expand=True)
    rows = [line for line in screen.splitlines() if re.search(r"\b[CX]-\d+[HD]\b", line)]
    assert not any(re.match(r"^\s*(?:(?:cc|cx)\s+)?(?:\S+\s)?(C-5H|X-30D)\b", row) for row in rows)
    assert any(row.startswith("cc") and "C-7D" in row and row.rstrip().endswith("(C-5H n/a)") for row in rows)
    assert any(row.startswith("cx") and "X-7D" in row and row.rstrip().endswith("(X-30D n/a)") for row in rows)


def test_rich_expand_draws_no_na_row_and_counts_the_rows_shown():
    screen = expand(na_payload(), width=120)
    plain = strip(screen)
    assert re.search(r"POOLS 2\b", plain)
    assert not any(re.match(r"^\s*(?:(?:cc|cx)\s+)?\S*\s*(C-5H|X-30D)\b.*\bn/a\b", line) for line in plain.splitlines())
    quota = block(screen, "QUOTA")
    assert any("C-7D" in line and line.rstrip().endswith("(C-5H n/a)") for line in quota)
    assert any("X-7D" in line and line.rstrip().endswith("(X-30D n/a)") for line in quota)


def test_show_quota_notes_keeps_the_full_list():
    screen = render_grouped_frame(na_payload(), view="plain", unicode=False, width=120, expand=True,
                                  show_quota_notes=True)
    assert "cx X-30D n/a not reported (plan prolite)" in screen and "cc C-5H n/a not reported (plan prolite)" in screen


@pytest.mark.parametrize("notes", [False, True])
def test_retired_pools_join_the_peers_row_suffix_and_only_the_notes_add_a_line(notes):
    frame = na_payload()
    observations = frame["groups"]["capacity"]["data"]["observations"]
    for item in observations:
        if item["pool_ref"].endswith(("C-5H", "X-30D")):
            item.update(display_retired=True, display_plan="prolite")
    screen = render_grouped_frame(frame, view="plain", unicode=False, width=120, expand=True, show_quota_notes=notes)
    rows = [line for line in screen.splitlines() if re.search(r"\b[CX]-\d+[HD]\b", line)]
    assert any(row.startswith("cx") and row.rstrip().endswith("(X-30D n/a)") for row in rows)
    assert any(row.startswith("cc") and row.rstrip().endswith("(C-5H n/a)") for row in rows)
    lines = [line for line in screen.splitlines() if line.startswith(("cx X-30D n/a", "cc C-5H n/a"))]
    assert len(lines) == (2 if notes else 0)  # a separate line only with --show-quota-notes


def test_an_na_parent_passes_its_child_to_the_peers_first_row():
    payload = na_payload()
    payload["groups"]["capacity"]["data"]["observations"] = [
        pool("cc", "C-5H", .30), absent("cc", "C-7D"), pool("cx", "X-7D", .42), absent("cx", "X-30D")]
    screen = render_grouped_frame(payload, view="plain", unicode=False, width=120, expand=True)
    lines = screen.splitlines()
    rows = [line for line in lines[next(i for i, line in enumerate(lines) if "QUOTA [" in line):]  # the QUOTA details
            if re.search(r"\bC-\d+[HD]\b", line)]
    assert len(rows) == 1 and rows[0].startswith("cc") and "C-5H" in rows[0] and rows[0].rstrip().endswith("(C-7D n/a)")


def test_aged_and_unknown_pools_stay_visible():
    screen = render_grouped_frame(full_payload(), view="plain", unicode=False, width=100, height=None)
    assert "F-7D" in screen and "A-7D" in screen and "X-30D" not in screen
