"""The monitor design record (docs/design/monitor-pace-health-2026-11-23): every decision states purpose, evidence and reason."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit]

DESIGN = Path(__file__).resolve().parents[3] / "docs" / "design"
RECORD = DESIGN / "monitor-pace-health-2026-11-23.md"
ROUNDS = DESIGN / "monitor-pace-health-2026-11-23" / "rounds"
TEXT = RECORD.read_text(encoding="utf-8")


def _sections() -> dict[str, str]:
    parts = re.split(r"(?m)^## ", TEXT)
    return {part.split("\n", 1)[0]: part for part in parts[1:]}


def test_every_decision_states_purpose_evidence_and_reason():
    sections = _sections()
    names = ["C1", *[f"M{i}" for i in range(1, 11)], "AI SURFACE"]
    for name in names:
        (title,) = [key for key in sections if key.startswith(name + " ") or key == name]
        body = sections[title]
        for label in ("Purpose", "Evidence", "Reason"):
            assert label in body, (title, label)
        assert "Decision" in body, title


def test_the_evidence_facts_are_listed_and_the_rounds_are_kept():
    for fact in [f"F{i}" for i in range(1, 11)]:
        assert re.search(rf"(?m)^{fact} ", TEXT), fact
    assert sorted(path.name for path in ROUNDS.glob("r*.md")) == [f"r{i}.md" for i in range(1, 10)]
    for path in ROUNDS.glob("r*.md"):
        text = path.read_text(encoding="utf-8")
        assert "## Prompt" in text and "## Replies" in text and "reply ---" in text, path.name


def test_the_design_index_links_the_record_and_nothing_leaks_local_paths():
    index = (DESIGN / "README.md").read_text(encoding="utf-8")
    assert "monitor-pace-health-2026-11-23.md" in index
    for path in [RECORD, *ROUNDS.glob("r*.md")]:
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"[A-Za-z]:[\/]+(PkgDev|Users)", text), path.name
        assert "sess_" not in text, path.name


def test_monitor_docs_name_both_rejected_pool_target_forms():
    root = DESIGN.parents[1]
    m2 = next(body for title, body in _sections().items() if title.startswith("M2 "))
    forms = re.findall(r"`(\[(?:pool_targets|pools\.<ID>)\])`", m2)
    assert len(forms) == 2
    guide = (root / "docs" / "monitor.md").read_text(encoding="utf-8")
    assert all(f"`{form}`" in guide for form in forms)


def test_record_and_unreleased_changelog_report_implementation():
    root = DESIGN.parents[1]
    status = re.fullmatch(r"Status: implemented in PRs (#460-#468) \(scored in (.*)\)", TEXT.splitlines()[2])
    assert status is not None
    scoring = sorted(ROUNDS.with_name("scoring").glob("s*.md"))
    assert status[2] == f"scoring/{scoring[0].name} to {scoring[-1].name}"
    # The section carrying the record's Documentation bullet: Unreleased until a release moves it under its version.
    sections = (root / "CHANGELOG.md").read_text(encoding="utf-8").replace("\r\n", "\n").split("\n## [")
    unreleased = next(s for s in sections if "### Documentation" in s and f"docs/design/{RECORD.name}" in s)
    documentation = unreleased.split("### Documentation", 1)[1]
    assert "not implemented" not in unreleased
    assert f"`docs/design/{RECORD.name}`" in documentation and status[1] in documentation
