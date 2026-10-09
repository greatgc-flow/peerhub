"""Console stream setup for the public peerhub CLI."""
from __future__ import annotations

import os
import sys
from typing import Any, TextIO

_warned_encoding = False


class _EncodingWarningStream:
    def __init__(self, stream: TextIO) -> None:
        self._stream = stream

    def __getattr__(self, name: str) -> Any:
        return getattr(self._stream, name)

    def write(self, text: str) -> int:
        global _warned_encoding
        encoding = self._stream.encoding
        if encoding and self._stream.errors == "replace" and not _warned_encoding:
            try:
                text.encode(encoding, "strict")
            except UnicodeEncodeError:
                _warned_encoding = True
                sys.stderr.write(f"WARNING: output encoding {encoding} cannot show some characters; "
                                 "they are replaced; use UTF-8 or --json\n")
        return self._stream.write(text)

    def writelines(self, lines: list[str]) -> None:
        for line in lines:
            self.write(line)


def tolerant_streams() -> None:
    """Never crash on text the console encoding cannot represent (REL-002/REL-011), without forcing an encoding.

    The stream keeps the user's encoding (locale default or an explicit PYTHONIOENCODING); only characters that cannot be encoded
    are replaced ('?'), unless the user chose an error handler explicitly (PYTHONIOENCODING=enc:errors)."""
    explicit_errors = ":" in os.environ.get("PYTHONIOENCODING", "")
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name)
        if isinstance(stream, _EncodingWarningStream):
            continue
        try:
            if not explicit_errors:
                stream.reconfigure(errors="replace")  # type: ignore[union-attr]
            if stream.errors == "replace":
                setattr(sys, name, _EncodingWarningStream(stream))
        except (AttributeError, ValueError, OSError):
            pass
