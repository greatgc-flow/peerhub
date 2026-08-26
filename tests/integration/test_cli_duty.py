from __future__ import annotations

import json
from pathlib import Path

from peerhub.cli import main


def test_cli_duty_claim_heartbeat_close_and_status(tmp_path: Path, capsys) -> None:
    base = ["--workspace", str(tmp_path)]
    assert main(["duty", "claim", *base, "--room-id", "room-1", "--instance-id", "i-1", "--profile-id", "cx.standard", "--owner-principal-id", "p-1", "--authority-epoch", "1", "--json"]) == 0
    lease = json.loads(capsys.readouterr().out)
    assert lease["state"] == "ACTIVE"
    heartbeat = ["duty", "heartbeat", *base, "--lease-id", lease["lease_id"], "--room-id", "room-1", "--instance-id", "i-1", "--profile-id", "cx.standard", "--term", str(lease["term"]), "--authority-epoch", str(lease["authority_epoch"]), "--json"]
    assert main(heartbeat) == 0
    assert json.loads(capsys.readouterr().out)["state"] == "ACTIVE"
    assert main(["duty", "status", *base, "--room-id", "room-1"]) == 0
    assert "i-1/cx.standard" in capsys.readouterr().out
    assert main(["duty", "close", *base, "--lease-id", lease["lease_id"], "--room-id", "room-1", "--instance-id", "i-1", "--profile-id", "cx.standard", "--term", str(lease["term"]), "--authority-epoch", str(lease["authority_epoch"])]) == 0
    assert "closed" in capsys.readouterr().out
    assert main(["duty", "status", *base, "--room-id", "room-1"]) == 0
    assert "UNHELD" in capsys.readouterr().out

