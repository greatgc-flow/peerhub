from __future__ import annotations

from pathlib import Path

import pytest

from fakes import FakeClock, FakeIdSource
from peerhub.core.errors import InvalidMutationError, RecordNotFoundError
from peerhub.core.protocol import CommandID
from peerhub.governance.broker import GovernanceBroker
from peerhub.governance.contract import EffectIntent, MutationRequest
from peerhub.governance.rooms import HANDOFF_SECTIONS, RoomsService
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


def _insert_inbox_message(
    broker: GovernanceBroker,
    *,
    message_id: str,
    room_id: str,
    recipient_instance_id: str,
    recipient_profile_id: str,
    sequence: int,
) -> None:
    """Insert a valid delivery row with an explicitly chosen sequence."""

    broker.submit(
        MutationRequest(
            request_id=f"inject-{message_id}",
            command_id=CommandID(f"inject-command-{message_id}"),
            correlation_id=f"inject-correlation-{message_id}",
            client_id="rooms-test",
            command_type="test.inbox-message.inject",
            idempotency_key=f"inject-{message_id}",
            actor_id="peer-a",
            policy_revision="test-v1",
            target_id=f"inbox-message:{message_id}",
            expected_revision=0,
            operation="test.inbox-message.inject",
            desired_state={
                "kind": "inbox-message",
                "scope": room_id,
                "schema_version": 1,
                "message_id": message_id,
                "room_id": room_id,
                "sender": {
                    "instance_id": "peer-a",
                    "profile_id": "profile-a",
                },
                "recipient": {
                    "instance_id": recipient_instance_id,
                    "profile_id": recipient_profile_id,
                },
                "sequence": sequence,
                "body": message_id,
                "message_type": "MSG",
                "thread_ref": None,
                "resource_ref": None,
                "correlation_id": f"inject-correlation-{message_id}",
                "priority": None,
                "created_at": sequence,
                "promoted_to": None,
            },
            effect_intent=EffectIntent(kind="test.noop", payload={}),
        )
    )


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


def test_mailbox_delivery_is_private_read_only_and_cursor_scoped(
    tmp_path: Path,
) -> None:
    service, broker = _service(tmp_path)
    service.create_room(
        room_id="room-mailbox",
        topic_id="topic-mailbox",
        title="Mailbox",
        creator_id="peer-a",
        participants=("peer-a", "peer-b", "peer-c"),
    )
    to_b = service.send_message(
        room_id="room-mailbox",
        sender_instance_id="peer-a",
        sender_profile_id="profile-a",
        recipient_instance_id="peer-b",
        recipient_profile_id="profile-b",
        body="Only peer-b can read this",
        correlation_id="broadcast-01",
    )
    to_a = service.send_message(
        room_id="room-mailbox",
        sender_instance_id="peer-b",
        sender_profile_id="profile-b",
        recipient_instance_id="peer-a",
        recipient_profile_id="profile-a",
        body="Only peer-a can read this",
        correlation_id="broadcast-01",
    )

    inbox_for_a = service.check_inbox(
        room_id="room-mailbox",
        caller_instance_id="peer-a",
        caller_profile_id="profile-a",
    )
    inbox_for_b = service.check_inbox(
        room_id="room-mailbox",
        caller_instance_id="peer-b",
        caller_profile_id="profile-b",
    )
    inbox_for_c = service.check_inbox(
        room_id="room-mailbox",
        caller_instance_id="peer-c",
        caller_profile_id="profile-c",
    )

    assert tuple(message.state["body"] for message in inbox_for_a) == (
        "Only peer-a can read this",
    )
    assert tuple(message.state["body"] for message in inbox_for_b) == (
        "Only peer-b can read this",
    )
    assert inbox_for_c == ()
    assert inbox_for_a[0].state["correlation_id"] == "broadcast-01"
    assert inbox_for_b[0].state["correlation_id"] == "broadcast-01"
    assert inbox_for_a[0].state["priority"] is None
    assert inbox_for_b[0].state["priority"] is None
    # check_inbox is read-only: it must not create or advance a cursor.
    assert broker.get_target("inbox-cursor:room-mailbox:peer-a:profile-a") is None
    assert broker.get_target("inbox-cursor:room-mailbox:peer-b:profile-b") is None

    read = service.mark_read(
        room_id="room-mailbox",
        recipient_instance_id="peer-b",
        recipient_profile_id="profile-b",
        up_through_sequence=1,
    )
    assert read.receipt.target_id == "inbox-cursor:room-mailbox:peer-b:profile-b"
    assert service.check_inbox(
        room_id="room-mailbox",
        caller_instance_id="peer-b",
        caller_profile_id="profile-b",
    ) == ()
    all_for_b = service.check_inbox(
        room_id="room-mailbox",
        caller_instance_id="peer-b",
        caller_profile_id="profile-b",
        include_read=True,
    )
    cursor = broker.get_target("inbox-cursor:room-mailbox:peer-b:profile-b")
    assert tuple(message.state["body"] for message in all_for_b) == (
        "Only peer-b can read this",
    )
    assert cursor is not None
    assert cursor.state["read_through_sequence"] == 1
    assert cursor.state["last_read_message_id"] == to_b.receipt.target_id.removeprefix(
        "inbox-message:"
    )
    cursor_state_before_repeat = cursor.state
    service.mark_read(
        room_id="room-mailbox",
        recipient_instance_id="peer-b",
        recipient_profile_id="profile-b",
        up_through_sequence=1,
    )
    cursor_after_repeat = broker.get_target(
        "inbox-cursor:room-mailbox:peer-b:profile-b"
    )
    assert cursor_after_repeat is not None
    assert cursor_after_repeat.state == cursor_state_before_repeat
    assert to_a.receipt.target_id.startswith("inbox-message:")


