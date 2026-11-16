"""Write the selected CHANGELOG section verbatim (UTF-8, LF line endings) for a GitHub release body."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def release_notes(text: str, version: str) -> str:
    lines = text.splitlines(keepends=True)
    start = next((i for i, line in enumerate(lines)
                  if line.startswith(f"## [{version}]")), None)
    if start is None:
        raise ValueError(f"No CHANGELOG section for {version}")
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
    return "".join(lines[start + 1:end]).strip() + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True)
    args = parser.parse_args()
    changelog = Path(__file__).resolve().parents[1] / "CHANGELOG.md"
    notes = release_notes(changelog.read_text(encoding="utf-8"), args.version)
    # Bytes, not print: a redirect on Windows would turn every LF into CRLF (and use the console encoding),
    # and the release body must match the section exactly.
    sys.stdout.buffer.write(notes.encode("utf-8"))
    sys.stdout.buffer.flush()


if __name__ == "__main__":
    main()
