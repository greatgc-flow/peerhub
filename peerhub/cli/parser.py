"""Shared root-parser construction for the CLI package migration."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from typing import Any, TypedDict, cast


class HelpEpilogKwargs(TypedDict):
    """Keyword arguments shared by CLI parsers with example epilogs."""

    formatter_class: type[argparse.HelpFormatter]
    epilog: str


def help_epilog_kwargs(*example_lines: str) -> HelpEpilogKwargs:
    """Build the common raw-formatted ``Examples`` help epilog kwargs."""

    return {
        "formatter_class": argparse.RawDescriptionHelpFormatter,
        "epilog": "Examples:\n" + "".join(example_lines),
    }


def create_root_parser(
    *,
    formatter_class: type[argparse.HelpFormatter],
    version_action: type[argparse.Action],
    version_getter: Callable[[], str],
) -> argparse.ArgumentParser:
    """Create the stable top-level parser before command registration.

    ``version_getter`` remains injected from the public CLI module so existing
    integrations that patch its lazy version lookup retain their behavior.
    """

    parser = argparse.ArgumentParser(
        prog="peerhub",
        description="PeerHub Local Coordination CLI",
        formatter_class=formatter_class,
    )
    parser.add_argument(
        "--version",
        action=version_action,
        help="show program's version number and exit",
    )
    return parser


def complete_help_metadata(parser: argparse.ArgumentParser) -> None:
    """Promote chooser help into every child parser's description.

    ``argparse`` keeps ``add_parser(..., help=...)`` only on the parent
    chooser.  This single recursive pass reuses that authoritative text so all
    nested and leaf help pages explain themselves without a second hardcoded
    command-description table.
    """

    for raw_action in parser._actions:  # pyright: ignore[reportPrivateUsage]
        if not isinstance(raw_action, argparse._SubParsersAction):  # pyright: ignore[reportPrivateUsage]
            continue
        action = cast(Any, raw_action)
        chooser_help = {
            str(choice.dest): str(choice.help)
            for choice in cast(list[argparse.Action], action._choices_actions)
            if choice.help not in (None, argparse.SUPPRESS)
        }
        for name, child_value in cast(dict[str, object], action.choices).items():
            child = cast(argparse.ArgumentParser, child_value)
            if not child.description and name in chooser_help:
                child.description = chooser_help[name]
            complete_help_metadata(child)


def normalize_help_args(args: list[str] | None) -> list[str]:
    """Normalize Windows ``/?`` help at any parser depth before dispatch.

    Normalization must happen before ``argparse`` sees the tokens.  Otherwise
    commands with positional arguments can mistake ``/?`` for a peer, prompt,
    or archive path and enter their handler instead of displaying help.
    """

    source = sys.argv[1:] if args is None else args
    return ["--help" if token == "/?" else token for token in source]


def recursive_command_catalog(parser: argparse.ArgumentParser) -> str:
    """Render every leaf command and its promoted description."""

    commands: list[tuple[tuple[str, ...], str]] = []

    def visit(current: argparse.ArgumentParser, path: tuple[str, ...]) -> None:
        subparser_actions: list[Any] = [
            cast(Any, action)
            for action in current._actions  # pyright: ignore[reportPrivateUsage]
            if isinstance(action, argparse._SubParsersAction)  # pyright: ignore[reportPrivateUsage]
        ]
        if not subparser_actions:
            commands.append((path, (current.description or "").strip()))
            return
        for name, child_value in cast(
            dict[str, object], subparser_actions[0].choices
        ).items():
            visit(cast(argparse.ArgumentParser, child_value), (*path, name))

    visit(parser, ())
    lines = [f"Command catalog ({len(commands)} leaf commands):"]
    for path, description in commands:
        suffix = f"  {description}" if description else ""
        lines.append(f"  peerhub {' '.join(path)}{suffix}")
    return "\n".join(lines)


def add_workspace_arg(
    parser: argparse.ArgumentParser,
    *,
    help: str = "Path to workspace root",
) -> None:
    """Register the standard -w/--workspace flag, consolidating an
    identical add_argument call repeated across daily.py's diag/broadcast
    parsers and ask's own parser (which customizes only the help text)."""
    parser.add_argument("-w", "--workspace", default=None, help=help)


def add_json_arg(
    parser: argparse.ArgumentParser,
    *,
    short: bool = True,
    help: str = "Emit JSON output",
) -> None:
    """Register the standard --json flag, consolidating an identical
    add_argument call repeated across daily.py's diag/broadcast/ask
    parsers and setup.py's config-paths/adapter-discover parsers (which
    vary only in help text and whether -j is offered)."""
    names = ("-j", "--json") if short else ("--json",)
    parser.add_argument(*names, action="store_true", help=help)
