"""Room, thread, and message domain operations over governed targets."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from peerhub.core.context import Clock, IdSource
from peerhub.core.errors import InvalidMutationError, RecordNotFoundError
from peerhub.core.protocol import CommandID, JsonValue

from .broker import GovernanceBroker
from .contract import EffectIntent, MutationRequest, MutationSubmission


class RoomsService:
    """Create room/thread records and append independent message records."""

    def __init__(self, broker: GovernanceBroker, *, clock: Clock, ids: IdSource) -> None:
        self._broker = broker
        self._clock = clock
        self._ids = ids

    def create_room(
        self,
        *,
        room_id: str,
        topic_id: str,
        title: str,
        creator_id: str,
        participants: Sequence[str],
    ) -> MutationSubmission:
        timestamp = self._clock.now()
        participant_records = tuple(
            {
                "instance_id": participant,
                "profile_id": participant,
                "role": "member",
                "joined_at": timestamp,
            }
            for participant in participants
        )
        state: dict[str, JsonValue] = {
            "kind": "room",
            "scope": None,
            "schema_version": 1,
            "room_id": room_id,
            "topic_id": topic_id,
            "title": title,
            "status": "active",
            "created_at": timestamp,
            "created_by": {"instance_id": creator_id, "profile_id": creator_id},
            "participants": participant_records,
            "thread_ids": (),
            "session_bindings": (),
            "message_projection": {
                "message_count": 0,
                "last_message_id": None,
                "last_message_at": None,
            },
            "retention": {"mode": "retained"},
        }
        return self._submit(room_id, 0, creator_id, "room.create", state)

    def create_thread(
        self,
        *,
        thread_id: str,
        room_id: str,
        subject: str,
        creator_id: str,
    ) -> MutationSubmission:
        room = self._broker.get_target(room_id)
        if room is None:
            raise RecordNotFoundError("room", room_id)
        if room.state.get("kind") != "room":
            raise InvalidMutationError("target is not a room")
        timestamp = self._clock.now()
        state: dict[str, JsonValue] = {
            "kind": "thread",
            "scope": room_id,
            "schema_version": 1,
            "thread_id": thread_id,
            "room_id": room_id,
            "subject": subject,
            "status": "open",
            "created_at": timestamp,
            "created_by": {"instance_id": creator_id, "profile_id": creator_id},
            "participant_keys": (),
            "message_projection": {
                "message_count": 0,
                "first_message_id": None,
                "last_message_id": None,
                "last_message_at": None,
                "preview": None,
            },
        }
        return self._submit(thread_id, 0, creator_id, "thread.create", state)

    def clear_room(
        self,
        old_room_id: str,
        *,
        new_room_id: str,
        subject: str,
        actor_id: str,
    ) -> MutationSubmission:
        old_room = self._broker.get_target(old_room_id)
        if old_room is None:
            raise RecordNotFoundError("room", old_room_id)
        if old_room.state.get("kind") != "room":
            raise InvalidMutationError("target is not a room")
        # Legacy clear-room starts a wholly fresh room boundary. The old
        # target is intentionally read-only: its history remains retained.
        return self.create_room(
            room_id=new_room_id,
            topic_id=subject,
            title=subject,
            creator_id=actor_id,
            participants=(),
        )

    def append_message(
        self,
        *,
        message_id: str,
        room_id: str,
        thread_id: str,
        author_id: str,
        body: str,
    ) -> MutationSubmission:
        thread = self._broker.get_target(thread_id)
        if thread is None:
            raise RecordNotFoundError("thread", thread_id)
        if thread.state.get("kind") != "thread" or thread.state.get("room_id") != room_id:
            raise InvalidMutationError("thread is not in the requested room")
        # KNOWN LIMITATION: sequence is computed by scanning existing
        # messages, not CAS-protected -- concurrent appends to the same
        # thread could compute the same sequence number. Acceptable for
        # this increment's single-dispatcher usage; a real fix needs a
        # dedicated per-thread sequence-counter TargetState with its own
        # CAS increment before this sees concurrent writers.
        existing = self._broker.list_targets("message", room_id)
        sequence = 1 + max(
            (self._sequence_for_thread(target.state, thread_id) for target in existing),
            default=0,
        )
        timestamp = self._clock.now()
        state: dict[str, JsonValue] = {
            "kind": "message",
            "scope": room_id,
            "schema_version": 1,
            "message_id": message_id,
            "room_id": room_id,
            "thread_id": thread_id,
            "sequence": sequence,
            "author": {"instance_id": author_id, "profile_id": author_id},
            "created_at": timestamp,
            "message_type": "text",
            "body": body,
            "reply_to": None,
            "metadata": {},
        }
        return self._submit(
            f"message:{message_id}", 0, author_id, "message.append", state
        )

    @staticmethod
    def _sequence_for_thread(state: Mapping[str, JsonValue], thread_id: str) -> int:
        if state.get("thread_id") != thread_id:
            return 0
        sequence = state.get("sequence")
        return sequence if isinstance(sequence, int) and not isinstance(sequence, bool) else 0

    def _submit(
        self,
        target_id: str,
        expected_revision: int,
        actor_id: str,
        operation: str,
        desired_state: dict[str, JsonValue],
    ) -> MutationSubmission:
        request_id = self._ids.new_id("rooms-request")
        return self._broker.submit(
            MutationRequest(
                request_id=request_id,
                command_id=CommandID(self._ids.new_id("rooms-command")),
                correlation_id=self._ids.new_id("rooms-correlation"),
                client_id="peerhub.rooms",
                command_type=operation,
                idempotency_key=request_id,
                actor_id=actor_id,
                policy_revision="protocol-v2",
                target_id=target_id,
                expected_revision=expected_revision,
                operation=operation,
                desired_state=desired_state,
                effect_intent=EffectIntent(kind="rooms.noop", payload={}),
            )
        )
