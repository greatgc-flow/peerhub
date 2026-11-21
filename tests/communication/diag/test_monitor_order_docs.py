"""The docs and packaged lessons follow the monitor section catalog (#430, #436)."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from peerhub.cli.monitor_view import FRAME_HEADS, FRAME_SECTIONS, FRAME_SECTION_ORDER

ROOT = Path(__file__).resolve().parents[3]
FILES = ("README.md", "docs/monitor.md", "docs/lessons.md", "peerhub/config_data/lessons.toml")
SUGGESTION = re.compile(r"\bthe suggestion\b")


@pytest.mark.parametrize("name", FILES)
def test_docs_list_the_frame_sections_in_the_code_order(name: str) -> None:
    text = (ROOT / name).read_text(encoding="utf-8")
    flat = SUGGESTION.sub(FRAME_SECTIONS["suggest"], " ".join(text.split()))
    expected = ", ".join(FRAME_HEADS[:-1]) + " and " + FRAME_HEADS[-1]
    assert expected in flat, f"{name}: {expected} not found"


def test_monitor_docs_list_expanded_details_in_catalog_order() -> None:
    text = (ROOT / "docs/monitor.md").read_text(encoding="utf-8")
    details = [head for key, head in FRAME_SECTIONS.items() if key not in FRAME_SECTION_ORDER]
    expected = f"After {FRAME_SECTIONS['leads']} it shows {', '.join(details)} and any other selected group."
    assert expected in " ".join(text.split())
