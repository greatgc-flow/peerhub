"""The compact and expanded monitor share the same frame section order."""
from __future__ import annotations

import pytest

from peerhub.cli.monitor_view import FRAME_HEADS, FRAME_SECTIONS, FRAME_TRAILING_ROWS, render_grouped_frame
from tests.communication.diag.test_monitor_expand_rich import detail_payload, heads


def _all_sections_payload():
    payload = detail_payload()
    activity = payload["groups"]["activity"]["data"]
    activity["leads"] = activity["leads"][:1]  # leave room for LEADS within the compact line budget
    activity["asks"] = [activity["asks"][0], activity["asks"][-1]]
    return payload


@pytest.mark.parametrize("view", ["plain", "rich"])
@pytest.mark.parametrize("expand", [False, True], ids=["compact", "expand"])
def test_full_frame_section_order(view: str, expand: bool) -> None:
    screen = render_grouped_frame(_all_sections_payload(), view=view, unicode=view == "rich", width=120, expand=expand)
    catalog = tuple(FRAME_SECTIONS.values())
    shown = tuple(head for head in heads(screen) if head in catalog)

    assert shown == (catalog if expand else FRAME_HEADS)


@pytest.mark.parametrize("view", ["plain", "rich"])
@pytest.mark.parametrize("expand", [False, True], ids=["compact", "expand"])
def test_height_evicts_sections_from_the_bottom(view: str, expand: bool) -> None:
    payload = _all_sections_payload()
    options = {"view": view, "unicode": view == "rich", "width": 120, "expand": expand}
    catalog = tuple(FRAME_SECTIONS.values()) if expand else FRAME_HEADS
    full = render_grouped_frame(payload, **options)
    assert tuple(head for head in heads(full) if head in catalog) == catalog

    smallest_height = {}
    for height in range(len(full.splitlines()) + FRAME_TRAILING_ROWS, -1, -1):
        screen = render_grouped_frame(payload, height=height, **options)
        lines = screen.splitlines()
        assert len(lines) <= max(0, height - FRAME_TRAILING_ROWS)
        shown = tuple(head for head in heads(screen) if head in catalog)
        assert shown == catalog[:len(shown)], (height, shown)
        for head in shown:
            smallest_height[head] = height
        if height == 5 + FRAME_TRAILING_ROWS:  # verdict, title, POOLS head, one row and the overflow line
            assert shown == (FRAME_SECTIONS["pools"],)
            assert lines[0].startswith("ATTN") and lines[1].startswith("PeerHub Monitor")
            assert any("-7D" in line for line in lines[3:])

    for key in ("recent", "running", "alerts"):
        assert smallest_height[FRAME_SECTIONS["pools"]] < smallest_height[FRAME_SECTIONS[key]]
