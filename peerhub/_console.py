"""Console stream setup for the public peerhub CLI."""
from __future__ import annotations

import os
import sys


def tolerant_streams() -> None:
    """Never crash on text the console encoding cannot represent (REL-002/REL-011), without forcing an encoding.

    The stream keeps the user's encoding (locale default or an explicit PYTHONIOENCODING); only characters that cannot be encoded
    are replaced ('?'), unless the user chose an error handler explicitly (PYTHONIOENCODING=enc:errors)."""
    if ":" in os.environ.get("PYTHONIOENCODING", ""):
        return
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")  # type: ignore[union-attr]
        except (AttributeError, ValueError, OSError):
            pass
