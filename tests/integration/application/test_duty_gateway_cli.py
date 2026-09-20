"""CLI gateway coverage for every mutating terminal-duty action."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from peerhub.cli import main


_LEASE_FIELDS = {
    "lease_id",
    "room_id",
    "role",
    "owner",
    "owner_principal_id",
    "authority_epoch",
    "term",
    "state",
    "heartbeat_expires_at",
}


def _claim(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    *,
    room_id: str,
    instance_id: str,
    timeout_ms: int = 60_000,
) -> dict[str, object]:
    assert main([
        "duty", "claim",
        "--workspace", str(tmp_path),
        "--room-id", room_id,
        "--instance-id", instance_id,
        "--profile-id", f"{instance_id}.profile",
        "--owner-principal-id", f"principal:{instance_id}",
        "--authority-epoch", "1",
        "--heartbeat-timeout-ms", str(timeout_ms),
        "--json",
    ]) == 0
    return json.loads(capsys.readouterr().out)


def _assert_lease_payload(
    payload: dict[str, object],
    *,
    instance_id: str,
    state: str,
) -> None:
    assert set(payload) == _LEASE_FIELDS
    assert payload["owner"] == {
        "instance_id": instance_id,
        "profile_id": f"{instance_id}.profile",
    }
    assert payload["owner_principal_id"] == f"principal:{instance_id}"
    assert payload["state"] == state
    assert isinstance(payload["authority_epoch"], int)
    assert isinstance(payload["term"], int)
    assert isinstance(payload["heartbeat_expires_at"], int)


def test_cli_duty_claim_heartbeat_close_and_sweep_route_through_gateway(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class DutyClock:
        current = 1_000

        def now(self) -> int:
            return self.current

    monkeypatch.setattr("peerhub.cli.SystemClock", DutyClock)

    default_lease = _claim(
        tmp_path, capsys, room_id="room-default", instance_id="default"
    )
    _assert_lease_payload(default_lease, instance_id="default", state="ACTIVE")
    assert default_lease["heartbeat_expires_at"] == 1_060

    expiring_lease = _claim(
        tmp_path,
        capsys,
        room_id="room-expiring",
        instance_id="expiring",
        timeout_ms=1,
    )
    _assert_lease_payload(expiring_lease, instance_id="expiring", state="ACTIVE")
    assert expiring_lease["heartbeat_expires_at"] == 1_001

    assert main([
        "duty", "heartbeat",
        "--workspace", str(tmp_path),
        "--lease-id", str(default_lease["lease_id"]),
        "--room-id", "room-default",
        "--instance-id", "default",
        "--profile-id", "default.profile",
        "--term", str(default_lease["term"]),
        "--authority-epoch", str(default_lease["authority_epoch"]),
        "--json",
    ]) == 0
    heartbeat = json.loads(capsys.readouterr().out)
    _assert_lease_payload(heartbeat, instance_id="default", state="ACTIVE")

    assert main([
        "duty", "close",
        "--workspace", str(tmp_path),
        "--lease-id", str(default_lease["lease_id"]),
        "--room-id", "room-default",
        "--instance-id", "default",
        "--profile-id", "default.profile",
        "--term", str(default_lease["term"]),
        "--authority-epoch", str(default_lease["authority_epoch"]),
        "--json",
    ]) == 0
    closed = json.loads(capsys.readouterr().out)
    _assert_lease_payload(closed, instance_id="default", state="RELEASED")

    DutyClock.current = 1_002
    assert main([
        "duty", "sweep",
        "--workspace", str(tmp_path),
        "--recovery-actor-principal-id", "system:sweep",
        "--evidence-digest", "sha256:sweep",
        "--policy-id", "terminal-duty-recovery",
        "--policy-revision", "1",
        "--json",
    ]) == 0
    swept = json.loads(capsys.readouterr().out)
    assert swept["expired_count"] == 1
    assert swept["leases"][0]["lease_id"] == expiring_lease["lease_id"]
    _assert_lease_payload(
        swept["leases"][0], instance_id="expiring", state="EXPIRED"
    )

    assert main([
        "duty", "sweep",
        "--workspace", str(tmp_path),
        "--role", "terminal-duty",
        "--recovery-actor-principal-id", "system:manual-sweep",
        "--trigger", "MANUAL_RECOVERY",
        "--evidence-digest", "sha256:manual-sweep",
        "--policy-id", "terminal-duty-recovery",
        "--policy-revision", "2",
        "--json",
    ]) == 0
    assert json.loads(capsys.readouterr().out) == {
        "expired_count": 0,
        "leases": [],
    }


def test_cli_duty_close_with_session_routes_through_gateway(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    lease = _claim(
        tmp_path, capsys, room_id="room-session", instance_id="session"
    )
    assert main([
        "session", "open",
        "--workspace", str(tmp_path),
        "--workspace-scope-id", "workspace-session",
        "--room-id", "room-session",
        "--actor-principal-id", "principal:session",
        "--instance-id", "session",
        "--profile-id", "session.profile",
        "--session-fingerprint", "fingerprint-session",
        "--json",
    ]) == 0
    session = json.loads(capsys.readouterr().out)

    assert main([
        "duty", "close",
        "--workspace", str(tmp_path),
        "--lease-id", str(lease["lease_id"]),
        "--room-id", "room-session",
        "--instance-id", "session",
        "--profile-id", "session.profile",
        "--term", str(lease["term"]),
        "--authority-epoch", str(lease["authority_epoch"]),
        "--close-session",
        "--session-id", session["session_id"],
        "--session-generation", str(session["session_generation"]),
        "--workspace-scope-id", "workspace-session",
        "--actor-principal-id", "principal:session",
        "--json",
    ]) == 0
    result = json.loads(capsys.readouterr().out)
    _assert_lease_payload(
        result["duty_close"]["lease"], instance_id="session", state="RELEASED"
    )
    assert result["session_close"] == {
        "status": "ok",
        "session_id": session["session_id"],
        "session_generation": session["session_generation"],
        "state": "ENDED",
    }


def test_cli_duty_close_reports_partial_failure_and_retries_through_gateway(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    lease = _claim(
        tmp_path, capsys, room_id="room-retry", instance_id="retry"
    )
    assert main([
        "session", "open",
        "--workspace", str(tmp_path),
        "--workspace-scope-id", "workspace-retry",
        "--room-id", "room-retry",
        "--actor-principal-id", "principal:retry",
        "--instance-id", "retry",
        "--profile-id", "retry.profile",
        "--session-fingerprint", "fingerprint-retry",
        "--json",
    ]) == 0
    session = json.loads(capsys.readouterr().out)

    close_args = [
        "duty", "close",
        "--workspace", str(tmp_path),
        "--lease-id", str(lease["lease_id"]),
        "--room-id", "room-retry",
        "--instance-id", "retry",
        "--profile-id", "retry.profile",
        "--term", str(lease["term"]),
        "--authority-epoch", str(lease["authority_epoch"]),
        "--close-session",
        "--session-id", session["session_id"],
        "--workspace-scope-id", "workspace-retry",
        "--actor-principal-id", "principal:retry",
        "--json",
    ]
    assert main([
        *close_args,
        "--session-generation", str(session["session_generation"] + 1),
    ]) == 2
    failed = json.loads(capsys.readouterr().out)
    _assert_lease_payload(
        failed["duty_close"]["lease"], instance_id="retry", state="RELEASED"
    )
    assert failed["session_close"]["status"] == "failed"

    assert main([
        *close_args,
        "--session-generation", str(session["session_generation"]),
    ]) == 0
    retried = json.loads(capsys.readouterr().out)
    assert retried["session_close"] == {
        "status": "ok",
        "session_id": session["session_id"],
        "session_generation": session["session_generation"],
        "state": "ENDED",
    }


def _mismatch_request_context(monkeypatch: pytest.MonkeyPatch) -> None:
    import peerhub.cli as cli_module

    original_request_context = cli_module.RequestContext

    def mismatched_request_context(*, principal: str, client_id: str):
        del client_id
        return original_request_context(
            principal=principal, client_id="a-different-client"
        )

    monkeypatch.setattr(
        cli_module, "RequestContext", mismatched_request_context
    )


def test_cli_duty_claim_rejects_mismatched_asserted_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _mismatch_request_context(monkeypatch)
    assert main([
        "duty", "claim",
        "--workspace", str(tmp_path),
        "--room-id", "room-claim",
        "--instance-id", "claim",
        "--profile-id", "claim.profile",
        "--owner-principal-id", "principal:claim",
        "--authority-epoch", "1",
    ]) == 2


def test_cli_duty_heartbeat_rejects_mismatched_asserted_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _mismatch_request_context(monkeypatch)
    assert main([
        "duty", "heartbeat",
        "--workspace", str(tmp_path),
        "--lease-id", "lease-heartbeat",
        "--room-id", "room-heartbeat",
        "--instance-id", "heartbeat",
        "--profile-id", "heartbeat.profile",
        "--term", "1",
        "--authority-epoch", "1",
    ]) == 2


def test_cli_duty_close_rejects_mismatched_asserted_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _mismatch_request_context(monkeypatch)
    assert main([
        "duty", "close",
        "--workspace", str(tmp_path),
        "--lease-id", "lease-close",
        "--room-id", "room-close",
        "--instance-id", "close",
        "--profile-id", "close.profile",
        "--term", "1",
        "--authority-epoch", "1",
    ]) == 2


def test_cli_duty_sweep_rejects_mismatched_asserted_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _mismatch_request_context(monkeypatch)
    assert main([
        "duty", "sweep",
        "--workspace", str(tmp_path),
        "--recovery-actor-principal-id", "system:sweep",
        "--evidence-digest", "sha256:sweep",
        "--policy-id", "terminal-duty-recovery",
        "--policy-revision", "1",
    ]) == 2
