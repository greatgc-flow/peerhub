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
    """Policy 5/13: peerhub guide stays within <=60 lines and words * 1.3 <= 900 estimated tokens."""
    code = main(["guide"])
    assert code == 0
    out = capsys.readouterr().out
    assert "peerhub explain ag silence" in out
    lines = out.splitlines()
    assert len(lines) <= 60, f"Guide has {len(lines)} lines, exceeding cap of 60"
    words = len(out.split())
    estimated_tokens = int(words * 1.3)
    assert estimated_tokens <= 900, f"Guide has {estimated_tokens} estimated tokens ({words} words * 1.3), exceeding cap of 900"


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
    """Policy 13: Safety guidance parsed into semantic rules with meaning-level assertions."""
    code = main(["guide"])
    assert code == 0
    out = capsys.readouterr().out

    parser = build_parser()

    # Scenario 1: Per-peer write boundary enforcement
    # CLI ask requires explicit --writable; default is safe read-only (writable=False)
    ro_args = parser.parse_args(["ask", "cx", "test"])
    assert ro_args.writable is False
    rw_args = parser.parse_args(["ask", "cx", "test", "--writable"])
    assert rw_args.writable is True

    # Parse Safety section into semantic rules dict: {"Writes": "...", "Uncertain / pending_earlier_record": "...", ...}
    assert "Safety:\n" in out
    assert "Default-timeout ask: do not retry on the same stream" in out
    assert "start a new stream with explicit --timeout-seconds (long reviews: 1800 or more)" in out
    safety_block = out.split("Safety:\n", 1)[1].split("\n\n", 1)[0]
    rules: dict[str, str] = {}
    current_rule: str | None = None
    for line in safety_block.splitlines():
        if not line.strip():
            continue
        m = re.match(r"^\s{2}([A-Za-z0-9_/ ]+):\s*(.*)$", line)
        if m:
            current_rule = m.group(1).strip()
            rules[current_rule] = m.group(2).strip()
        elif current_rule:
            rules[current_rule] += " " + line.strip()

    assert "Writes" in rules
    assert "Uncertain" in rules
    assert "pending_earlier_record" in rules
    assert "Store hazards" in rules
    assert "Cost side effects" in rules
    assert "Secrets" in rules

    # Meaning-level check 1: Writes enforcement table per peer
    # cx: sandbox enforced; cc: enforced; ag: NOT enforced
    writes_text = rules["Writes"]
    assert re.search(r"\bcx\s+(?:sandbox\s+)?enforced\b", writes_text, re.IGNORECASE), "cx must be stated as enforced"
    assert not re.search(r"\bcx\s+not\s+enforced\b", writes_text, re.IGNORECASE), "cx must not be stated as not enforced"
    assert re.search(r"\bcc\s+enforced\b", writes_text, re.IGNORECASE), "cc must be stated as enforced"
    assert not re.search(r"\bcc\s+not\s+enforced\b", writes_text, re.IGNORECASE), "cc must not be stated as not enforced"
    assert re.search(r"\bag\s+not\s+enforced\b", writes_text, re.IGNORECASE), "ag must be stated as NOT enforced"
    ag_stripped = re.sub(r"\bag\s+not\s+enforced\b", "", writes_text, flags=re.IGNORECASE)
    assert not re.search(r"\bag\s+enforced\b", ag_stripped, re.IGNORECASE), "ag must not be stated as enforced"
    assert "issue #140" in writes_text
    assert re.search(r"when edits are intended,\s*pass --writable on every turn,\s*including resumed turns", writes_text)

    # Meaning-level check 2: Uncertain / pending_earlier_record rule
    unc_text = rules["Uncertain"]
    assert re.search(r"\bstop that stream\b", unc_text, re.IGNORECASE)
    # Recovery command in text must extract and parse cleanly
    diag_cmd_match = re.search(r"'peerhub\s+([a-z0-9 _-]+)'", unc_text)
    assert diag_cmd_match, "Recovery command 'peerhub ...' not found in uncertain rule"
    diag_cmd = diag_cmd_match.group(1)
    rec_args = parser.parse_args(shlex.split(diag_cmd))
    assert rec_args.subcommand == "diag"
    assert getattr(rec_args, "action", None) == "health"

    # Forbids retry-with-new-id of the original prompt (deleting 'never' fails)
    assert re.search(r"\bnever\s+blind[- ]retry\s+original\s+prompt\s+under\s+a\s+new\s+request[- ]id\b", unc_text, re.IGNORECASE)
    assert not re.search(r"\b(?:can|should|may|must)\s+blind[- ]retry\b", unc_text, re.IGNORECASE)
    assert re.search(r"--fresh", unc_text)
    assert re.search(r"delete claims", unc_text)
    # Distinguishes genuinely new work on a new stream
    assert re.search(r"\bcontinue genuinely new work on a new stream with a summary\b", unc_text, re.IGNORECASE)
    assert re.search(r"peerhub explain <code>", unc_text)
    pending_text = rules["pending_earlier_record"]
    assert "processed_status" in pending_text
    assert "delivered" in pending_text and "same request-id" in pending_text
    assert "uncertain" in pending_text

    # Meaning-level check 3: Store hazards
    store_text = rules["Store hazards"]
    assert re.search(r"\bback up core\.db before upgrading\b", store_text, re.IGNORECASE)
    assert re.search(r"\bnew schema not openable by old versions\b", store_text, re.IGNORECASE)
    assert re.search(r"\bdon't run dev builds on the real store\b", store_text, re.IGNORECASE)

    # Meaning-level check 4: Cost side effects
    cost_text = rules["Cost side effects"]
    assert re.search(r"\b(?:monitor watch|observation refresh)\s+may spend quota\b", cost_text, re.IGNORECASE)
    assert re.search(r"\b(?:snapshot/diag|diag)\s+read-only\b", cost_text, re.IGNORECASE)

    # Meaning-level check 5: Secrets persistence
    sec_text = rules["Secrets"]
    assert re.search(r"\bprompts/streams persist in local store\b", sec_text, re.IGNORECASE)
    assert re.search(r"\bno tokens/passwords\b", sec_text, re.IGNORECASE)


