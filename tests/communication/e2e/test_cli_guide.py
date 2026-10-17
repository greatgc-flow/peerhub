"""Unit and E2E tests for Slice 5: peerhub guide subcommand.

Tests:
- Line and token caps (<=60 lines, ~900 tokens)
- Every example `peerhub ...` parses via real argparse
- Printed profile IDs exist and .standard is listed first
- Required guidance: two-asks reuse, no blind retry, discovery cmds, single recovery step
- Works without store or providers
- Pointers present in --help and README.md
"""
from __future__ import annotations

import re
import shlex
from pathlib import Path

import pytest

from peerhub.cli.app import build_parser, main

pytestmark = [pytest.mark.unit]


def test_guide_line_and_token_cap(capsys):
    """S5: peerhub guide stays within <=60 lines and ~900 tokens."""
    code = main(["guide"])
    assert code == 0
    out = capsys.readouterr().out
    lines = out.splitlines()
    assert len(lines) <= 60, f"Guide has {len(lines)} lines, exceeding cap of 60"
    words = out.split()
    assert len(words) <= 900, f"Guide has {len(words)} words, exceeding cap of 900"


def test_guide_example_commands_parse_via_real_argparse(capsys):
    """S5: Every example command 'peerhub ...' in the guide parses via real argparse."""
    code = main(["guide"])
    assert code == 0
    out = capsys.readouterr().out

    parser = build_parser()
    found_examples = 0
    for line in out.splitlines():
        line_clean = line.strip()
        if line_clean.startswith("peerhub "):
            cmd_part = line_clean.split("#", 1)[0].strip()
            tokens = shlex.split(cmd_part)
            args = tokens[1:]  # strip 'peerhub'
            # Must parse without raising SystemExit or error
            try:
                parsed = parser.parse_args(args)
                assert parsed is not None
                found_examples += 1
            except SystemExit as exc:
                pytest.fail(f"Example command failed to parse: {cmd_part!r} ({exc})")

    assert found_examples >= 4, f"Found only {found_examples} examples"


def test_guide_printed_profile_ids_exist_and_standard_first(capsys):
    """S5: Printed profile IDs exist in packaged defaults, and .standard profiles appear first."""
    from peerhub.extensions.adapters.profiles import list_profiles

    code = main(["guide"])
    assert code == 0
    out = capsys.readouterr().out

    known = {p["profile"] for p in list_profiles(".")}
    # Extract profile mentions
    prof_pattern = re.compile(r"\b([a-z]{2}\.[a-z]+)\b")
    printed_profiles = prof_pattern.findall(out)
    assert printed_profiles
    for p in printed_profiles:
        assert p in known, f"Printed profile {p} is not a known profile"

    # Verify .standard appears before .effort or .deepthink in the profile table
    standard_idx = out.find(".standard")
    effort_idx = out.find(".effort")
    deepthink_idx = out.find(".deepthink")
    assert standard_idx != -1 and effort_idx != -1 and deepthink_idx != -1
    assert standard_idx < effort_idx < deepthink_idx


def test_guide_contains_required_sections_and_guidance(capsys):
    """S5: Contains two-asks reuse, no blind retry, discovery cmds, single recovery step."""
    code = main(["guide"])
    assert code == 0
    out = capsys.readouterr().out

    # Two-asks reuse example with distinct request-id
    assert "ask" in out and "--stream" in out
    assert "--request-id req-001" in out and "--request-id req-002" in out
    # Uncertain delivery / no blind retry guidance
    assert "blind retry" in out.lower() or "uncertain" in out.lower()
    # Long asks section guidance
    assert "long asks" in out.lower()
    assert "blocks until the peer finishes" in out
    assert "session:" in out
    assert "--timeout-seconds" in out and "--silence-timeout-seconds" in out
    assert "foreground/background-with-exit-notification" in out
    # Discovery commands
    assert "peerhub monitor" in out
    assert "peerhub diag" in out
    assert "session list" in out
    assert "context usage" not in out
    # Layer column label accurate
    assert "Layer" in out
    # Single recovery step without saying repo skill
    assert "repo skill" not in out.lower()
    assert "refresh-model-profiles" in out


def test_guide_shows_error_line_if_profiles_fail(monkeypatch, capsys):
    """S5/CC review: No silent except Exception empty table; error line is displayed."""
    from peerhub.cli.app import main
    import peerhub.extensions.adapters.profiles as prof_mod

    def _fail_list(ws):
        raise RuntimeError("disk unreadable")

    monkeypatch.setattr(prof_mod, "list_profiles", _fail_list)
    code = main(["guide"])
    assert code == 0
    out = capsys.readouterr().out
    assert "error loading profiles: RuntimeError: disk unreadable" in out


def test_guide_works_without_store_or_providers(tmp_path, monkeypatch, capsys):
    """S5: Works in an isolated directory without store, config, or provider access."""
    empty_dir = tmp_path / "empty_workspace"
    empty_dir.mkdir()
    monkeypatch.chdir(empty_dir)
    monkeypatch.delenv("PEERHUB_DB", raising=False)
    monkeypatch.delenv("PEERHUB_CONFIG_HOME", raising=False)

    code = main(["guide"])
    assert code == 0
    out = capsys.readouterr().out
    assert "PeerHub Quick Reference" in out
    assert not (empty_dir / ".peerhub").exists()


def test_guide_pointers_in_help_and_readme(capsys):
    """S5: Pointers to 'peerhub guide' exist in --help and README.md."""
    # Check --help
    with pytest.raises(SystemExit):
        main(["--help"])
    help_out = capsys.readouterr().out
    assert "guide" in help_out

    # Check README.md
    readme_path = Path("README.md")
    assert readme_path.is_file()
    assert "peerhub guide" in readme_path.read_text(encoding="utf-8")
