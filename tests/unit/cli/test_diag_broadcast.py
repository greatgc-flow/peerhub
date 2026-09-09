"""Unit tests for peerhub CLI diag and broadcast commands."""

import pytest
from pathlib import Path
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
