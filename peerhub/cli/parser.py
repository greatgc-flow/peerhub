"""Shared root-parser construction for the CLI package migration."""

from __future__ import annotations

import argparse
from collections.abc import Callable


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
