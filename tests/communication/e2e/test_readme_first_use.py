"""README first-use path (scenario review 2026-11-22): a first ask, who reads what, safe upgrade with a real restore recipe."""
from __future__ import annotations

import re
import shlex
from pathlib import Path

import pytest

from peerhub.cli import app, update
from peerhub.cli.store_select import DEFAULT_DB_PATH
from peerhub.core.store import CoreStore

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


def _powershell_commands(section: str) -> list[list[str]]:
    block = re.search(r"```powershell\n(.*?)\n```", section, re.DOTALL)
    assert block, "the documented recipe contains a PowerShell command block"
    commands = []
    for line in block[1].splitlines():
        if line.startswith("peerhub "):
            lexer = shlex.shlex(line, posix=True)
            lexer.whitespace_split = True
            lexer.escape = ""  # Backslashes in PowerShell paths are literal.
            commands.append(list(lexer)[1:])
    return commands


@pytest.mark.parametrize("ambient_store", [False, True])
def test_core_recipe_writes_only_its_explicit_store(tmp_path, monkeypatch, capsys, ambient_store):
    """Run the documented commands, including a conflicting shared-store setting."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PEERHUB_CONFIG_HOME", str(tmp_path / "no-global"))
    monkeypatch.delenv("PEERHUB_DB", raising=False)
    other_db = tmp_path / "shared" / "core.db"
    if ambient_store:
        # The shared peer makes an accidentally redirected command succeed,
        # so the database assertions detect a silent write to the wrong store.
        assert app.main(["--db", str(other_db), "peer", "register", "--peer", "reader"]) == 0
        monkeypatch.setenv("PEERHUB_DB", str(other_db))
    capsys.readouterr()
    for argv in _powershell_commands(_section("## Core recipe")):
        assert app.main(argv) == 0
    local = CoreStore(tmp_path / DEFAULT_DB_PATH, readonly=True)
    assert [stream.stream_id for stream in local.list_streams()] == ["core-demo"]
    assert [record.body for record in local.read_records("core-demo")] == ["Hello"]
    assert local.get_offset("reader", "core-demo").read_through_position == 1
    if ambient_store:
        other = CoreStore(other_db, readonly=True)
        assert other.list_streams() == []
        assert other.get_peer("reader").display_name is None


@pytest.mark.parametrize("recipe,expected", [
    ("core", ["peer", "stream", "record", "record", "offset", "offset", "offset"]),
    ("accounts", ["account", "account", "account", "ask"]),
])
def test_recipes_select_one_store_despite_environment(monkeypatch, recipe, expected):
    """Run the documented commands through CLI selection with execution stubbed out."""
    docs = Path(__file__).resolve().parents[3] / "docs"
    doc = _section("## Core recipe") if recipe == "core" else (docs / "accounts.md").read_text(encoding="utf-8")
    commands = _powershell_commands(doc)
    selected = []

    def inspect(args, parser):
        selected.append((args.subcommand, args.db, args.db_source))
        return 0

    cwd = Path(__file__).resolve().parent
    monkeypatch.chdir(cwd)
    monkeypatch.setenv("PEERHUB_DB", str(cwd / "other.db"))
    monkeypatch.setattr(app, "_dispatch_command", inspect)
    for argv in commands:
        assert app.main(argv) == 0
    assert [command for command, _, _ in selected] == expected
    assert {(db, source) for _, db, source in selected} == {
        (str(cwd / DEFAULT_DB_PATH), "explicit"),
    }


def test_upgrade_says_how_to_back_up_check_and_restore():
    upgrade = _section("### Upgrade")
    for needle in ("peerhub update --plan", "peerhub diag health", "docs/migrations.md"):
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
    assert "peerhub diag --plan" in README and "docs/monitor.md" in README
    assert "docs/commands.md#guide-and-explain" in README


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
