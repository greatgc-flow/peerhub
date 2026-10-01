import argparse
import contextlib
import io
import re
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch
import pytest

import peerhub.cli


class _ParserCaptured(Exception):
    def __init__(self, parser: argparse.ArgumentParser) -> None:
        self.parser = parser


def _capture_root_parser() -> argparse.ArgumentParser:
    def capture(
        parser: argparse.ArgumentParser,
        args: list[str] | None = None,
        namespace: argparse.Namespace | None = None,
    ) -> argparse.Namespace:
        del args, namespace
        raise _ParserCaptured(parser)

    with patch.object(argparse.ArgumentParser, "parse_args", capture):
        with pytest.raises(_ParserCaptured) as captured:
            peerhub.cli.main([])
    return captured.value.parser


def _parser_nodes(
    parser: argparse.ArgumentParser,
    prefix: tuple[str, ...] = (),
) -> Iterator[tuple[tuple[str, ...], argparse.ArgumentParser]]:
    yield prefix, parser
    for action in parser._actions:  # pyright: ignore[reportPrivateUsage]
        if not isinstance(action, argparse._SubParsersAction):  # pyright: ignore[reportPrivateUsage]
            continue
        for name, child in action.choices.items():
            yield from _parser_nodes(child, (*prefix, name))


def _is_leaf(parser: argparse.ArgumentParser) -> bool:
    return not any(
        isinstance(action, argparse._SubParsersAction)  # pyright: ignore[reportPrivateUsage]
        for action in parser._actions  # pyright: ignore[reportPrivateUsage]
    )


def test_tiered_help_and_subcommands(capsys):
    """
    Test that the --help output is tiered correctly, contains all expected commands,
    and that every registered command is still invocable at its expected path.
    """
    captured_subparsers = []
    original_add_subparsers = argparse.ArgumentParser.add_subparsers
    
    def mock_add_subparsers(self, *args, **kwargs):
        sp = original_add_subparsers(self, *args, **kwargs)
        captured_subparsers.append(sp)
        return sp

    with patch.object(argparse.ArgumentParser, 'add_subparsers', mock_add_subparsers):
        try:
            peerhub.cli.main(["--help"])
        except SystemExit as e:
            assert e.code == 0
            
    out, err = capsys.readouterr()
    
    # Assert tier headers and note exist
    assert "Primary commands:" in out
    assert "Setup & config:" in out
    assert "Operations (peer infrastructure):" in out
    assert "Governance (hub.py parity" in out
    assert "(Note: consensus propose/proposal-add/proposal-vote/vote overlap)" in out
    assert "Common workflows:" in out
    assert "peerhub workspace init --workspace ./peerhub-demo" in out
    assert "peerhub task create" in out
    
    assert len(captured_subparsers) > 0, "No subparsers were registered"
    
    # The first add_subparsers call on the main parser gives the top-level commands
    top_level_sp = captured_subparsers[0]
    commands = list(top_level_sp.choices.keys())
    
    assert len(commands) > 15, "Should have found top-level commands (~30 expected)"
    
    for cmd in commands:
        # 1. Assert the command name is formatted in the help text 
        # (TieredHelpFormatter indents commands with 4 spaces or more depending on subparser defaults)
        assert f"  {cmd} " in out or f"    {cmd} " in out, f"Command '{cmd}' missing from tiered help text"
        
        # 2. Assert it still successfully parses and handles its own --help
        try:
            peerhub.cli.main([cmd, "--help"])
        except SystemExit as e:
            assert e.code == 0, f"Command '{cmd} --help' failed with code {e.code}"


