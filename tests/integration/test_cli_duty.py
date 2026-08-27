from __future__ import annotations

import json
from pathlib import Path

import pytest

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


def test_cli_duty_close_can_also_end_room_session(
    tmp_path: Path, capsys
) -> None:
    base = ["--workspace", str(tmp_path)]
    assert main([
        "duty", "claim", *base,
        "--room-id", "room-both",
        "--instance-id", "instance-both",
        "--profile-id", "profile-both",
        "--owner-principal-id", "principal-both",
        "--authority-epoch", "1",
        "--json",
    ]) == 0
    lease = json.loads(capsys.readouterr().out)
    assert main([
        "session", "open", *base,
        "--workspace-scope-id", "workspace-both",
        "--room-id", "room-both",
        "--actor-principal-id", "principal-both",
        "--instance-id", "instance-both",
        "--profile-id", "profile-both",
        "--session-fingerprint", "fingerprint-both",
        "--json",
    ]) == 0
    session = json.loads(capsys.readouterr().out)

    assert main([
        "duty", "close", *base,
        "--lease-id", lease["lease_id"],
        "--room-id", "room-both",
        "--instance-id", "instance-both",
        "--profile-id", "profile-both",
        "--term", str(lease["term"]),
        "--authority-epoch", str(lease["authority_epoch"]),
        "--close-session",
        "--session-id", session["session_id"],
        "--session-generation", str(session["session_generation"]),
        "--workspace-scope-id", "workspace-both",
        "--actor-principal-id", "principal-both",
        "--json",
    ]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["duty_close"]["status"] == "ok"
    assert result["session_close"] == {
        "status": "ok",
        "session_id": session["session_id"],
        "session_generation": session["session_generation"],
        "state": "ENDED",
    }


def test_cli_duty_close_reports_session_fence_failure_independently(
    tmp_path: Path, capsys
) -> None:
    base = ["--workspace", str(tmp_path)]
    assert main([
        "duty", "claim", *base,
        "--room-id", "room-partial",
        "--instance-id", "instance-partial",
        "--profile-id", "profile-partial",
        "--owner-principal-id", "principal-partial",
        "--authority-epoch", "1",
        "--json",
    ]) == 0
    lease = json.loads(capsys.readouterr().out)
    assert main([
        "session", "open", *base,
        "--workspace-scope-id", "workspace-partial",
        "--room-id", "room-partial",
        "--actor-principal-id", "principal-partial",
        "--instance-id", "instance-partial",
        "--profile-id", "profile-partial",
        "--session-fingerprint", "fingerprint-partial",
        "--json",
    ]) == 0
    session = json.loads(capsys.readouterr().out)

    assert main([
        "duty", "close", *base,
        "--lease-id", lease["lease_id"],
        "--room-id", "room-partial",
        "--instance-id", "instance-partial",
        "--profile-id", "profile-partial",
        "--term", str(lease["term"]),
        "--authority-epoch", str(lease["authority_epoch"]),
        "--close-session",
        "--session-id", session["session_id"],
        "--session-generation", str(session["session_generation"] + 1),
        "--workspace-scope-id", "workspace-partial",
        "--actor-principal-id", "principal-partial",
        "--json",
    ]) == 2
    result = json.loads(capsys.readouterr().out)
    assert result["duty_close"]["status"] == "ok"
    assert result["session_close"]["status"] == "failed"
    assert main([
        "duty", "status", *base, "--room-id", "room-partial"
    ]) == 0
    assert "UNHELD" in capsys.readouterr().out

    assert main([
        "session", "close", *base,
        "--session-id", session["session_id"],
        "--session-generation", str(session["session_generation"]),
        "--workspace-scope-id", "workspace-partial",
        "--room-id", "room-partial",
        "--actor-principal-id", "principal-partial",
        "--instance-id", "instance-partial",
        "--profile-id", "profile-partial",
        "--json",
    ]) == 0
    assert json.loads(capsys.readouterr().out)["state"] == "ENDED"


def test_cli_duty_sweep_only_expires_timed_out_leases(
    tmp_path: Path,
    capsys,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class SweepClock:
        current = 1_000

        def now(self) -> int:
            return self.current

    monkeypatch.setattr("peerhub.cli.SystemClock", SweepClock)
    base = ["--workspace", str(tmp_path)]

    def claim(room_id: str, instance_id: str, timeout: int) -> dict:
        assert main([
            "duty", "claim", *base,
            "--room-id", room_id,
            "--instance-id", instance_id,
            "--profile-id", f"{instance_id}.profile",
            "--owner-principal-id", f"principal:{instance_id}",
            "--authority-epoch", "1",
            "--heartbeat-timeout-ms", str(timeout),
            "--json",
        ]) == 0
        return json.loads(capsys.readouterr().out)

    expired = claim("room-expired", "instance-expired", 1)
    active = claim("room-active", "instance-active", 60_000)
    SweepClock.current = 1_002
    assert main([
        "duty", "sweep", *base,
        "--recovery-actor-principal-id", "system:sweep",
        "--evidence-digest", "sha256:sweep",
        "--policy-id", "terminal-duty-recovery",
        "--policy-revision", "1",
        "--json",
    ]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["expired_count"] == 1
    assert result["leases"][0]["lease_id"] == expired["lease_id"]
    assert result["leases"][0]["state"] == "EXPIRED"
    assert main([
        "duty", "status", *base, "--room-id", active["room_id"]
    ]) == 0
    assert "instance-active/instance-active.profile" in capsys.readouterr().out
