from __future__ import annotations

import json
from pathlib import Path

from peerhub.cli import SystemClock, UuidSource, main
from peerhub.core.context import PathLayout, RuntimeContext
from peerhub.dispatch.duty_lease import DutyOwnerIdentity
from peerhub.dispatch.room_session import RoomSessionOpenRequest
from peerhub.runtime import create_runtime


def test_cli_alert_raise_updates_slot_and_delivers_critical_mail(
    tmp_path: Path,
    capsys,
) -> None:
    context = RuntimeContext(
        workspace_home_id=tmp_path.name,
        paths=PathLayout.for_workspace(tmp_path),
        clock=SystemClock(),
        ids=UuidSource(),
    )
    with create_runtime(context, adapter_peer_kind="fake") as runtime:
        runtime.rooms_service.create_room(
            room_id="room-cli-alert",
            topic_id="topic-cli-alert",
            title="CLI alert room",
            creator_id="raiser",
            participants=("raiser", "recipient"),
        )
        for actor, instance_id, profile_id in (
            ("raiser-principal", "raiser-terminal", "raiser-profile"),
            (
                "recipient-principal",
                "recipient-terminal",
                "recipient-profile",
            ),
        ):
            runtime.room_participation_coordinator.open_session(
                RoomSessionOpenRequest(
                    workspace_scope_id="workspace-1",
                    room_id="room-cli-alert",
                    actor_principal_id=actor,
                    owner=DutyOwnerIdentity(instance_id, profile_id),
                    session_fingerprint=f"fingerprint-{actor}",
                    heartbeat_timeout_ms=60_000,
                )
            )

    exit_code = main([
        "alert",
        "raise",
        "--workspace",
        str(tmp_path),
        "--room-id",
        "room-cli-alert",
        "--raiser-instance-id",
        "raiser-terminal",
        "--raiser-profile-id",
        "raiser-profile",
        "--severity",
        "p0",
        "--msg",
        "CLI emergency",
        "--json",
    ])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["alert_target_id"] == "room-alert:room-cli-alert"
    assert payload["recipient_profile_ids"] == ["recipient-profile"]

    with create_runtime(context, adapter_peer_kind="fake") as runtime:
        target = runtime.governance_broker.get_target(
            "room-alert:room-cli-alert"
        )
        inbox = runtime.rooms_service.check_inbox(
            room_id="room-cli-alert",
            caller_instance_id="recipient-terminal",
            caller_profile_id="recipient-profile",
        )

    assert target is not None
    assert target.state["severity"] == "P0"
    assert target.state["message"] == "CLI emergency"
    assert len(inbox) == 1
    assert inbox[0].state["priority"] == "CRITICAL"
