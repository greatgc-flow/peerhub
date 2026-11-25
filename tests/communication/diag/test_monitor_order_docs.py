"""The docs and packaged lessons follow the monitor section catalog (#430, #436)."""
from __future__ import annotations

import re
from pathlib import Path

from peerhub.cli.monitor_format import POOLS_USE_FIRST_ASCENDING
from peerhub.cli.monitor_legend import FRAME_HEADS, FRAME_SECTIONS, FRAME_SECTION_ORDER
from peerhub.cli.monitor_quota_view import POOLS_USE_FIRST_HINT

ROOT = Path(__file__).resolve().parents[3]
MONITOR_DOC = "docs/monitor.md"
SUGGESTION = re.compile(r"\bthe suggestion\b")


def test_docs_list_the_frame_sections_in_the_code_order() -> None:
    text = (ROOT / MONITOR_DOC).read_text(encoding="utf-8")
    flat = SUGGESTION.sub(FRAME_SECTIONS["suggest"], " ".join(text.split()))
    expected = ", ".join(FRAME_HEADS[:-1]) + " and " + FRAME_HEADS[-1]
    assert expected in flat, f"{MONITOR_DOC}: {expected} not found"


def test_monitor_docs_list_expanded_details_in_catalog_order() -> None:
    text = (ROOT / MONITOR_DOC).read_text(encoding="utf-8")
    details = [head for key, head in FRAME_SECTIONS.items() if key not in FRAME_SECTION_ORDER]
    expected = f"After {FRAME_SECTIONS['leads']} it shows {', '.join(details)} and any other selected group."
    assert expected in " ".join(text.split())


def test_monitor_docs_describe_height_priority_and_pool_order() -> None:
    text = " ".join((ROOT / MONITOR_DOC).read_text(encoding="utf-8").split())
    direction = "ascending" if POOLS_USE_FIRST_ASCENDING else "descending"
    assert "Short frames shrink and drop sections from the bottom up in both layouts." in text
    assert (f"{FRAME_SECTIONS['pools']} sorts families by {direction} 7D PACE of the parent, "
            "then shows unknown PACE and finally `out` pools.") in text
    assert f"The heading shows `{POOLS_USE_FIRST_HINT}` when there is room." in text
