"""Public PeerHub CLI; no legacy dispatch or dynamic attribute forwarding."""
from __future__ import annotations

import os
import sys

from .app import build_parser
from .selector import InvalidSelectorError, REPORT_ENV, resolve_selector

__all__ = ["build_parser", "main"]


def main(args: list[str] | None = None) -> int:
    try:
        selector, source = resolve_selector(os.environ)
    except InvalidSelectorError as exc:
        print(f"peerhub: {exc}", file=sys.stderr)
        return 2
    if os.environ.get(REPORT_ENV) == "1":
        print(f"peerhub: cli selector={selector} (source: {source})", file=sys.stderr)
    actual_args = sys.argv[1:] if args is None else args
    if actual_args and actual_args[0] in ("--version", "-v", "-V"):
        from peerhub._version import __version__
        print(f"peerhub {__version__}")
        return 0
    from .app import main as run
    return run(args)
