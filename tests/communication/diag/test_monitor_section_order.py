"""The compact and expanded monitor share the same frame section order."""
from __future__ import annotations

import pytest

from peerhub.cli.monitor_view import FRAME_HEADS, render_grouped_frame
from tests.communication.diag.test_monitor_expand_rich import heads
from tests.communication.diag.test_monitor_plain_golden import full_payload


@pytest.mark.parametrize("view", ["plain", "rich"])
@pytest.mark.parametrize("expand", [False, True], ids=["compact", "expand"])
def test_full_frame_section_order(view: str, expand: bool) -> None:
    screen = render_grouped_frame(full_payload(), view=view, unicode=view == "rich", width=120, expand=expand)
    shown = tuple(head for head in heads(screen) if head in FRAME_HEADS)

    assert shown == FRAME_HEADS
    assert shown.index("POOLS") < min(shown.index("ALERTS"), shown.index("RUNNING"))
