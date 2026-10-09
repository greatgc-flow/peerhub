"""Public PeerHub CLI; one entrypoint, no dynamic attribute forwarding."""
from __future__ import annotations

from .app import build_parser

__all__ = ["build_parser", "main"]


def main(args: list[str] | None = None) -> int:
    from .app import main as run
    return run(args)