def test_guide_efficiency_guidance_and_transport(capsys):
    """Policy 13: Efficiency guidance <=3 lines (excluding heading); transport options parse via CLI."""
    code = main(["guide"])
    assert code == 0
    out = capsys.readouterr().out

    assert "Efficiency:\n" in out
    eff_block = out.split("Efficiency:\n", 1)[1].split("\n\n", 1)[0]
    # Exclude heading or title line
    eff_lines = [line.strip() for line in eff_block.splitlines() if line.strip() and not line.strip().startswith("Efficiency")]
    assert len(eff_lines) <= 3, f"Efficiency section has {len(eff_lines)} lines, exceeding limit of 3"

    # Concrete transport mechanism verification: --query-file and --json parse
    parser = build_parser()
    qf_args = parser.parse_args(["ask", "cc", "prompt", "--query-file", "input.txt", "--json"])
    assert qf_args.query_file == "input.txt"
    assert qf_args.json is True


def test_guide_report_issues_section(capsys):
    """Policy 13: Report issues section <=3 lines; covers repository, search, diagnostics, and privacy."""
    code = main(["guide"])
    assert code == 0
    out = capsys.readouterr().out

    assert "Report Issues:\n" in out
    rep_block = out.split("Report Issues:\n", 1)[1].split("\n\n", 1)[0]
    rep_lines = [line.strip() for line in rep_block.splitlines() if line.strip() and not line.strip().startswith("Report Issues")]
    assert len(rep_lines) <= 3, f"Report Issues section has {len(rep_lines)} lines, exceeding limit of 3"

    rep_text = " ".join(rep_lines)
    assert "github.com/greatgc-flow/peerhub" in rep_text
    assert "gh issue list -R greatgc-flow/peerhub --search KEYWORD" in rep_text
    assert "peerhub --version" in rep_text
    assert "status/exit code" in rep_text
    assert "workaround" in rep_text
    assert "no secrets/private paths" in rep_text


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
    assert "ag prints nothing until done" in out
    assert "--timeout-seconds 7200" in out
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
    # Upgrade monitor lock guidance (#123)
    assert "stop 'peerhub monitor' -> pip install -U peerhub -> restart monitor" in out
    assert "locks peerhub.exe on Windows" in out


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


def test_remote_ask_ag_silence_timeout_guidance():
    """Issue #118: docs/remote-ask.md documents ag silence timeout behavior."""
    remote_ask = Path("docs/remote-ask.md")
    assert remote_ask.is_file()
    text = remote_ask.read_text(encoding="utf-8")
    assert "ag prints nothing until done" in text
    assert "--silence-timeout-seconds" in text
    assert "--timeout-seconds 7200" in text


def test_readme_upgrade_guidance():
    """Issue #123: README Upgrade section documents monitor stopping and PyPI lag."""
    readme_path = Path("README.md")
    assert readme_path.is_file()
    text = readme_path.read_text(encoding="utf-8")
    assert "peerhub monitor" in text
    assert "locks `peerhub.exe` on Windows" in text
    assert "PyPI index lag" in text



