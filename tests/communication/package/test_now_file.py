"""Keep the repository handoff bounded and aligned with the release version."""

import re
from pathlib import Path

import pytest

from peerhub._version import __version__

ROOT = Path(__file__).resolve().parents[3]
HEADINGS = [
    "## State",
    "## Recent flow (newest first, at most 8)",
    "## Next (ordered, at most 8)",
    "## Rules that bite (at most 8, one line each, link the doc)",
]

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
        assert len(items) <= 8, f"{lines[start]} exceeds the eight-item cap."

    state = "\n".join(lines[starts[0] + 1:starts[1]])
    assert re.findall(r"^- Released: v(\d+\.\d+\.\d+) \(\d{4}-\d{2}-\d{2}\)", state, re.MULTILINE) == [__version__]
    assert re.findall(r"^Updated: \d{4}-\d{2}-\d{2} \(v(\d+\.\d+\.\d+)\)$", contents, re.MULTILINE) == [__version__]
