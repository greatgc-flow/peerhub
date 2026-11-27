"""Keep the repository handoff short: state and next steps only (value review 2026-11-27)."""

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]

pytestmark = pytest.mark.package


def test_now_handoff_contract() -> None:
    lines = (ROOT / "NOW.md").read_text(encoding="utf-8").splitlines()
    assert len(lines) <= 30, "NOW.md holds state and next steps only; history lives in CHANGELOG.md."
    assert [line for line in lines if line.startswith("## ")] == ["## State", "## Next (ordered)"]
