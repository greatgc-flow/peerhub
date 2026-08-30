"""SQLite-backed integration coverage for room alert raising."""

from __future__ import annotations

from pathlib import Path

import pytest

from peerhub.application.alert_raise import AlertRaiseCoordinator
from peerhub.core.errors import InvalidMutationError
from peerhub.core.protocol import CommandID
from peerhub.dispatch.duty_lease import DutyOwnerIdentity
from peerhub.dispatch.room_session import (
    RoomParticipationCoordinator,
    RoomSessionOpenRequest,
)
from peerhub.governance.broker import GovernanceBroker
from peerhub.governance.contract import (
    EffectIntent,
    MutationRequest,
    MutationSubmission,
)
from peerhub.governance.rooms import RoomsService
from peerhub.persistence.sqlite import SqliteStateStore
from tests.fakes import SequentialIdSource


class FixedClock:
    def __init__(self, value: int = 100) -> None:
        self.value = value

    def now(self) -> int:
        return self.value


@pytest.fixture
def services(tmp_path: Path):
    store = SqliteStateStore(
        tmp_path / "alert-raise.sqlite3",
        workspace_home_id="alert-raise-test",
    )
    store.initialize()
    clock = FixedClock()
    ids = SequentialIdSource()
    broker = GovernanceBroker(store, clock=clock, ids=ids)
    rooms = RoomsService(broker, clock=clock, ids=ids)
    room_sessions = RoomParticipationCoordinator(
        store,
        clock=clock,
        ids=ids,
    )
    coordinator = AlertRaiseCoordinator(
        broker,
        rooms=rooms,
        room_sessions=room_sessions,
        clock=clock,
        ids=ids,
    )
    yield coordinator, rooms, room_sessions, broker
    store.close()


def _create_room(rooms: RoomsService, room_id: str = "room-alert") -> None:
    rooms.create_room(
        room_id=room_id,
        topic_id=f"topic-{room_id}",
        title="Alert room",
        creator_id="raiser",
        participants=("raiser", "peer-b", "peer-c"),
    )


def _open_session(
    room_sessions: RoomParticipationCoordinator,
    *,
    room_id: str = "room-alert",
    actor_principal_id: str,
    instance_id: str,
    profile_id: str,
) -> None:
    room_sessions.open_session(
        RoomSessionOpenRequest(
            workspace_scope_id="workspace-1",
            room_id=room_id,
            actor_principal_id=actor_principal_id,
            owner=DutyOwnerIdentity(instance_id, profile_id),
            session_fingerprint=f"fingerprint-{actor_principal_id}",
            heartbeat_timeout_ms=1_000,
        )
    )


