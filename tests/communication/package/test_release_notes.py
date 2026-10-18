"""Release bodies come from a single version section, without adjacent history."""

from pathlib import Path

import pytest

from peerhub._version import __version__

from tools.release_notes import release_notes


def test_release_notes_extracts_exact_body():
    text = "# Changelog\n\n## [0.15.0] - 2026-10-12\n\n### Changed\n\n- Breaking.\n\n## [0.14.0]\n\n- Old.\n"
    assert release_notes(text, "0.15.0") == "### Changed\n\n- Breaking.\n"
    assert release_notes(text, "0.14.0") == "- Old.\n"
    with pytest.raises(ValueError, match="No CHANGELOG section"):
        release_notes(text, "0.99.0")


def test_current_package_version_has_changelog_release_body():
    changelog = Path(__file__).resolve().parents[3] / "CHANGELOG.md"
    body = release_notes(changelog.read_text(encoding="utf-8"), __version__)
    assert __version__ == "0.20.0"
    assert "Bare `peerhub` exit 2 -> 0" in body
    assert "Argon `A-7D` quota pool" in body
    assert "RESET-IN" in body
    assert "cx reset credits" in body
