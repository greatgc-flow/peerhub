from __future__ import annotations

from pathlib import Path

import pytest

from fakes import FakeClock, FakeIdSource
from peerhub.core.errors import InvalidMutationError
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


def test_reactions_append_events_and_keep_current_state_projection(tmp_path: Path) -> None:
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

    submission = service.react(
        message_id="message-01",
        room_id="room-01",
        actor_instance_id="peer-a",
        actor_profile_id="default",
        reaction_type="ACK",
    )
    state = service.get_reaction_state(
        "message-01", "peer-a", "default", "ACK"
    )
    events = broker.list_targets("reaction-event", "room-01")
    assert submission.receipt.target_id.startswith("reaction-state:")
    assert state is not None
    assert state.revision == 1
    assert state.state["status"] == "ACTIVE"
    assert state.state["latest_action"] == "ADD"
    assert len(events) == 1
    assert events[0].revision == 1
    assert events[0].state["action"] == "ADD"

    service.react(
        message_id="message-01",
        room_id="room-01",
        actor_instance_id="peer-a",
        actor_profile_id="default",
        reaction_type="ACK",
    )
    state = service.get_reaction_state(
        "message-01", "peer-a", "default", "ACK"
    )
    assert state is not None
    assert state.revision == 2
    assert state.state["status"] == "ACTIVE"
    assert len(broker.list_targets("reaction-event", "room-01")) == 2

    service.unreact(
        message_id="message-01",
        room_id="room-01",
        actor_instance_id="peer-a",
        actor_profile_id="default",
        reaction_type="ACK",
    )
    state = service.get_reaction_state(
        "message-01", "peer-a", "default", "ACK"
    )
    events = broker.list_targets("reaction-event", "room-01")
    assert state is not None
    assert state.revision == 3
    assert state.state["status"] == "REMOVED"
    assert events[-1].state["action"] == "REMOVE"

    service.react(
        message_id="message-01",
        room_id="room-01",
        actor_instance_id="peer-a",
        actor_profile_id="default",
        reaction_type="ACK",
    )
    state = service.get_reaction_state(
        "message-01", "peer-a", "default", "ACK"
    )
    events = broker.list_targets("reaction-event", "room-01")
    assert state is not None
    assert state.revision == 4
    assert state.state["status"] == "ACTIVE"
    assert events[-1].state["action"] == "ADD"
    assert len(events) == 4


def test_handoff_notes_generate_a_capped_rebuildable_checkpoint(
    tmp_path: Path,
) -> None:
    service, broker = _service(tmp_path)
    service.create_room(
        room_id="room-continuity",
        topic_id="topic-continuity",
        title="Continuity",
        creator_id="peer-a",
        participants=("peer-a",),
    )
    service.set_room_goal(
        room_id="room-continuity",
        goal="Ship the handoff projection",
        actor_id="peer-a",
    )
    for index in range(1, 7):
        service.append_handoff_note(
            room_id="room-continuity",
            section="RECENT_COMPLETED",
            text=f"completed-{index}",
            actor_id="peer-a",
        )
    for section, text in (
        ("PENDING_ISSUES", "Resolve export semantics"),
        ("KEY_DECISIONS", "Notes remain authoritative"),
        ("CONSENSUS_HISTORY", "cc and cx agreed"),
        ("ACTIVE_THREADS", "thread-continuity"),
    ):
        service.append_handoff_note(
            room_id="room-continuity",
            section=section,
            text=text,
            actor_id="peer-a",
        )

    checkpoint = service.checkpoint(
        "room-continuity",
        actor_id="peer-a",
        idempotency_key="checkpoint-1",
    )
    sections = checkpoint["sections"]
    assert sections["GOAL"]["value"] == "Ship the handoff projection"
    assert sections["RECENT_COMPLETED"]["items"] == (
        "completed-2",
        "completed-3",
        "completed-4",
        "completed-5",
        "completed-6",
    )
    assert sections["RECENT_COMPLETED"]["truncated"] is True
    assert sections["PENDING_ISSUES"]["items"] == (
        "Resolve export semantics",
    )
    assert checkpoint["as_of_event_seq"] == 10
    assert checkpoint["truncated_sections"] == ("RECENT_COMPLETED",)
    assert "## GOAL" in checkpoint["markdown"]
    assert "## CONSENSUS_HISTORY" in checkpoint["markdown"]
    assert "- cc and cx agreed" in checkpoint["markdown"]

    goal_target = broker.get_target("room-goal:room-continuity")
    notes = broker.list_targets("continuity-note", "room-continuity")
    events = broker.list_targets("checkpoint-created", "room-continuity")
    assert goal_target is not None
    assert goal_target.state["goal"] == "Ship the handoff projection"
    assert len(notes) == 10
    assert len(events) == 1
    assert events[0].state["checkpoint"]["sections"][
        "RECENT_COMPLETED"
    ]["items"] == (
        "completed-2",
        "completed-3",
        "completed-4",
        "completed-5",
        "completed-6",
    )

    replay = service.checkpoint(
        "room-continuity",
        actor_id="peer-a",
        idempotency_key="checkpoint-1",
    )
    assert replay == checkpoint
    assert len(broker.list_targets("checkpoint-created", "room-continuity")) == 1

    with pytest.raises(InvalidMutationError, match="section must be"):
        service.append_handoff_note(
            room_id="room-continuity",
            section="GOAL",
            text="GOAL is a scalar projection",
            actor_id="peer-a",
        )


def test_checkpoint_total_budget_trims_recent_completed_first(
    tmp_path: Path,
) -> None:
    service, _ = _service(tmp_path)
    service.create_room(
        room_id="room-budget",
        topic_id="topic-budget",
        title="Budget",
        creator_id="peer-a",
        participants=(),
    )
    service.set_room_goal(
        room_id="room-budget",
        goal="g" * 11_700,
        actor_id="peer-a",
    )
    service.append_handoff_note(
        room_id="room-budget",
        section="RECENT_COMPLETED",
        text="x" * 1_000,
        actor_id="peer-a",
    )

    checkpoint = service.checkpoint("room-budget", actor_id="peer-a")

    assert len(checkpoint["markdown"]) <= 12_000
    assert checkpoint["sections"]["RECENT_COMPLETED"]["items"] == ()
    assert checkpoint["sections"]["RECENT_COMPLETED"]["truncated"] is True
    assert checkpoint["sections"]["GOAL"]["truncated"] is False
