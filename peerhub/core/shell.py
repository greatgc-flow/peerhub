"""Quote argument vectors for the platform shell."""
from __future__ import annotations

import os
import shlex

_POWERSHELL_QUOTES = str.maketrans({quote: quote * 2 for quote in "'‘’"})


def format_shell_command(argv: list[str]) -> str:
    """Render argv for PowerShell on Windows or a POSIX shell elsewhere."""
    if os.name == "nt":
        # PowerShell also treats typographic single quotes as delimiters.
        return " ".join(arg if arg.replace(".", "").replace("-", "").isalnum()
                        else "'" + arg.translate(_POWERSHELL_QUOTES) + "'" for arg in argv)
    return shlex.join(argv)
