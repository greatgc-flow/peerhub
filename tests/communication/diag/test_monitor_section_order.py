"""The compact and expanded monitor share the same frame section order."""
from __future__ import annotations

import pytest

from peerhub.cli.monitor_view import FRAME_HEADS, FRAME_SECTIONS, render_grouped_frame
from tests.communication.diag.test_monitor_expand_rich import detail_payload, heads


@pytest.mark.parametrize("view", ["plain", "rich"])
@pytest.mark.parametrize("expand", [False, True], ids=["compact", "expand"])
def test_full_frame_section_order(view: str, expand: bool) -> None:
    screen = render_grouped_frame(detail_payload(), view=view, unicode=view == "rich", width=120, expand=expand)
    catalog = tuple(FRAME_SECTIONS.values())
    shown = tuple(head for head in heads(screen) if head in catalog)

    assert shown == (catalog if expand else FRAME_HEADS)
