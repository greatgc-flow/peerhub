"""Shared argparse behavior for the PeerHub CLI."""
from __future__ import annotations

import argparse
from typing import NoReturn, TYPE_CHECKING

from peerhub.cli.support import error_line

WORKSPACE_OPTIONS = ("-w", "--workspace")


class ArgumentParser(argparse.ArgumentParser):
    def error(self, message: str, hint: str | None = None) -> NoReturn:
        error_line("invalid_arguments", message, hint)
        exc = SystemExit(2)
        setattr(exc, "argument_error", message)
        raise exc


if TYPE_CHECKING:
    Subparsers = argparse._SubParsersAction[ArgumentParser]  # pyright: ignore[reportPrivateUsage]
