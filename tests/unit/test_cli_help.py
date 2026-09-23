import argparse
from unittest.mock import patch
import pytest

import peerhub.cli


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
