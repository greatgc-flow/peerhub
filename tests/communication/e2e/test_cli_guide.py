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
    """Policy 13: Every example command 'peerhub ...' in the guide parses via real argparse."""
    code = main(["guide"])
    assert code == 0
    out = capsys.readouterr().out

    parser = build_parser()
    found_examples = 0

    # Parse full command lines starting with 'peerhub '
    for line in out.splitlines():
        line_clean = line.strip()
        if line_clean.startswith("peerhub "):
            cmd_part = line_clean.split("#", 1)[0].strip()
            tokens = shlex.split(cmd_part)
            args = tokens[1:]  # strip 'peerhub'
            try:
                parsed = parser.parse_args(args)
                assert parsed is not None
                found_examples += 1
            except SystemExit as exc:
                pytest.fail(f"Example command failed to parse: {cmd_part!r} ({exc})")

    assert found_examples >= 6, f"Found only {found_examples} examples"


def test_guide_generated_data_parity(capsys):
    """Policy 13: Generated profile table exhibits 1:1 parity with packaged profile registry."""
    from peerhub.extensions.adapters.profiles import list_profiles

    code = main(["guide"])
    assert code == 0
    out = capsys.readouterr().out

    profs = list_profiles(".")
    assert profs, "No profiles found in registry"
    for p in profs:
        p_name = p["profile"]
        assert p_name in out, f"Profile {p_name} missing from rendered guide table"

    # Verify .standard ordering before higher effort tiers
    std_pos = [out.find(f"{peer}.standard") for peer in ("ag", "cc", "cx")]
    effort_pos = [out.find(f"{peer}.effort") for peer in ("ag", "cc", "cx")]
    for s, e in zip(std_pos, effort_pos):
        assert s != -1 and e != -1 and s < e, ".standard profile must appear before .effort"


def test_guide_safety_scenarios_and_behaviour(capsys):
    """Policy 13: Safety guidance covers write boundaries, uncertainty recovery, store hazards, and secrets."""
    code = main(["guide"])
    assert code == 0
    out = capsys.readouterr().out

    parser = build_parser()

    # Scenario 1: Recovery from uncertain stream / pending_earlier_record
    # Guide specifies running 'peerhub diag health' before retry on a new stream
    recovery_args = parser.parse_args(["diag", "health"])
    assert recovery_args.subcommand == "diag"
    assert recovery_args.action == "health"

    # Scenario 2: Per-peer write boundary enforcement
    # CLI ask requires explicit --writable; default is safe read-only (writable=False)
    ro_args = parser.parse_args(["ask", "cx", "test"])
    assert ro_args.writable is False
    rw_args = parser.parse_args(["ask", "cx", "test", "--writable"])
    assert rw_args.writable is True

    # Scenario 3: Safety section covers all operational safety domains
    assert "Safety:\n" in out
    safety_block = out.split("Safety:\n", 1)[1].split("\n\n", 1)[0]
    # Each peer write boundary mentioned
    for peer in ("cx", "cc", "ag"):
        assert peer in safety_block
    # Refusal of dangerous blind retries / delete claims
    assert "delete claims" in safety_block
    # Store hazards & backup
    assert "core.db" in safety_block
    # Cost side effects
    assert "quota" in safety_block
    # Secrets persistence prohibition
    assert "local store" in safety_block


def test_guide_efficiency_guidance_and_transport(capsys):
    """Policy 13: Efficiency guidance <=3 lines; transport options parse via CLI."""
    code = main(["guide"])
    assert code == 0
    out = capsys.readouterr().out

    assert "Efficiency:\n" in out
    eff_block = out.split("Efficiency:\n", 1)[1].split("\n\n", 1)[0]
    eff_lines = [line.strip() for line in eff_block.splitlines() if line.strip()]
    assert len(eff_lines) <= 3, f"Efficiency section has {len(eff_lines)} lines, exceeding limit of 3"

    # Concrete transport mechanism verification: --query-file and --json parse
    parser = build_parser()
    qf_args = parser.parse_args(["ask", "cc", "prompt", "--query-file", "input.txt", "--json"])
    assert qf_args.query_file == "input.txt"
    assert qf_args.json is True


def test_guide_contains_required_sections_and_guidance(capsys):
    """S5: Contains two-asks reuse, discovery cmds, long asks, and single recovery step."""
    code = main(["guide"])
    assert code == 0
    out = capsys.readouterr().out

    # Two-asks reuse example with distinct request-id
    assert "ask" in out and "--stream" in out
    assert "--request-id req-001" in out and "--request-id req-002" in out
    # Long asks section guidance
    assert "long asks" in out.lower()
    assert "blocks until the peer finishes" in out
    assert "session:" in out
    assert "--timeout-seconds" in out and "--silence-timeout-seconds" in out
    assert "foreground/background-with-exit-notification" in out
    # Discovery commands
    assert "peerhub monitor" in out
    assert "peerhub monitor --expand" in out
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

