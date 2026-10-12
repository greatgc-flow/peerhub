"""Verify that CHANGELOG.md release headings match git tags."""

from pathlib import Path
from tools.lint_changelog import lint_changelog_tags


def test_changelog_release_headings_match_git_tags():
    repo_root = Path(__file__).resolve().parents[3]
    missing = lint_changelog_tags(repo_root)
    assert not missing, f"CHANGELOG.md contains release headings missing git tags: {missing}"
