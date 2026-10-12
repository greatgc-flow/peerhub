"""Verify that CHANGELOG.md release headings match git tags."""

from pathlib import Path

import pytest

from tools import lint_changelog
from tools.lint_changelog import lint_changelog_tags


def test_changelog_release_headings_match_git_tags() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    missing = lint_changelog_tags(repo_root)
    assert not missing, f"CHANGELOG.md contains release headings missing git tags: {missing}"


@pytest.mark.parametrize(
    ("headings", "tags", "expected"),
    [
        (["0.15.0", "0.14.0", "0.13.0"], {"v0.13.0"}, ["0.14.0"]),
        (["0.15.0", "0.14.0"], {"v0.14.0"}, []),
        (["Unreleased", "0.14.0"], {"v0.14.0"}, []),
        (["0.16.0", "0.14.0"], {"v0.14.0"}, ["0.16.0"]),
        (["0.14.0"], {"0.14.0"}, []),
    ],
    ids=["missing-historical-tag", "pending-current-release", "unreleased",
         "future-release-needs-tag", "unprefixed-tag"],
)
def test_changelog_tag_policy(
    monkeypatch: pytest.MonkeyPatch,
    headings: list[str], tags: set[str], expected: list[str],
) -> None:
    repo_root = Path("pending-release")
    files = {
        repo_root / "peerhub" / "_version.py": '__version__ = "0.15.0"\n',
        repo_root / "CHANGELOG.md": "".join(
            f"## [{version}]\n\nRelease notes.\n\n" for version in headings
        ),
    }
    monkeypatch.setattr(Path, "read_text", lambda path, **_kwargs: files[path])
    monkeypatch.setattr(lint_changelog, "get_git_tags", lambda _root: tags)
    assert lint_changelog_tags(repo_root) == expected
