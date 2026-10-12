"""Lint CHANGELOG.md release headings to verify they match git tags."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path


def get_git_tags(repo_root: Path) -> set[str]:
    try:
        proc = subprocess.run(
            ["git", "tag"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=True,
        )
        return {t.strip() for t in proc.stdout.splitlines() if t.strip()}
    except Exception as exc:
        raise RuntimeError(f"Failed to read git tags: {exc}") from exc


def get_changelog_versions(changelog_path: Path) -> list[str]:
    text = changelog_path.read_text(encoding="utf-8")
    return re.findall(r"^## \[([^\]]+)\]", text, re.MULTILINE)


def lint_changelog_tags(repo_root: Path | None = None) -> list[str]:
    root = repo_root or Path(__file__).resolve().parents[1]
    changelog = root / "CHANGELOG.md"
    versions = get_changelog_versions(changelog)
    tags = get_git_tags(root)

    missing: list[str] = []
    for ver in versions:
        if f"v{ver}" not in tags and ver not in tags:
            missing.append(ver)
    return missing


def main() -> int:
    try:
        missing = lint_changelog_tags()
        if missing:
            print(f"ERROR: CHANGELOG.md versions missing git tags: {', '.join(missing)}", file=sys.stderr)
            return 1
        print("OK: All CHANGELOG.md release headings match git tags.")
        return 0
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