def test_raise_alert_persists_current_slot_and_notifies_other_live_members(
    services,
) -> None:
    coordinator, rooms, room_sessions, broker = services
    _create_room(rooms)
    _open_session(
        room_sessions,
        actor_principal_id="raiser-principal",
        instance_id="raiser-terminal",
        profile_id="raiser-profile",
    )
    _open_session(
        room_sessions,
        actor_principal_id="peer-b-principal",
        instance_id="peer-b-terminal",
        profile_id="peer-b-profile",
    )
    _open_session(
        room_sessions,
        actor_principal_id="peer-c-principal",
        instance_id="peer-c-terminal",
        profile_id="peer-c-profile",
    )

    result = coordinator.raise_alert(
        room_id="room-alert",
        raiser_instance_id="raiser-terminal",
        raiser_profile_id="raiser-profile",
        severity="p0",
        message="Storage partition failure",
    )

    assert result.alert_target_id == "room-alert:room-alert"
    assert result.room_id == "room-alert"
    assert result.recipient_profile_ids == (
        "peer-b-profile",
        "peer-c-profile",
    )
    assert len(result.inbox_message_target_ids) == 2

    target = broker.get_target("room-alert:room-alert")
    assert target is not None
    assert target.revision == 1
    assert target.state == {
        "kind": "room-alert",
        "scope": "room-alert",
        "schema_version": 1,
        "room_id": "room-alert",
        "alert_id": result.alert_id,
        "severity": "P0",
        "message": "Storage partition failure",
        "status": "OPEN",
        "raised_by": {
            "instance_id": "raiser-terminal",
            "profile_id": "raiser-profile",
        },
        "raised_at": 100,
        "ack_pending": (
            {
                "instance_id": "raiser-terminal",
                "profile_id": "raiser-profile",
            },
            {
                "instance_id": "peer-b-terminal",
                "profile_id": "peer-b-profile",
            },
            {
                "instance_id": "peer-c-terminal",
                "profile_id": "peer-c-profile",
            },
        ),
        "blocked": "P0 Alert: Storage partition failure...",
        "updated_at": 100,
    }

    for instance_id, profile_id in (
        ("peer-b-terminal", "peer-b-profile"),
        ("peer-c-terminal", "peer-c-profile"),
    ):
        inbox = rooms.check_inbox(
            room_id="room-alert",
            caller_instance_id=instance_id,
            caller_profile_id=profile_id,
        )
        assert len(inbox) == 1
        assert inbox[0].state["message_type"] == "ALERT"
        assert inbox[0].state["body"] == (
            "[CRITICAL-ALERT] P0: Storage partition failure"
        )
        assert inbox[0].state["resource_ref"] == "room-alert:room-alert"
        assert inbox[0].state["correlation_id"] == result.alert_id
        assert inbox[0].state["priority"] == "CRITICAL"

    assert rooms.check_inbox(
        room_id="room-alert",
        caller_instance_id="raiser-terminal",
        caller_profile_id="raiser-profile",
    ) == ()


def test_raise_alert_deduplicates_duplicate_live_session_identities(
    services,
) -> None:
    coordinator, rooms, room_sessions, _ = services
    _create_room(rooms)
    _open_session(
        room_sessions,
        actor_principal_id="raiser-principal",
        instance_id="raiser",
        profile_id="raiser",
    )
    for actor in ("recipient-principal-a", "recipient-principal-b"):
        _open_session(
            room_sessions,
            actor_principal_id=actor,
            instance_id="recipient-terminal",
            profile_id="recipient-profile",
        )

    result = coordinator.raise_alert(
        room_id="room-alert",
        raiser_instance_id="raiser",
        raiser_profile_id="raiser",
        message="deduplicate",
    )

    assert result.recipient_profile_ids == ("recipient-profile",)
    assert len(result.inbox_message_target_ids) == 1
    inbox = rooms.check_inbox(
        room_id="room-alert",
        caller_instance_id="recipient-terminal",
        caller_profile_id="recipient-profile",
    )
    assert len(inbox) == 1


@pytest.mark.parametrize(("supplied", "normalized"), [("P0", "P0"), ("p1", "P1")])
def test_raise_alert_accepts_and_normalizes_supported_severities(
    services,
    supplied: str,
    normalized: str,
) -> None:
    coordinator, rooms, _, broker = services
    _create_room(rooms)

    coordinator.raise_alert(
        room_id="room-alert",
        raiser_instance_id="raiser",
        raiser_profile_id="raiser",
        severity=supplied,
    )

    target = broker.get_target("room-alert:room-alert")
    assert target is not None
    assert target.state["severity"] == normalized


def test_raise_alert_rejects_unsupported_severity_before_writes(services) -> None:
    coordinator, _, _, broker = services

    with pytest.raises(InvalidMutationError, match="P0 or P1"):
        coordinator.raise_alert(
            room_id="room-invalid",
            raiser_instance_id="raiser",
            raiser_profile_id="raiser",
            severity="P2",
            message="unsupported",
        )

    assert broker.get_target("room-alert:room-invalid") is None


