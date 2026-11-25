"""Content checks for the user guide and the command compatibility reference."""

import argparse
import re
from pathlib import Path

from peerhub.cli import app, ask_cmd
from peerhub.extensions.adapters.profiles import list_profiles, qualified_profile

ROOT = Path(__file__).resolve().parents[3]
COMMANDS = ROOT / "docs" / "commands.md"


def test_user_docs_have_no_encoding_damage():
    bad = []
    for path in [ROOT / "README.md", *sorted((ROOT / "docs").glob("*.md"))]:
        fence = ""
        shell = False
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if "\ufffd" in line:
                bad.append(f"{path.relative_to(ROOT)}:{number}: replacement character")
            delimiter = re.match(r"^\s*(`{3,}|~{3,})(\w*)", line)
            if delimiter and (not fence or delimiter[1].startswith(fence)):
                fence, shell = ("", False) if fence else (delimiter[1], delimiter[2] in {"bash", "sh", "powershell"})
                continue
            # Comments in shell examples are prose too. Skip other fenced code and inline code.
            if shell:
                stripped = line.lstrip()
                line = stripped[1:] if stripped.startswith("#") else line.partition(" # ")[2]
            elif fence:
                continue
            prose = re.sub(r"`[^`]*`", "", line)
            if re.search(r"\d\? [a-z]", prose):
                bad.append(f"{path.relative_to(ROOT)}:{number}: question mark after a digit in prose")
    assert not bad, "\n".join(bad)


def _hidden_options(parser, command=""):
    options = set()
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            for name, child in action.choices.items():
                options.update(_hidden_options(child, f"{command} {name}".strip()))
        elif action.help == argparse.SUPPRESS:
            options.update((command, flag) for flag in action.option_strings)
    return options


def test_command_compatibility_matches_parser_and_profile_loader(monkeypatch):
    section = COMMANDS.read_text(encoding="utf-8").split("## Compatibility\n", 1)[1].split("\n## ", 1)[0]
    rows = dict(re.findall(r"^\| (.+?) \| (.+?) \| .+? \|$", section, re.MULTILINE)[1:])

    documented_options = set()
    for surface in (key for key in rows if key.startswith("Hidden ")):
        flag, *commands = re.findall(r"`([^`]+)`", surface)
        if commands:
            documented_options.update((command, flag) for command in commands)
        else:
            documented_options.add(tuple(flag.rsplit(" ", 1)))
    assert documented_options == _hidden_options(app.build_parser())

    # A file path cannot hold local config, so the loader reads only packaged defaults.
    monkeypatch.setenv("PEERHUB_CONFIG_HOME", str(COMMANDS))
    profiles = list_profiles(COMMANDS, include_meta=True)
    aliases = {f"Profile alias `{row['profile']}`": row["fields"] for row in profiles if row["fields"]["alias_of"]["value"]}
    short = next(key for key in rows if key.startswith("Short profile IDs "))
    example, = re.findall(r"`([^`]+)`", short)
    qualified = qualified_profile("cc", example)
    assert qualified in {row["profile"] for row in profiles}
    assert f"`{qualified}`" in rows[short]

    export = "Python export `peerhub.cli.app.with_writable`"
    assert "with_writable" in app.__all__ and app.with_writable is ask_cmd.with_writable
    assert rows[export] == "`peerhub.cli.ask_cmd.with_writable`"
    assert rows.keys() == {short, export, *aliases, *(key for key in rows if key.startswith("Hidden "))}
    for alias, fields in aliases.items():
        target = fields["alias_of"]["value"]
        effort = fields["effort_override"]["value"]
        canonical = f"--profile {target}" + (f" --effort {effort}" if effort else "")
        assert f"`{canonical}`" in rows[alias]
