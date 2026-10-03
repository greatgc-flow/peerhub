"""Console stream setup shared by the legacy and M1 CLIs."""
from __future__ import annotations

import sys


def utf8_streams() -> None:
    """Console encodings (cp949/cp1252 on Windows pipes) must never turn non-ASCII text into a crash or mojibake (REL-011)."""
    for stream in (sys.stdout, sys.stderr):
        try:
            if getattr(stream, "encoding", "utf-8").lower().replace("-", "") != "utf8":
                stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
        except (AttributeError, ValueError, OSError):
            pass