def test_raise_alert_accepts_an_empty_message(services) -> None:
    coordinator, rooms, _, broker = services
    _create_room(rooms)

    coordinator.raise_alert(
        room_id="room-alert",
        raiser_instance_id="raiser",
        raiser_profile_id="raiser",
        message="",
    )

    target = broker.get_target("room-alert:room-alert")
    assert target is not None
    assert target.state["message"] == ""
    assert target.state["blocked"] == "P1 Alert: ..."


def test_second_raise_overwrites_the_single_current_slot(services) -> None:
    coordinator, rooms, _, broker = services
    _create_room(rooms)

    first = coordinator.raise_alert(
        room_id="room-alert",
        raiser_instance_id="raiser",
        raiser_profile_id="raiser",
        severity="P1",
        message="first",
    )
    second = coordinator.raise_alert(
        room_id="room-alert",
        raiser_instance_id="raiser",
        raiser_profile_id="raiser",
        severity="P0",
        message="replacement",
    )

    assert second.alert_id != first.alert_id
    alerts = broker.list_targets("room-alert", "room-alert")
    assert len(alerts) == 1
    assert alerts[0].target_id == "room-alert:room-alert"
    assert alerts[0].revision == 2
    assert alerts[0].state["alert_id"] == second.alert_id
    assert alerts[0].state["severity"] == "P0"
    assert alerts[0].state["message"] == "replacement"


def test_raise_alert_retries_real_cas_conflict_with_a_fresh_session_snapshot(
    services,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    coordinator, rooms, room_sessions, broker = services
    _create_room(rooms)
    _open_session(
        room_sessions,
        actor_principal_id="raiser-principal",
        instance_id="raiser",
        profile_id="raiser",
    )
    _open_session(
        room_sessions,
        actor_principal_id="original-principal",
        instance_id="original-terminal",
        profile_id="original-profile",
    )

    original_submit = broker.submit
    injected = False

    def submit_with_intervening_commit(
        request: MutationRequest,
    ) -> MutationSubmission:
        nonlocal injected
        if request.target_id == "room-alert:room-alert" and not injected:
            injected = True
            _open_session(
                room_sessions,
                actor_principal_id="late-principal",
                instance_id="late-terminal",
                profile_id="late-profile",
            )
            original_submit(
                MutationRequest(
                    request_id="competing-alert-request",
                    command_id=CommandID("competing-alert-command"),
                    correlation_id="competing-alert-correlation",
                    client_id="test.concurrent-alert",
                    command_type="alert.raise",
                    idempotency_key="competing-alert-request",
                    actor_id="competitor",
                    policy_revision="protocol-v2",
                    target_id="room-alert:room-alert",
                    expected_revision=0,
                    operation="alert.raise",
                    desired_state={
                        "kind": "room-alert",
                        "scope": "room-alert",
                        "schema_version": 1,
                        "room_id": "room-alert",
                        "alert_id": "competing-alert",
                    },
                    effect_intent=EffectIntent(
                        kind="alert-raise.noop",
                        payload={},
                    ),
                )
            )
        return original_submit(request)

    monkeypatch.setattr(broker, "submit", submit_with_intervening_commit)

    result = coordinator.raise_alert(
        room_id="room-alert",
        raiser_instance_id="raiser",
        raiser_profile_id="raiser",
        severity="P1",
        message="retry me",
    )

    assert injected is True
    target = broker.get_target("room-alert:room-alert")
    assert target is not None
    assert target.revision == 2
    assert target.state["alert_id"] == result.alert_id
    assert target.state["ack_pending"][-1] == {
        "instance_id": "late-terminal",
        "profile_id": "late-profile",
    }
    assert result.recipient_profile_ids == (
        "original-profile",
        "late-profile",
    )
    late_inbox = rooms.check_inbox(
        room_id="room-alert",
        caller_instance_id="late-terminal",
        caller_profile_id="late-profile",
    )
    assert len(late_inbox) == 1
    assert late_inbox[0].state["correlation_id"] == result.alert_id