@pytest.mark.parametrize(
    ("command", "examples"),
    [
        (
            "workspace",
            ("peerhub workspace init --workspace ./peerhub-demo",),
        ),
        (
            "adapter",
            ("peerhub adapter discover --json",),
        ),
        (
            "config",
            (
                "peerhub config paths --workspace ./peerhub-demo",
                "peerhub config validate --workspace ./peerhub-demo --json",
                "peerhub config init --scope workspace --workspace ./peerhub-demo",
            ),
        ),
        (
            "backup",
            (
                "peerhub backup workspace --workspace ./peerhub-demo --output ./backups",
            ),
        ),
    ],
)
def test_setup_group_help_includes_usage_examples(
    command: str, examples: tuple[str, ...], capsys: pytest.CaptureFixture[str]
) -> None:
    """Stage 2 setup groups always advertise concrete runnable workflows."""

    with pytest.raises(SystemExit) as exit_info:
        peerhub.cli.main([command, "--help"])

    assert exit_info.value.code == 0
    output = capsys.readouterr().out
    assert "Examples:" in output
    for example in examples:
        assert example in output


@pytest.mark.parametrize(
    ("command", "examples"),
    [
        (
            "health",
            (
                "peerhub health check --workspace ./peerhub-demo --peer cc",
                "peerhub health sweep --workspace ./peerhub-demo --json",
            ),
        ),
        (
            "peer",
            (
                "peerhub peer status --workspace ./peerhub-demo --all",
                "peerhub peer recover --workspace ./peerhub-demo --peer cc",
            ),
        ),
        (
            "lease",
            (
                "peerhub lease status --workspace ./peerhub-demo",
                "peerhub lease sweep --workspace ./peerhub-demo --no-reap",
            ),
        ),
        (
            "gate",
            ("peerhub gate check cc --workspace ./peerhub-demo",),
        ),
        (
            "node",
            (
                "peerhub node list --workspace ./peerhub-demo",
                "peerhub node register --workspace ./peerhub-demo --node-id reviewer --peer-kind cx --actor cc",
            ),
        ),
        (
            "routing",
            (
                "peerhub routing discover --workspace ./peerhub-demo --needs review",
                "peerhub routing elect-leader --workspace ./peerhub-demo --needs review --reason \"Start review\"",
            ),
        ),
        (
            "leadership",
            (
                "peerhub leadership status --workspace ./peerhub-demo",
                "peerhub leadership claim --workspace ./peerhub-demo --peer-node-id cx --actor cx",
            ),
        ),
        (
            "broker",
            ("peerhub broker status --workspace ./peerhub-demo --json",),
        ),
    ],
)
def test_operations_group_help_includes_usage_examples(
    command: str, examples: tuple[str, ...], capsys: pytest.CaptureFixture[str]
) -> None:
    """Stage 3 operations groups always advertise concrete runnable workflows."""

    with pytest.raises(SystemExit) as exit_info:
        peerhub.cli.main([command, "--help"])

    assert exit_info.value.code == 0
    output = capsys.readouterr().out
    assert "Examples:" in output
    for example in examples:
        assert example in output


@pytest.mark.parametrize(
    ("command", "example"),
    [
        ("ask", "peerhub ask cx \"Summarize this repository\" --workspace ./peerhub-demo"),
        ("broadcast", "peerhub broadcast \"List one risk.\" --peers cx,ag --workspace ./peerhub-demo"),
        ("status", "peerhub status --workspace ./peerhub-demo --all"),
        ("diag", "peerhub diag --workspace ./peerhub-demo --domains"),
        ("statusline", "peerhub statusline --peer ag --workspace ./peerhub-demo"),
        ("consensus", "peerhub consensus list --workspace ./peerhub-demo"),
        ("task", "peerhub task create --workspace ./peerhub-demo --task-id docs-demo --summary \"Refresh docs\" --spec \"Add a usage example.\" --creator cx"),
        ("lesson", "peerhub lesson sweep --workspace ./peerhub-demo"),
        ("directive", "peerhub directive list --workspace ./peerhub-demo"),
        ("lock", "peerhub lock status --workspace ./peerhub-demo"),
        ("artifact", "peerhub artifact status --workspace ./peerhub-demo"),
        ("role", "peerhub role status --workspace ./peerhub-demo"),
        ("feedback", "peerhub feedback list --workspace ./peerhub-demo"),
        ("error", "peerhub error review list --workspace ./peerhub-demo"),
        ("alert", "peerhub alert raise --workspace ./peerhub-demo --room-id docs-room --raiser-instance-id cx-1 --raiser-profile-id standard --message \"Review blocked\""),
        ("room", "peerhub room create --workspace ./peerhub-demo --room-id docs-room --topic-id docs --title \"Docs review\" --creator cx --participants cx,ag"),
        ("duty", "peerhub duty status --workspace ./peerhub-demo --room-id docs-room"),
        ("session", "peerhub session open --workspace ./peerhub-demo --workspace-scope-id demo --room-id docs-room --actor-principal-id cx --instance-id cx-1 --profile-id standard --session-fingerprint cx-1-demo"),
    ],
)
def test_final_stage_group_help_includes_usage_examples(
    command: str, example: str, capsys: pytest.CaptureFixture[str]
) -> None:
    """Final-stage groups, including unlisted primary commands, expose examples."""

    with pytest.raises(SystemExit) as exit_info:
        peerhub.cli.main([command, "--help"])

    assert exit_info.value.code == 0
    output = capsys.readouterr().out
    assert "Examples:" in output
    assert example in output


