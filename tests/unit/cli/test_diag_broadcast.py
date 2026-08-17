"""Unit tests for peerhub CLI diag and broadcast commands."""

import pytest
from pathlib import Path
from peerhub.cli import main


def test_cli_diag_non_interactive(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]):
    exit_code = main(["diag", "--no-color"])
    assert exit_code == 0
    captured = capsys.readouterr()
    assert "PeerHub Multi-Peer Diagnostics" in captured.out
    assert "SUMMARY" in captured.out


def test_cli_diag_json(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]):
    exit_code = main(["diag", "--json"])
    assert exit_code == 0
    captured = capsys.readouterr()
    assert '"room"' in captured.out
    assert '"peers"' in captured.out
