"""Shared root-parser construction for the CLI package migration."""

from __future__ import annotations

import argparse
from collections.abc import Callable
from typing import TypedDict


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
        description="PeerHub Local Coordination CLI",
        formatter_class=formatter_class,
    )
    parser.add_argument(
        "--version",
        action=version_action,
        help="show program's version number and exit",
    )
    return parser


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
