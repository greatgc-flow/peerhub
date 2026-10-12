"""Print the selected CHANGELOG section verbatim for a GitHub release body."""

from __future__ import annotations

import argparse
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
    print(release_notes(changelog.read_text(encoding="utf-8"), args.version), end="")


if __name__ == "__main__":
    main()
