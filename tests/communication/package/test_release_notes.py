"""Release bodies come from a single version section, without adjacent history."""

import re
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
    changelog = (Path(__file__).resolve().parents[3] / "CHANGELOG.md").read_text(encoding="utf-8")
    versions = re.findall(r"^## \[([^]]+)\]", changelog, re.MULTILINE)
    assert __version__ in versions
    body = release_notes(changelog, __version__)
    assert body.strip()

    previous = versions[versions.index(__version__) + 1]
    assert release_notes(changelog, previous).strip() not in body