def test_every_leaf_has_complete_recursive_help() -> None:
    """Every production leaf supports both standard help forms and explains itself."""

    root = _capture_root_parser()
    leaves = [
        (path, parser)
        for path, parser in _parser_nodes(root)
        if _is_leaf(parser)
    ]

    assert root.prog == "peerhub"
    assert len(leaves) == 109

    for path, parser in leaves:
        command = " ".join(path)
        assert parser.description, f"{command} has no help description"
        for action in parser._actions:  # pyright: ignore[reportPrivateUsage]
            if isinstance(action, argparse._HelpAction):  # pyright: ignore[reportPrivateUsage]
                continue
            assert action.help not in (None, argparse.SUPPRESS), (
                f"{command} argument {action.dest!r} has no help text"
            )

        for flag in ("-h", "--help"):
            stdout = io.StringIO()
            stderr = io.StringIO()
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                with pytest.raises(SystemExit) as exit_info:
                    root.parse_args([*path, flag])
            assert exit_info.value.code == 0, f"peerhub {command} {flag} failed"
            output = stdout.getvalue()
            assert f"usage: peerhub {command}" in output
            assert parser.description in output


def test_windows_help_alias_never_enters_a_handler_at_any_depth() -> None:
    """``/?`` is help for root, nested groups, and leaves, never positional data."""

    root = _capture_root_parser()
    for path, _parser in _parser_nodes(root):
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            with pytest.raises(SystemExit) as exit_info:
                peerhub.cli.main([*path, "/?"])
        assert exit_info.value.code == 0, f"peerhub {' '.join(path)} /? failed"
        assert "usage: peerhub" in stdout.getvalue()
        assert stderr.getvalue() == ""


def test_root_help_contains_complete_leaf_catalog_and_workflows() -> None:
    root = _capture_root_parser()
    leaf_paths = [
        " ".join(path)
        for path, parser in _parser_nodes(root)
        if _is_leaf(parser)
    ]

    stdout = io.StringIO()
    with contextlib.redirect_stdout(stdout):
        with pytest.raises(SystemExit) as exit_info:
            peerhub.cli.main(["/?"])

    assert exit_info.value.code == 0
    output = stdout.getvalue()
    assert "Command catalog (109 leaf commands):" in output
    assert "Common workflows:" in output
    assert "peerhub ask cx \"Summarize this repository\"" in output
    for path in leaf_paths:
        assert f"  peerhub {path}  " in output, path


def test_readme_cookbook_covers_every_top_level_command_group() -> None:
    root = _capture_root_parser()
    top_level = {
        name
        for action in root._actions  # pyright: ignore[reportPrivateUsage]
        if isinstance(action, argparse._SubParsersAction)  # pyright: ignore[reportPrivateUsage]
        for name in action.choices
    }
    readme = (Path(__file__).resolve().parents[2] / "README.md").read_text(
        encoding="utf-8"
    )
    cookbook = readme.split("### Scenario cookbook", 1)[1].split(
        "### Feedback loop", 1
    )[0]
    documented = set(re.findall(r"`peerhub ([a-z][a-z-]*)\b", cookbook))
    assert documented == top_level
