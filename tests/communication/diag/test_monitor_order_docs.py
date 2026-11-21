"""The monitor order named in the docs and the packaged lessons follows FRAME_HEADS (#430)."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from peerhub.cli.monitor_view import FRAME_HEADS

ROOT = Path(__file__).resolve().parents[3]
FILES = ("README.md", "docs/monitor.md", "docs/lessons.md", "peerhub/config_data/lessons.toml")
HEADS = tuple(head.rstrip(":") for head in FRAME_HEADS)
SUGGESTION = re.compile(r"\bthe suggestion\b")


@pytest.mark.parametrize("name", FILES)
def test_docs_list_the_frame_sections_in_the_code_order(name: str) -> None:
    text = (ROOT / name).read_text(encoding="utf-8")
    flat = SUGGESTION.sub("suggest", " ".join(text.split()).replace("suggest:", "suggest"))
    expected = ", ".join(HEADS[:-1]).replace("suggest", "(?:suggest)") + " and " + HEADS[-1]
    assert re.search(expected, flat), f"{name}: {expected} not found"
