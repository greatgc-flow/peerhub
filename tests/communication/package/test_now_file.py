"""Keep the repository handoff short and structured (no version pin: a pin forces ceremonial edits)."""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
CAPS = {
    "## State": 3,
    "## Recent flow (newest first, at most 5)": 5,
    "## Next (ordered, at most 8)": 8,
    "## Rules that bite (at most 8, one line each, link the doc)": 8,
}
HEADINGS = list(CAPS)

pytestmark = pytest.mark.package


def test_now_handoff_contract() -> None:
    path = ROOT / "NOW.md"
    assert path.is_file(), "Keep the active handoff in root NOW.md."
    contents = path.read_text(encoding="utf-8")
    lines = contents.splitlines()
    assert len(lines) <= 60, "Move older flow to CHANGELOG.md or docs/history."
    assert [line for line in lines if line.startswith("## ")] == HEADINGS

    starts = [lines.index(heading) for heading in HEADINGS]
    for start, end in zip(starts, [*starts[1:], len(lines)], strict=True):
        items = [line for line in lines[start + 1:end] if re.match(r"^\s*(?:[-+*]|\d+[.)])\s+", line)]
        assert len(items) <= CAPS[lines[start]], f"{lines[start]} exceeds its item cap."

