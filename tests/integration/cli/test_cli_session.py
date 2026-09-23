from __future__ import annotations

import json
from pathlib import Path

from peerhub.cli import main


def _identity_arguments() -> list[str]:
    return [
        "--workspace-scope-id",
        "workspace-1",
        "--room-id",
        "room-1",
        "--actor-principal-id",
        "principal-1",
        "--instance-id",
        "instance-1",
        "--profile-id",
        "cx.standard",
    ]


def test_cli_session_open_heartbeat_and_close(
    tmp_path: Path, capsys
) -> None:
    workspace = ["--workspace", str(tmp_path)]
    assert main([
        "session",
        "open",
        *workspace,
        *_identity_arguments(),
        "--session-fingerprint",
        "fingerprint-1",
        "--heartbeat-timeout-ms",
        "60000",
        "--json",
    ]) == 0
    opened = json.loads(capsys.readouterr().out)
    assert opened["state"] == "ACTIVE"
    assert opened["owner"] == {
        "instance_id": "instance-1",
        "profile_id": "cx.standard",
    }

    fence = [
        "--session-id",
        opened["session_id"],
        "--session-generation",
        str(opened["session_generation"]),
        *_identity_arguments(),
    ]
    assert main([
        "session",
        "heartbeat",
        *workspace,
        *fence,
        "--heartbeat-timeout-ms",
        "120000",
        "--json",
    ]) == 0
    heartbeat = json.loads(capsys.readouterr().out)
    assert heartbeat["session_id"] == opened["session_id"]
    assert heartbeat["state"] == "ACTIVE"
    assert heartbeat["heartbeat_expires_at"] > opened["heartbeat_expires_at"]

    assert main([
        "session",
        "close",
        *workspace,
        *fence,
        "--json",
    ]) == 0
    closed = json.loads(capsys.readouterr().out)
    assert closed["session_id"] == opened["session_id"]
    assert closed["state"] == "ENDED"


def test_cli_session_rejects_a_stale_generation_fence(
    tmp_path: Path, capsys
) -> None:
    workspace = ["--workspace", str(tmp_path)]
    assert main([
        "session",
        "open",
        *workspace,
        *_identity_arguments(),
        "--session-fingerprint",
        "fingerprint-fence",
        "--json",
    ]) == 0
    opened = json.loads(capsys.readouterr().out)

    assert main([
        "session",
        "heartbeat",
        *workspace,
        "--session-id",
        opened["session_id"],
        "--session-generation",
        str(opened["session_generation"] + 1),
        *_identity_arguments(),
    ]) == 2
    captured = capsys.readouterr()
    assert "room session fence mismatch" in captured.err

    assert main([
        "session",
        "close",
        *workspace,
        "--session-id",
        opened["session_id"],
        "--session-generation",
        str(opened["session_generation"]),
        *_identity_arguments(),
        "--json",
    ]) == 0
    assert json.loads(capsys.readouterr().out)["state"] == "ENDED"
