"""Tests for docs/windows.md (PowerShell operations, quoting, parallel asks, non-ASCII paths)."""
from __future__ import annotations

from pathlib import Path
import re
import shlex
import pytest

from peerhub.cli.app import build_parser

pytestmark = [pytest.mark.unit]


def test_windows_docs_exists_and_contains_required_sections():
    """Issue #136: docs/windows.md exists and covers PowerShell parallel asks, quoting, and paths."""
    doc_path = Path("docs/windows.md")
    assert doc_path.is_file(), "docs/windows.md does not exist"
    content = doc_path.read_text(encoding="utf-8")

    # Required topics
    assert "Start-Job" in content
    assert "ForEach-Object -Parallel" in content
    assert "$using:" in content
    assert "--query-file" in content
    assert "UNVERIFIED" in content  # unverified recipes labelled as such

    # Stream isolation warning for parallel asks
    assert "stream" in content.lower()
    assert "claim" in content.lower() or "isolated" in content.lower()


def test_windows_docs_example_commands_parse_via_real_parser():
    """Issue #136: Every example command 'peerhub ...' in docs/windows.md parses via real argparse."""
    doc_path = Path("docs/windows.md")
    assert doc_path.is_file(), "docs/windows.md does not exist"
    content = doc_path.read_text(encoding="utf-8")

    parser = build_parser()
    found_examples = 0

    # Match code blocks or lines starting with peerhub
    # Handle both standalone lines and lines inside PowerShell scripts
    peerhub_pattern = re.compile(r"(?:^|\n)\s*(peerhub\s+[^\r\n#|;]+)")
    for match in peerhub_pattern.finditer(content):
        cmd_str = match.group(1).strip()
        cmd_clean = cmd_str.rstrip("`").strip()
        tokens = shlex.split(cmd_clean, posix=False)
        tokens = [t.strip('"\'') for t in tokens]
        if not tokens or tokens[0] != "peerhub":
            continue
        args = tokens[1:]
        try:
            parsed = parser.parse_args(args)
            assert parsed is not None
            found_examples += 1
        except SystemExit as exc:
            pytest.fail(f"Example command failed to parse in docs/windows.md: {cmd_clean!r} ({exc})")

    assert found_examples >= 5, f"Found only {found_examples} peerhub examples in docs/windows.md"


def test_windows_docs_ask_examples_pass_runtime_argument_validation():
    """Every ask command example in docs/windows.md satisfies runtime argument validation contracts."""
    from peerhub.extensions.bridge import normalize_session_selector

    doc_path = Path("docs/windows.md")
    content = doc_path.read_text(encoding="utf-8")
    parser = build_parser()

    peerhub_pattern = re.compile(r"(?:^|\n)\s*(peerhub\s+[^\r\n#|;]+)")
    for match in peerhub_pattern.finditer(content):
        cmd_str = match.group(1).strip()
        cmd_clean = cmd_str.rstrip("`").strip()
        tokens = shlex.split(cmd_clean, posix=False)
        tokens = [t.strip('"\'') for t in tokens]
        if not tokens or tokens[0] != "peerhub":
            continue
        parsed = parser.parse_args(tokens[1:])
        if getattr(parsed, "subcommand", None) == "ask":
            # 1. Exactly one of prompt or --query-file must be provided (app.py:300)
            assert (parsed.prompt is None) != (parsed.query_file is None), (
                f"ask requires exactly one of prompt or --query-file, but got prompt={parsed.prompt!r} "
                f"and query_file={parsed.query_file!r} in {cmd_clean!r}"
            )
            # 2. Session selector flags must not conflict
            normalize_session_selector(fresh=parsed.fresh, session=parsed.session)


def test_ask_rejects_simultaneous_prompt_and_query_file_at_runtime():
    """app.py:300 rejects invocation providing both positional prompt and --query-file."""
    from peerhub.cli.app import main

    with pytest.raises(SystemExit) as exc_info:
        main(["ask", "cc", "prompt here", "--query-file", "query.txt"])
    assert exc_info.value.code == 2

