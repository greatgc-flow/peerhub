"""README first-use path (scenario review 2026-11-22): a first ask, who reads what, safe upgrade with a real restore recipe."""
from __future__ import annotations

from pathlib import Path

import pytest

from peerhub.cli import update
from peerhub.cli.store_select import DEFAULT_DB_PATH

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
    for needle in ("peerhub update --plan", "peerhub diag health", "peerhub explain store-and-upgrade"):
        assert needle in upgrade
    backup = DEFAULT_DB_PATH + update.UPDATE_BACKUP_SUFFIX.format(timestamp="<UTC time>")
    assert upgrade.index("peerhub update --plan") < upgrade.index(backup) < upgrade.index("pip install -U peerhub")
    restore = upgrade[upgrade.index("**Check, or restore**"):]
    assert "printed restore command" in restore and "fences old clients" in restore
    assert "workspace.generation" in restore and "peerhub diag health" in restore


def test_upgrade_checks_health_before_restarting_the_monitor():
    upgrade = _section("### Upgrade")
    assert upgrade.index("`peerhub diag health`") < upgrade.index("**Restart monitor**")


def test_upgrade_names_the_plan_installers_with_one_example():
    upgrade = _section("### Upgrade")
    installer = update._upgrade_commands(Path(DEFAULT_DB_PATH))[1]
    install_step = upgrade.split("2. **Run upgrade**", 1)[1].split("3. **", 1)[0]
    explanation, example, _ = install_step.split("```")
    assert example.partition("\n")[2].strip() == installer["command"]
    assert any(all(command in line for command in installer["alternatives"]) for line in explanation.splitlines())


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


def test_upgrade_waits_for_running_asks_and_restore_uses_the_printed_interpreter():
    upgrade = _section("### Upgrade")
    step0 = upgrade[upgrade.index("0. "):upgrade.index("1. ")]
    assert "exits 0" in step0 and "asks are running" in step0
    step3 = upgrade[upgrade.index("3. "):upgrade.index("4. ")]
    assert "python` has that PeerHub version" not in step3 and "interpreter" in step3
