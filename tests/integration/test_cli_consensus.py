from __future__ import annotations

import json
from pathlib import Path

from peerhub.cli import main


def _propose_args(workspace: Path) -> list[str]:
    return [
        "consensus", "propose", "--workspace", str(workspace),
        "--round-id", "round-cli", "--title", "Ship", "--question", "Ready?",
        "--body", "Decide", "--proposer", "cx", "--required", "cx,ag",
        "--eligible", "cx,ag", "--risk", "normal",
    ]


def test_cli_consensus_propose_vote_and_status(tmp_path: Path, capsys) -> None:
    assert main(_propose_args(tmp_path)) == 0
    assert "round-cli" in capsys.readouterr().out

    assert main([
        "consensus", "vote", "--workspace", str(tmp_path),
        "--round-id", "round-cli", "--actor", "cx", "--choice", "agree",
    ]) == 0
    assert "voting" in capsys.readouterr().out

    assert main([
        "consensus", "vote", "--workspace", str(tmp_path),
        "--round-id", "round-cli", "--actor", "ag", "--choice", "agree",
        "--json",
    ]) == 0
    vote_output = json.loads(capsys.readouterr().out)
    assert vote_output["phase"] == "quorum_reached"
    assert vote_output["quorum"]["reached"] is True

    assert main([
        "consensus", "status", "--workspace", str(tmp_path),
        "--round-id", "round-cli", "--json",
    ]) == 0
    status_output = json.loads(capsys.readouterr().out)
    assert status_output["round_id"] == "round-cli"


def test_cli_consensus_status_not_found_returns_nonzero(tmp_path: Path, capsys) -> None:
    assert main([
        "consensus", "status", "--workspace", str(tmp_path),
        "--round-id", "missing",
    ]) == 2
    assert "not found" in capsys.readouterr().err


def test_cli_consensus_list_returns_all_rounds(tmp_path: Path, capsys) -> None:
    assert main(_propose_args(tmp_path)) == 0
    capsys.readouterr()

    second = _propose_args(tmp_path)
    second[second.index("round-cli")] = "round-cli-second"
    assert main(second) == 0
    capsys.readouterr()

    assert main([
        "consensus", "list", "--workspace", str(tmp_path), "--json",
    ]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert {proposal["target_id"] for proposal in payload["proposals"]} == {
        "round-cli",
        "round-cli-second",
    }
