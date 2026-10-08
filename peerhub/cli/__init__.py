"""Public PeerHub CLI; one entrypoint, no dynamic attribute forwarding."""
from __future__ import annotations

import sys

from .app import build_parser

__all__ = ["build_parser", "main"]


def main(args: list[str] | None = None) -> int:
    actual_args = sys.argv[1:] if args is None else args
    if actual_args and actual_args[0] in ("--version", "-v", "-V"):
        from peerhub._version import __version__
        print(f"peerhub {__version__}")
        return 0
    from .app import main as run
    return run(args)
