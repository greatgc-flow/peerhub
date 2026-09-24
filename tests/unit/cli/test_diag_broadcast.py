"""Unit tests for peerhub CLI diag and broadcast commands."""

import sys
from pathlib import Path
from types import ModuleType
from unittest.mock import Mock

import pytest
import peerhub.cli as cli
from peerhub.cli import main


def test_cli_diag_non_interactive(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]):
    exit_code = main(["diag", "--workspace", str(tmp_path), "--no-color"])
    assert exit_code == 0
    captured = capsys.readouterr()
    assert "PeerHub Multi-Peer Dashboard" in captured.out
    assert "SUMMARY" in captured.out


def test_cli_diag_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]):
    exit_code = main(["diag", "--workspace", str(tmp_path), "--json"])
    assert exit_code == 0
    captured = capsys.readouterr()
    # No "room" key: peerhub has no leader-election/room concept of its own
    # (that was an Engram-specific governance layer, intentionally not
    # ported -- see engram_peerhub_separation_proposal.md row 6.5). A
    # "room" key would previously have carried a fabricated "room-efde"
    # literal, not measured state.
    assert '"room"' not in captured.out
    assert '"peers"' in captured.out


def test_cli_diag_live_uses_ansi_clear_on_windows_without_subprocess(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Live diagnostics clear with VT sequences and preserve q-to-exit polling."""
    fake_msvcrt = ModuleType("msvcrt")
    fake_msvcrt.kbhit = lambda: True  # type: ignore[attr-defined]
    fake_msvcrt.getch = lambda: b"q"  # type: ignore[attr-defined]
    subprocess_run = Mock()

    monkeypatch.setitem(sys.modules, "msvcrt", fake_msvcrt)
    monkeypatch.setattr(cli.subprocess, "run", subprocess_run)

    assert main(["diag", "--live", "--workspace", ".", "--no-color"]) == 0

    captured = capsys.readouterr()
    assert "\033[2J\033[H" in captured.out
    subprocess_run.assert_not_called()
