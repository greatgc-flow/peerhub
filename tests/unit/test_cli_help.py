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