def test_count_unread_messages_is_room_wide_and_decreases_after_mark_read(
    tmp_path: Path,
) -> None:
    service, broker = _service(tmp_path)
    service.create_room(
        room_id="room-unread",
        topic_id="topic-unread",
        title="Unread aggregate",
        creator_id="peer-a",
        participants=("peer-a", "peer-b", "peer-c"),
    )
    service.create_room(
        room_id="room-other",
        topic_id="topic-other",
        title="Other room",
        creator_id="peer-a",
        participants=("peer-a", "peer-b"),
    )
    for index in range(3):
        service.send_message(
            room_id="room-unread",
            sender_instance_id="peer-a",
            sender_profile_id="profile-a",
            recipient_instance_id="peer-b",
            recipient_profile_id="profile-b",
            body=f"for-b-{index}",
        )
    for index in range(2):
        service.send_message(
            room_id="room-unread",
            sender_instance_id="peer-a",
            sender_profile_id="profile-a",
            recipient_instance_id="peer-c",
            recipient_profile_id="profile-c",
            body=f"for-c-{index}",
        )
    service.send_message(
        room_id="room-other",
        sender_instance_id="peer-a",
        sender_profile_id="profile-a",
        recipient_instance_id="peer-b",
        recipient_profile_id="profile-b",
        body="excluded by room scope",
    )

    messages_before = broker.list_targets("inbox-message", "room-unread")
    cursors_before = broker.list_targets("inbox-cursor", "room-unread")
    assert service.count_unread_messages(room_id="room-unread") == 5
    assert service.count_unread_messages(room_id="room-unread") == 5
    assert broker.list_targets("inbox-message", "room-unread") == messages_before
    assert broker.list_targets("inbox-cursor", "room-unread") == cursors_before == ()

    service.mark_read(
        room_id="room-unread",
        recipient_instance_id="peer-b",
        recipient_profile_id="profile-b",
        up_through_sequence=2,
    )

    assert service.count_unread_messages(room_id="room-unread") == 3
    assert service.count_unread_messages(room_id="room-other") == 1
    service.send_message(
        room_id="room-unread",
        sender_instance_id="peer-a",
        sender_profile_id="profile-a",
        recipient_instance_id="peer-b",
        recipient_profile_id="profile-b",
        body="newer than peer-b's cursor",
    )
    assert service.count_unread_messages(room_id="room-unread") == 4


def test_count_unread_messages_empty_and_cleared_rooms_start_at_zero(
    tmp_path: Path,
) -> None:
    service, _ = _service(tmp_path)
    service.create_room(
        room_id="room-empty",
        topic_id="topic-empty",
        title="Empty room",
        creator_id="peer-a",
        participants=(),
    )
    service.create_room(
        room_id="room-old",
        topic_id="topic-old",
        title="Old room",
        creator_id="peer-a",
        participants=("peer-a", "peer-b"),
    )
    service.send_message(
        room_id="room-old",
        sender_instance_id="peer-a",
        sender_profile_id="profile-a",
        recipient_instance_id="peer-b",
        recipient_profile_id="profile-b",
        body="retained in the old room",
    )

    assert service.count_unread_messages(room_id="room-empty") == 0
    with pytest.raises(RecordNotFoundError):
        service.count_unread_messages(room_id="missing-room")
    service.clear_room(
        "room-old",
        new_room_id="room-fresh",
        subject="Fresh room",
        actor_id="peer-a",
    )
    assert service.count_unread_messages(room_id="room-fresh") == 0
    assert service.count_unread_messages(room_id="room-old") == 1


