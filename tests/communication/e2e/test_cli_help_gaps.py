"""Public argument help stays complete and agrees with the CLI parser."""

import argparse
import re

import pytest

from peerhub.cli.app import build_parser
from peerhub.core.models import AppendRequest
from peerhub.extensions.diag_running import MAX_ROWS


BLANK_HELP = {
    ("", "--version"): "The global version flag is self-describing.",
}
HIDDEN_COMPAT_OPTIONS = {
    ("peer list", "--format"),
    ("profile list", "--format"),
    ("profile show", "--format"),
    ("session list", "--format"),
    ("session show", "--format"),
    ("stream list", "--format"),
}

# The parser leaves these values unresolved. The help describes the runtime rule.
CONTEXTUAL_DEFAULTS = {
    ("ask", "--effort"): None,
    ("ask", "--silence-timeout-seconds"): None,
    ("diag running", "--limit"): None,
    ("record append", "--created-at"): None,
    ("ask", "--stream"): None,  # stream depends on peer and request ID
    ("ask", "-w"): None,  # the current directory
    ("ask", "--writable"): False,  # read-only without the flag
    ("diag health", "--stream"): None,  # inspect the store instead
    ("diag health", "--observation-db"): None,  # inherit --db
    ("diag quota", "--observation-db"): None,  # inherit --db
    ("diag quota", "--max-age-seconds"): None,  # no age filter
    ("monitor", "--cycles"): None,  # cycle count depends on whether stdout is a TTY
    **{(f"profile {action}", flag): None for action in ("list", "show", "stats", "check") for flag in ("--workspace", "-w")},
}
REQUIRED_DEFAULTS = {
    ("monitor", "--peers"),
    ("monitor", "--interval-seconds"),
    ("monitor", "--timeout-seconds"),
}


def test_monitor_help_fits_narrow_terminal(capsys, monkeypatch):
    columns = 80
    monkeypatch.setenv("COLUMNS", str(columns))
    with pytest.raises(SystemExit) as exc:
        build_parser().parse_args(["monitor", "--help"])
    assert exc.value.code == 0
    lines = capsys.readouterr().out.splitlines()
    assert "--group GROUP" in " ".join(" ".join(lines).split())
    from peerhub.core.monitor_groups import GROUP_DESCRIPTIONS
    from peerhub.core.peer_kinds import QUOTA_CAPABLE_PEERS

    flat = " ".join(" ".join(lines).split())
    assert all(f"{group}: {meaning}" in flat for group, meaning in GROUP_DESCRIPTIONS.items())
    assert f"(default: {' '.join(QUOTA_CAPABLE_PEERS)})" in flat
    assert all(len(line) <= columns for line in lines)


def _walk(parser, path=()):
    yield " ".join(path), parser
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            for name, child in action.choices.items():
                yield from _walk(child, (*path, name))


@pytest.fixture(scope="module")
def commands():
    return dict(_walk(build_parser()))


def _arguments(parser):
    for action in parser._actions:
        if not isinstance(action, argparse._SubParsersAction):
            yield action.option_strings[0] if action.option_strings else action.dest, action


def test_all_arguments_have_nonempty_help(commands):
    blank = set()
    hidden = set()
    for command, parser in commands.items():
        for flag, action in _arguments(parser):
            if action.help == argparse.SUPPRESS:
                hidden.add((command, flag))
            elif not (action.help or "").strip():
                blank.add((command, flag))
    assert blank <= BLANK_HELP.keys()
    assert hidden == HIDDEN_COMPAT_OPTIONS


def test_named_defaults_match_argparse(commands):
    for command, parser in commands.items():
        for flag, action in _arguments(parser):
            if not action.help or action.help == argparse.SUPPRESS:
                continue
            help_text = parser._get_formatter()._expand_help(action)
            claims = re.findall(r"\bdefault:?\s+([^;(),]+)", help_text, re.IGNORECASE)
            default = action.default
            key = command, flag
            if key in CONTEXTUAL_DEFAULTS:
                assert default == CONTEXTUAL_DEFAULTS[key], key
                if key != ("diag running", "--limit"):  # its formatter adds the default in add_argument
                    assert re.search(r"\bdefault\b", help_text, re.IGNORECASE), key
                continue
            if flag == "--limit" or key in REQUIRED_DEFAULTS:
                assert claims, key
            shown_default = " ".join(map(str, default)) if isinstance(default, list) else str(default)
            for claim in claims:
                value = claim.split(". ", 1)[0].strip().rstrip(".")
                assert value.casefold() == shown_default.casefold(), (key, value, default)


@pytest.mark.parametrize(("command", "flag", "fragments"), [
    ("batch run", "manifest", ("JSON manifest", "example")),
    ("profile show", "profile", ("Profile",)),
    ("session show", "handle", ("session handle",)),
    ("outcome", "ask_id", ("receipt",)),
    ("outcome", "outcome", ("used the result", "declined the result", "attempted the ask again")),
    ("peer get", "--peer", ("Peer ID",)),
    ("stream show", "--stream", ("Stream ID",)),
    ("ask", "--effort", ("profile",)),
    ("ask", "--silence-timeout-seconds", ("seconds",)),
    ("monitor", "--interval-seconds", ("seconds",)),
    ("monitor", "--timeout-seconds", ("seconds",)),
    ("record append", "--kind", (AppendRequest.model_json_schema()["properties"]["kind"]["pattern"],)),
    ("record append", "--body", ("JSON", "stored as text")),
    ("record append", "--created-at", ("current UTC time",)),
])
def test_help_explains_requested_arguments(commands, command, flag, fragments):
    action = dict(_arguments(commands[command]))[flag]
    assert action.help and all(fragment in action.help for fragment in fragments), action.help


def test_diagnostic_and_account_help_matches_the_commands(commands):
    running_help = " ".join(commands["diag running"].format_help().split())
    assert f"By default, read up to {MAX_ROWS} rows" in running_help
    assert "SQLite" not in running_help and "VM steps" not in running_help
    account_help = " ".join(commands["account"].format_help().split())
    assert "Show one account binding" in account_help


@pytest.mark.parametrize(("command", "default"), [
    ("ask", "use the selected profile's effort if set"),
    ("ask", "no silence timeout"),
    ("diag running", f"read up to {MAX_ROWS} rows"),
    ("record append", "current UTC time"),
])
def test_unset_options_describe_the_runtime_default(commands, command, default):
    help_text = " ".join(commands[command].format_help().split())
    assert default in help_text and "default None" not in help_text
