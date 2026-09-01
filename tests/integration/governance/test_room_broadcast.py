"""SQLite integration coverage for legacy-compatible room broadcasts."""

from __future__ import annotations

from pathlib import Path

from fakes import FakeClock, FakeIdSource
from peerhub.application.room_broadcast import RoomBroadcastCoordinator
from peerhub.governance.broker import GovernanceBroker
from peerhub.governance.rooms import RoomsService
from peerhub.persistence.sqlite import SqliteStateStore


def _services(tmp_path: Path) -> tuple[RoomBroadcastCoordinator, RoomsService]:
    store = SqliteStateStore(
        tmp_path / "room-broadcast.sqlite3", workspace_home_id="broadcast-test"
    )
    store.initialize()
    broker = GovernanceBroker(
        store,
        clock=FakeClock(range(1, 200)),
        ids=FakeIdSource([f"broker-{index}" for index in range(1, 500)]),
    )
    rooms = RoomsService(
        broker,
        clock=FakeClock(range(1, 200)),
        ids=FakeIdSource([f"room-{index}" for index in range(1, 500)]),
    )
    return RoomBroadcastCoordinator(rooms=rooms), rooms


def _room(rooms: RoomsService, room_id: str) -> None:
    rooms.create_room(
        room_id=room_id,
        topic_id=f"{room_id}-topic",
        title="Broadcast room",
        creator_id="sender",
        participants=("sender", "peer-b", "peer-c"),
    )


def test_room_broadcast_full_room_delivers_real_mail_and_excludes_sender(
    tmp_path: Path,
) -> None:
    coordinator, rooms = _services(tmp_path)
    _room(rooms, "room-full")

    result = coordinator.broadcast(
        room_id="room-full", from_="sender", msg="hello everyone", targets=None
    )

    assert [outcome["target"] for outcome in result.delivered] == [
        "peer-b",
        "peer-c",
    ]
    for peer in ("peer-b", "peer-c"):
        inbox = rooms.check_inbox(
            room_id="room-full", caller_instance_id=peer, caller_profile_id=peer
        )
        assert len(inbox) == 1
        assert inbox[0].state["body"] == "hello everyone"
        assert inbox[0].state["message_type"] == "MSG"
    assert rooms.check_inbox(
        room_id="room-full", caller_instance_id="sender", caller_profile_id="sender"
    ) == ()


def test_room_broadcast_explicit_targets_deliver_only_subset(tmp_path: Path) -> None:
    coordinator, rooms = _services(tmp_path)
    _room(rooms, "room-subset")

    result = coordinator.broadcast(
        room_id="room-subset",
        from_="sender",
        msg="only b",
        targets=("peer-b",),
        msg_type="NOTICE",
        priority="HIGH",
    )

    assert result.delivered[0]["status"] == "OK"
    inbox = rooms.check_inbox(
        room_id="room-subset", caller_instance_id="peer-b", caller_profile_id="peer-b"
    )
    assert len(inbox) == 1
    assert inbox[0].state["message_type"] == "NOTICE"
    assert inbox[0].state["priority"] == "HIGH"
    assert rooms.check_inbox(
        room_id="room-subset", caller_instance_id="peer-c", caller_profile_id="peer-c"
    ) == ()


def test_room_broadcast_unknown_explicit_target_reports_error_and_continues(
    tmp_path: Path,
) -> None:
    coordinator, rooms = _services(tmp_path)
    _room(rooms, "room-unknown")

    result = coordinator.broadcast(
        room_id="room-unknown",
        from_="sender",
        msg="partial",
        targets=("missing", "peer-c"),
    )

    assert result.delivered[0] == {
        "target": "missing",
        "status": "ERROR",
        "error": "target is not a participant in the room",
    }
    assert result.delivered[1]["status"] == "OK"
    assert len(rooms.check_inbox(
        room_id="room-unknown", caller_instance_id="peer-c", caller_profile_id="peer-c"
    )) == 1
