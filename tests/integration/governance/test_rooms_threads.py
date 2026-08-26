from __future__ import annotations

from pathlib import Path

from fakes import FakeClock, FakeIdSource
from peerhub.governance.broker import GovernanceBroker
from peerhub.governance.rooms import RoomsService
from peerhub.persistence.sqlite import SqliteStateStore


def _service(tmp_path: Path) -> tuple[RoomsService, GovernanceBroker]:
    store = SqliteStateStore(
        tmp_path / "rooms.sqlite3",
        workspace_home_id="rooms-test",
    )
    store.initialize()
    broker = GovernanceBroker(
        store,
        clock=FakeClock(range(1, 100)),
        ids=FakeIdSource([f"id-{i}" for i in range(1, 200)]),
    )
    service = RoomsService(
        broker,
        clock=FakeClock(range(1, 100)),
        ids=FakeIdSource([f"domain-{i}" for i in range(1, 200)]),
    )
    return service, broker


def test_create_room_writes_canonical_room_target(tmp_path: Path) -> None:
    service, broker = _service(tmp_path)

    service.create_room(
        room_id="room-01",
        topic_id="topic-01",
        title="Architecture",
        creator_id="peer-a",
        participants=("peer-a", "peer-b"),
    )

    target = broker.get_target("room-01")
    assert target is not None
    assert target.revision == 1
    assert target.state["kind"] == "room"
    assert target.state["room_id"] == "room-01"
    assert target.state["status"] == "active"
    assert target.state["thread_ids"] == ()
    assert target.state["message_projection"]["message_count"] == 0


def test_clear_room_creates_fresh_room_without_mutating_old_target(tmp_path: Path) -> None:
    service, broker = _service(tmp_path)
    service.create_room(
        room_id="room-old",
        topic_id="topic-old",
        title="Old room",
        creator_id="peer-a",
        participants=("peer-a", "peer-b"),
    )
    old_before = broker.get_target("room-old")
    assert old_before is not None

    submission = service.clear_room(
        "room-old",
        new_room_id="room-new",
        subject="Fresh subject",
        actor_id="peer-a",
    )

    new_room = broker.get_target("room-new")
    old_after = broker.get_target("room-old")
    assert submission.receipt.target_id == "room-new"
    assert new_room is not None
    assert new_room.state["kind"] == "room"
    assert new_room.state["room_id"] == "room-new"
    assert new_room.state["title"] == "Fresh subject"
    assert new_room.state["topic_id"] == "Fresh subject"
    assert new_room.state["participants"] == ()
    assert new_room.state["thread_ids"] == ()
    assert old_after is not None
    assert (old_after.revision, old_after.state) == (old_before.revision, old_before.state)


def test_create_thread_is_room_scoped(tmp_path: Path) -> None:
    service, broker = _service(tmp_path)
    service.create_room(
        room_id="room-01",
        topic_id="topic-01",
        title="Architecture",
        creator_id="peer-a",
        participants=("peer-a",),
    )

    service.create_thread(
        thread_id="thread-01",
        room_id="room-01",
        subject="Decisions",
        creator_id="peer-a",
    )

    target = broker.get_target("thread-01")
    assert target is not None
    assert target.state["kind"] == "thread"
    assert target.state["scope"] == "room-01"
    assert target.state["room_id"] == "room-01"
    assert target.state["message_projection"]["message_count"] == 0


def test_append_message_creates_separate_immutable_target(tmp_path: Path) -> None:
    service, broker = _service(tmp_path)
    service.create_room(
        room_id="room-01",
        topic_id="topic-01",
        title="Architecture",
        creator_id="peer-a",
        participants=("peer-a",),
    )
    service.create_thread(
        thread_id="thread-01",
        room_id="room-01",
        subject="Decisions",
        creator_id="peer-a",
    )

    service.append_message(
        message_id="message-01",
        room_id="room-01",
        thread_id="thread-01",
        author_id="peer-a",
        body="Hello",
    )

    message = broker.get_target("message:message-01")
    thread = broker.get_target("thread-01")
    assert message is not None
    assert thread is not None
    assert message.revision == 1
    assert message.state["kind"] == "message"
    assert message.state["scope"] == "room-01"
    assert message.state["sequence"] == 1
    assert message.state["body"] == "Hello"
    assert thread.revision == 1
