"""Release bodies come from a single version section, without adjacent history."""

import subprocess
import sys
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


def test_tool_writes_the_section_as_lf_bytes_on_every_platform():
    # `> <notes-file>` stores what the tool writes: text-mode stdout would give CRLF on Windows.
    repo = Path(__file__).resolve().parents[3]
    section = release_notes((repo / "CHANGELOG.md").read_text(encoding="utf-8"), __version__)
    run = subprocess.run([sys.executable, "-m", "tools.release_notes", "--version", __version__],
                         cwd=repo, capture_output=True, timeout=60, check=False)
    assert run.returncode == 0, run.stderr
    assert b"\r" not in run.stdout
    assert run.stdout == section.encode("utf-8")
    assert run.stdout.endswith(b"\n") and not run.stdout.endswith(b"\n\n")


def test_current_package_version_has_changelog_release_body():
    changelog = Path(__file__).resolve().parents[3] / "CHANGELOG.md"
    body = release_notes(changelog.read_text(encoding="utf-8"), __version__)
    assert __version__ == "0.24.20"
    assert "#384" in body and "Your prepayment credits are depleted" in body
    assert "#385" in body and "peerhub explain setup" in body
    assert "#381" not in body and "x-goog-api-key" not in body  # no adjacent history (0.24.19)
    assert "#330" not in body and "idx_bd_scope_claim_generation" not in body  # no adjacent history (0.24.18)
    assert "stamp_junit" not in body  # no adjacent history (0.24.17)
    assert "ATTN 3?" not in body  # no adjacent history (0.24.16)
    assert "rename this heading" not in body  # the pre-release note is gone
    assert "#371" not in body  # no adjacent history (0.24.15)
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
