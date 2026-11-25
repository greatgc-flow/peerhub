"""Shared CLI warning and error output."""
from __future__ import annotations

import sys
from typing import Any


def print_warnings(result: dict[str, Any]) -> None:
    for w in result.get("warnings", []):
        print(f"warning: {w}", file=sys.stderr, flush=True)


def error_line(code: str, message: str, hint: str | None = None) -> None:
    hint = hint or f"peerhub explain {code}"
    print(f"[{code}] {' '.join(str(message).split())}; next: {' '.join(hint.split())}", file=sys.stderr)
