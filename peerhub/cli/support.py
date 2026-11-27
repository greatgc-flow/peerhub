"""Shared CLI warning and error output."""
from __future__ import annotations

import sys
from typing import Any


def print_warnings(result: dict[str, Any]) -> None:
    for w in result.get("warnings", []):
        print(f"warning: {w}", file=sys.stderr, flush=True)


_NON_SENTENCE_DOT_PREFIXES = (".", " ", "/", "\\", ":", "=")


def _strip_sentence_period(text: str) -> str:
    if len(text) > 1 and text.endswith(".") and text[-2] not in _NON_SENTENCE_DOT_PREFIXES:
        return text[:-1]
    return text


def error_line(code: str, message: str, hint: str | None = None) -> None:
    hint = hint or f"peerhub explain {code}"
    clean_msg = _strip_sentence_period(" ".join(str(message).split()))
    print(f"[{code}] {clean_msg}; next: {' '.join(hint.split())}", file=sys.stderr)
