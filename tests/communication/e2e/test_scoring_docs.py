"""The archived scoring record keeps its rubric, protocol, history, and replies together."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit]

DIR = Path(__file__).resolve().parents[3] / "docs" / "history" / "design" / "scoring-2026-11-22"
README = (DIR / "README.md").read_text(encoding="utf-8")


def test_rubric_names_every_axis_and_scenario():
    for axis in ("A. Information", "B. Hints", "C. Concurrency", "D. Failure and recovery", "E. Cost", "F. Docs and tests"):
        assert axis in README
    for scenario in [f"A{i}" for i in range(1, 7)] + [f"H{i}" for i in range(1, 7)]:
        assert re.search(rf"\| {scenario} \|", README), scenario
    assert "95 or more" in README and "do not build" in README.lower()


def test_every_round_file_linked_in_the_history_exists_and_holds_replies():
    linked = re.findall(r"\(rounds/([a-z0-9-]+\.md)\)", README)
    assert linked and len(set(linked)) == len(linked)
    for name in linked:
        text = (DIR / "rounds" / name).read_text(encoding="utf-8")
        assert "## Prompt" in text and "## Replies" in text and "reply ---" in text, name


def test_no_local_paths_or_session_handles_in_the_record():
    for path in [DIR / "README.md", *(DIR / "rounds").glob("*.md")]:
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"[A-Za-z]:[\/]+(PkgDev|Users)", text), path.name
        assert "sess_" not in text, path.name
