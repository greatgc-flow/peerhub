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
    assert __version__ == "0.24.15"
    assert "denied_empty" in body and "#371" in body
    assert "PEERHUB_AG_TRANSPORT" not in body  # no adjacent history (0.24.14)
    assert "--responses" not in body  # no adjacent history (0.24.13)
    assert "explain_cmd" not in body  # no adjacent history (0.24.12)
    assert "traffic lights" not in body  # no adjacent history (0.24.11)
    assert "(#339)" not in body  # no adjacent history (0.24.7)
    assert "(#329)" not in body  # no adjacent history (0.24.6)
    assert "Profile cards: every packaged profile" not in body  # no adjacent history (0.24.5)
    assert "`PACE` column" not in body  # no adjacent history (0.24.4)
    assert "ask_prompt_too_large" not in body  # no adjacent history (0.24.3)
    assert "yellow `fast`" not in body  # no adjacent history (0.24.2)
    assert "permission_denials" not in body  # no adjacent history (0.24.1)
    assert "Breaking: compact human text" not in body  # no adjacent history (0.24.0)
    assert "five operating lessons" not in body  # no adjacent history
    assert "(#232, #241)" not in body