def test_count_unread_messages_counts_duplicate_and_gapped_sequence_rows(
    tmp_path: Path,
) -> None:
    service, broker = _service(tmp_path)
    service.create_room(
        room_id="room-sequences",
        topic_id="topic-sequences",
        title="Sequence edge cases",
        creator_id="peer-a",
        participants=("peer-a", "peer-b"),
    )
    for index, sequence in enumerate((1, 4, 4, 9), start=1):
        _insert_inbox_message(
            broker,
            message_id=f"sequence-{index}",
            room_id="room-sequences",
            recipient_instance_id="peer-b",
            recipient_profile_id="profile-b",
            sequence=sequence,
        )
    service.mark_read(
        room_id="room-sequences",
        recipient_instance_id="peer-b",
        recipient_profile_id="profile-b",
        up_through_sequence=2,
    )

    # Count the three physical rows above the cursor.  max(sequence) - cursor
    # would incorrectly report seven because the stream has duplicates/gaps.
    assert service.count_unread_messages(room_id="room-sequences") == 3


def test_promote_mailbox_message_creates_thread_message_and_marks_source(
    tmp_path: Path,
) -> None:
    service, broker = _service(tmp_path)
    service.create_room(
        room_id="room-promote",
        topic_id="topic-promote",
        title="Promotion",
        creator_id="peer-a",
        participants=("peer-a", "peer-b"),
    )
    service.create_thread(
        thread_id="thread-decisions",
        room_id="room-promote",
        subject="Decisions",
        creator_id="peer-a",
    )
    delivery = service.send_message(
        room_id="room-promote",
        sender_instance_id="peer-a",
        sender_profile_id="profile-a",
        recipient_instance_id="peer-b",
        recipient_profile_id="profile-b",
        body="Promote this decision",
    )
    inbox_message_id = delivery.receipt.target_id.removeprefix("inbox-message:")

    promotion = service.promote_message(
        message_id=inbox_message_id,
        room_id="room-promote",
        thread_id="thread-decisions",
        actor_id="peer-b",
    )

    source = broker.get_target(delivery.receipt.target_id)
    thread_messages = broker.list_targets("message", "room-promote")
    assert promotion.receipt.target_id == delivery.receipt.target_id
    assert source is not None
    assert source.revision == 2
    assert source.state["promoted_to"] == "thread-decisions"
    assert len(thread_messages) == 1
    promoted = thread_messages[0]
    assert promoted.state["thread_id"] == "thread-decisions"
    assert promoted.state["message_type"] == "MSG_PROMOTED"
    assert promoted.state["body"] == "Promote this decision"
    assert promoted.state["metadata"] == {
        "promoted_from_inbox_message_id": inbox_message_id,
    }


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


def test_context_fill_reuses_projection_filters_sections_and_writes_nothing(
    tmp_path: Path,
) -> None:
    service, broker = _service(tmp_path)
    service.create_room(
        room_id="room-context",
        topic_id="topic-context",
        title="Context",
        creator_id="peer-a",
        participants=(),
    )
    service.set_room_goal(
        room_id="room-context",
        goal="Build bounded startup context",
        actor_id="peer-a",
    )
    for index in range(1, 7):
        service.append_handoff_note(
            room_id="room-context",
            section="RECENT_COMPLETED",
            text=f"context-completed-{index}",
            actor_id="peer-a",
        )
    service.append_handoff_note(
        room_id="room-context",
        section="KEY_DECISIONS",
        text="Reuse the checkpoint projection",
        actor_id="peer-a",
    )

    note_count = len(broker.list_targets("continuity-note", "room-context"))
    all_sections = service.context_fill(
        "room-context",
        session_id="unregistered-session-metadata",
    )
    filtered = service.context_fill(
        "room-context",
        session_id="unregistered-session-metadata",
        sections=("KEY_DECISIONS", "GOAL"),
    )

    assert all_sections["room_id"] == "room-context"
    assert all_sections["session_id"] == "unregistered-session-metadata"
    assert tuple(all_sections["sections"]) == HANDOFF_SECTIONS
    assert all_sections["sections"]["RECENT_COMPLETED"]["items"] == (
        "context-completed-2",
        "context-completed-3",
        "context-completed-4",
        "context-completed-5",
        "context-completed-6",
    )
    assert all_sections["truncated"] is True
    assert all_sections["truncated_sections"] == ("RECENT_COMPLETED",)
    assert tuple(filtered["sections"]) == ("KEY_DECISIONS", "GOAL")
    assert filtered["sections"]["KEY_DECISIONS"]["items"] == (
        "Reuse the checkpoint projection",
    )
    assert filtered["truncated"] is False
    assert "markdown" not in filtered
    assert "checkpoint_id" not in filtered
    assert len(broker.list_targets("checkpoint-created", "room-context")) == 0
    assert len(broker.list_targets("continuity-note", "room-context")) == note_count

    service.checkpoint("room-context", actor_id="peer-a")
    service.context_fill(
        "room-context",
        session_id="unregistered-session-metadata",
    )
    assert len(broker.list_targets("checkpoint-created", "room-context")) == 1
    assert len(broker.list_targets("continuity-note", "room-context")) == note_count

    with pytest.raises(ValueError, match="session_id"):
        service.context_fill("room-context", session_id="")
    with pytest.raises(ValueError, match="unknown context section"):
        service.context_fill(
            "room-context",
            session_id="session",
            sections=("DECISIONS",),
        )
