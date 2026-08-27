"""Integration coverage for immediate cross-domain lesson broadcasts."""

from __future__ import annotations

from pathlib import Path

import pytest

from fakes import FakeClock, FakeIdSource
from peerhub.application.lesson_broadcast import LessonBroadcastCoordinator
from peerhub.core.errors import InvalidMutationError
from peerhub.governance.broker import GovernanceBroker
from peerhub.governance.lessons import LessonService
from peerhub.governance.rooms import RoomsService
from peerhub.persistence.sqlite import SqliteStateStore


def _services(
    tmp_path: Path,
) -> tuple[LessonBroadcastCoordinator, LessonService, RoomsService, GovernanceBroker]:
    store = SqliteStateStore(
        tmp_path / "lesson-broadcast.sqlite3",
        workspace_home_id="lesson-broadcast-test",
    )
    store.initialize()
    broker = GovernanceBroker(
        store,
        clock=FakeClock(range(1, 200)),
        ids=FakeIdSource([f"broker-{index}" for index in range(1, 500)]),
    )
    lessons = LessonService(
        broker,
        clock=FakeClock(range(1, 200)),
        ids=FakeIdSource([f"lesson-{index}" for index in range(1, 500)]),
    )
    rooms = RoomsService(
        broker,
        clock=FakeClock(range(1, 200)),
        ids=FakeIdSource([f"room-{index}" for index in range(1, 500)]),
    )
    return (
        LessonBroadcastCoordinator(broker=broker, lessons=lessons, rooms=rooms),
        lessons,
        rooms,
        broker,
    )


def _activate(lessons: LessonService, lesson_id: str) -> None:
    lessons.propose(
        lesson_id=lesson_id,
        title="Inspect the actual state",
        rule="Verify first, then report.",
        category="verification",
        severity="LOW",
        proposer_id="sender",
        affected_peers=(),
    )
    lessons.approve(lesson_id, approved_by_actor_id="human:reviewer")
    lessons.activate(lesson_id, actor_id="sender")


def test_active_lesson_broadcast_delivers_mail_and_records_pending_delivery(
    tmp_path: Path,
) -> None:
    coordinator, lessons, rooms, broker = _services(tmp_path)
    _activate(lessons, "lesson-01")
    rooms.create_room(
        room_id="room-01",
        topic_id="topic-01",
        title="Broadcast room",
        creator_id="sender",
        participants=("sender", "peer-b", "peer-c"),
    )

    result = coordinator.broadcast(
        lesson_id="lesson-01",
        room_id="room-01",
        sender_instance_id="sender",
        sender_profile_id="sender",
        created_at=99,
    )

    assert result.recipient_profile_ids == ("peer-b", "peer-c")
    campaign = broker.get_target(result.campaign_target_id)
    assert campaign is not None
    assert campaign.state["kind"] == "lesson-broadcast"
    assert campaign.state["lesson_id"] == "lesson-01"
    assert campaign.state["recipients"] == (
        {"instance_id": "peer-b", "profile_id": "peer-b"},
        {"instance_id": "peer-c", "profile_id": "peer-c"},
    )

    for peer_id in ("peer-b", "peer-c"):
        inbox = rooms.check_inbox(
            room_id="room-01",
            caller_instance_id=peer_id,
            caller_profile_id=peer_id,
        )
        assert len(inbox) == 1
        assert inbox[0].state["message_type"] == "LESSON"
        assert inbox[0].state["resource_ref"] == "lesson:lesson-01"
        assert inbox[0].state["correlation_id"] == result.campaign_id
        delivery = broker.get_target(f"lesson-delivery:lesson-01:{peer_id}")
        assert delivery is not None
        assert delivery.state["status"] == "PENDING"
        assert delivery.state["delivery_method"] == "broadcast"

    assert rooms.check_inbox(
        room_id="room-01",
        caller_instance_id="sender",
        caller_profile_id="sender",
    ) == ()


def test_inactive_lesson_broadcast_is_rejected_before_writes(tmp_path: Path) -> None:
    coordinator, lessons, rooms, broker = _services(tmp_path)
    lessons.propose(
        lesson_id="lesson-inactive",
        title="Inactive lesson",
        rule="Do not deliver this.",
        category="verification",
        severity="LOW",
        proposer_id="sender",
        affected_peers=(),
    )
    rooms.create_room(
        room_id="room-inactive",
        topic_id="topic-inactive",
        title="Inactive room",
        creator_id="sender",
        participants=("sender", "peer-b"),
    )

    with pytest.raises(InvalidMutationError, match="active lesson"):
        coordinator.broadcast(
            lesson_id="lesson-inactive",
            room_id="room-inactive",
            sender_instance_id="sender",
            sender_profile_id="sender",
            created_at=99,
        )

    assert broker.list_targets("lesson-broadcast", "room-inactive") == ()
    assert broker.list_targets("inbox-message", "room-inactive") == ()
    assert broker.list_targets("lesson-delivery", "lesson-inactive") == ()
