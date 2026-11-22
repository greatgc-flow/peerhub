"""README first-use path (scenario review 2026-11-22): a first ask, who reads what, safe upgrade with a real restore recipe."""
from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit]

README = (Path(__file__).resolve().parents[3] / "README.md").read_text(encoding="utf-8")


def _section(title: str) -> str:
    start = README.index(title)
    end = README.find("\n## ", start + 1)
    end2 = README.find("\n### ", start + 1)
    candidates = [e for e in (end, end2) if e != -1]
    return README[start:min(candidates) if candidates else len(README)]


def test_first_ask_with_an_explicit_store_comes_before_the_core_recipe():
    assert README.index("## First ask") < README.index("## Core recipe")
    first = _section("## First ask")
    assert 'peerhub --db .peerhub/core.db ask cc "Reply OK" --profile cc.standard --stream smoke --request-id smoke-1' in first
    assert "peerhub diag --plan" in first and "peerhub monitor" in first and "human" in first
    assert "peerhub guide" in first and "peerhub explain report-issues" in first


def test_upgrade_says_how_to_back_up_check_and_restore():
    upgrade = _section("### Upgrade")
    for needle in ("peerhub update --plan", ".pre-update.bak", "peerhub diag health", "peerhub explain store-and-upgrade"):
        assert needle in upgrade
    assert upgrade.index("peerhub update --plan") < upgrade.index("pip install -U peerhub") < upgrade.rindex(".pre-update.bak")


def test_readme_does_not_send_ai_sessions_to_the_monitor():
    assert "# For an AI session: line 1" not in README
    assert "# For a person who wants only the verdict, suggestion and pools" in README
    assert "`peerhub explain <topic or keywords>`" in README


def test_upgrade_requires_a_successful_new_backup_before_the_install():
    upgrade = _section("### Upgrade")
    before_install = upgrade[:upgrade.index("pip install -U peerhub")]
    assert "must exit 0" in before_install and "new modified time" in before_install


def test_batch_doc_keeps_code_constant_names_out_of_the_reader_recipe():
    doc = (Path(__file__).resolve().parents[3] / "docs" / "batch.md").read_text(encoding="utf-8")
    assert "POOLS_USE_FIRST_ASCENDING" not in doc
