"""Room, thread, and message domain operations over governed targets."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence

from peerhub.core.context import Clock, IdSource
from peerhub.core.errors import InvalidMutationError, RecordNotFoundError
from peerhub.core.protocol import CommandID, JsonValue

from .broker import GovernanceBroker
from .contract import EffectIntent, MutationRequest, MutationSubmission, TargetState


HANDOFF_LIST_SECTIONS = (
    "RECENT_COMPLETED",
    "PENDING_ISSUES",
    "KEY_DECISIONS",
    "CONSENSUS_HISTORY",
    "ACTIVE_THREADS",
)
HANDOFF_SECTIONS = ("GOAL", *HANDOFF_LIST_SECTIONS)
HANDOFF_SECTION_LIMITS = {
    "RECENT_COMPLETED": 5,
    "PENDING_ISSUES": 3,
    "KEY_DECISIONS": 3,
    "CONSENSUS_HISTORY": 10,
    "ACTIVE_THREADS": 5,
}
HANDOFF_MAX_CHARS = 12_000


class RoomsService:
    """Create room/thread records and append independent message records."""

    def __init__(self, broker: GovernanceBroker, *, clock: Clock, ids: IdSource) -> None:
        self._broker = broker
        self._clock = clock
        self._ids = ids

    def get_target(self, target_id: str) -> TargetState | None:
        return self._broker.get_target(target_id)

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

    def react(
        self,
        *,
        message_id: str,
        room_id: str,
        actor_instance_id: str,
        actor_profile_id: str,
        reaction_type: str,
    ) -> MutationSubmission:
        """Append an ADD reaction event and refresh its current projection."""

        return self._record_reaction(
            message_id=message_id,
            room_id=room_id,
            actor_instance_id=actor_instance_id,
            actor_profile_id=actor_profile_id,
            reaction_type=reaction_type,
            action="ADD",
        )

    def unreact(
        self,
        *,
        message_id: str,
        room_id: str,
        actor_instance_id: str,
        actor_profile_id: str,
        reaction_type: str,
    ) -> MutationSubmission:
        """Append a REMOVE reaction event and retain a removed projection."""

        return self._record_reaction(
            message_id=message_id,
            room_id=room_id,
            actor_instance_id=actor_instance_id,
            actor_profile_id=actor_profile_id,
            reaction_type=reaction_type,
            action="REMOVE",
        )

    def get_reaction_state(
        self,
        message_id: str,
        actor_instance_id: str,
        actor_profile_id: str,
        reaction_type: str,
    ) -> TargetState | None:
        """Return this actor's current projection for one message reaction."""

        return self._broker.get_target(
            self._reaction_state_target_id(
                message_id,
                actor_instance_id,
                actor_profile_id,
                reaction_type,
            )
        )

    def append_handoff_note(
        self,
        *,
        room_id: str,
        section: str,
        text: str,
        actor_id: str,
    ) -> MutationSubmission:
        """Append one immutable note to a room's continuity event stream."""

        self._require_room(room_id)
        if section not in HANDOFF_LIST_SECTIONS:
            raise InvalidMutationError(
                "section must be one of " + ", ".join(HANDOFF_LIST_SECTIONS)
            )
        if not text:
            raise InvalidMutationError("handoff note text must be nonempty")

        existing_notes = self._broker.list_targets("continuity-note", room_id)
        sequence = 1 + max(
            (self._continuity_note_sequence(target) for target in existing_notes),
            default=0,
        )
        note_id = self._ids.new_id("continuity-note")
        state: dict[str, JsonValue] = {
            "kind": "continuity-note",
            "scope": room_id,
            "schema_version": 1,
            "note_id": note_id,
            "room_id": room_id,
            "section": section,
            "text": text,
            "actor_id": actor_id,
            "created_at": self._clock.now(),
            "sequence": sequence,
        }
        return self._submit(
            f"continuity-note:{note_id}",
            0,
            actor_id,
            "continuity.note.append",
            state,
        )

    def set_room_goal(
        self,
        *,
        room_id: str,
        goal: str,
        actor_id: str,
    ) -> MutationSubmission:
        """Set the room's scalar GOAL projection independently of notes."""

        self._require_room(room_id)
        target_id = self._room_goal_target_id(room_id)
        current = self._broker.get_target(target_id)
        state: dict[str, JsonValue] = {
            "kind": "room-goal",
            "scope": room_id,
            "schema_version": 1,
            "room_id": room_id,
            "goal": goal,
            "actor_id": actor_id,
            "updated_at": self._clock.now(),
        }
        return self._submit(
            target_id,
            0 if current is None else current.revision,
            actor_id,
            "continuity.goal.set",
            state,
        )

    def checkpoint(
        self,
        room_id: str,
        *,
        actor_id: str = "peerhub",
        idempotency_key: str | None = None,
        idempotency_scope: str = "peerhub",
    ) -> Mapping[str, JsonValue]:
        """Generate and record a bounded handoff projection for one room.

        Continuity notes remain authoritative.  The snapshot embedded in the
        immutable checkpoint event is a reproducible export, not a mutable
        source of truth.
        """

        room = self._require_room(room_id)
        checkpoint_id = self._checkpoint_id(
            idempotency_key,
            idempotency_scope=idempotency_scope,
        )
        event_target_id = f"checkpoint-created:{checkpoint_id}"
        existing = self._broker.get_target(event_target_id)
        if existing is not None:
            if (
                existing.state.get("kind") != "checkpoint-created"
                or existing.state.get("room_id") != room_id
                or existing.state.get("actor_id") != actor_id
            ):
                raise InvalidMutationError(
                    "checkpoint idempotency key is already bound to different parameters"
                )
            stored = existing.state.get("checkpoint")
            if not isinstance(stored, Mapping):
                raise InvalidMutationError("stored checkpoint event is malformed")
            return stored

        projection = self._build_continuity_projection(room)
        result: dict[str, JsonValue] = {"checkpoint_id": checkpoint_id}
        result.update(projection)
        result["created_at"] = self._clock.now()
        event_state: dict[str, JsonValue] = {
            "kind": "checkpoint-created",
            "scope": room_id,
            "schema_version": 1,
            "event_id": checkpoint_id,
            "room_id": room_id,
            "actor_id": actor_id,
            "created_at": result["created_at"],
            "as_of_event_seq": result["as_of_event_seq"],
            "checkpoint": result,
        }
        self._submit(
            event_target_id,
            0,
            actor_id,
            "continuity.checkpoint_created",
            event_state,
        )
        return result

    def context_fill(
        self,
        room_id: str,
        *,
        session_id: str,
        sections: Sequence[str] | None = None,
    ) -> Mapping[str, JsonValue]:
        """Return bounded room continuity without recording an event.

        ``session_id`` is echoed as context provenance.  It is deliberately
        not validated against ``RoomParticipationCoordinator`` so this read
        model remains independent of the room-session lifecycle store.
        """

        if type(session_id) is not str or not session_id:
            raise ValueError("session_id must be a nonempty string")
        selected_sections = self._validate_context_sections(sections)
        room = self._require_room(room_id)
        projection = self._build_continuity_projection(room)
        projected_sections = projection["sections"]
        if not isinstance(projected_sections, Mapping):
            raise RuntimeError("continuity projection sections are malformed")

        filtered_sections: dict[str, JsonValue] = {}
        truncated_sections: list[str] = []
        for section in selected_sections:
            payload = projected_sections.get(section)
            if not isinstance(payload, Mapping):
                raise RuntimeError(
                    f"continuity projection section {section} is malformed"
                )
            filtered_sections[section] = payload
            if payload.get("truncated") is True:
                truncated_sections.append(section)

        return {
            "room_id": room_id,
            "session_id": session_id,
            "as_of_event_seq": projection["as_of_event_seq"],
            "sections": filtered_sections,
            "truncated": bool(truncated_sections),
            "truncated_sections": tuple(truncated_sections),
            "source": projection["source"],
        }

    def _build_continuity_projection(
        self,
        room: TargetState,
    ) -> dict[str, JsonValue]:
        """Aggregate the shared checkpoint/context-fill read projection."""

        room_id = room.target_id
        goal_target = self._broker.get_target(self._room_goal_target_id(room_id))
        goal = ""
        goal_revision = 0
        if goal_target is not None:
            stored_goal = goal_target.state.get("goal")
            if isinstance(stored_goal, str):
                goal = stored_goal
            goal_revision = goal_target.revision

        note_targets = tuple(
            sorted(
                self._broker.list_targets("continuity-note", room_id),
                key=self._continuity_note_sort_key,
            )
        )
        as_of_event_seq = max(
            (self._continuity_note_sequence(target) for target in note_targets),
            default=0,
        )
        source_items: dict[str, list[str]] = {
            section: [] for section in HANDOFF_LIST_SECTIONS
        }
        for target in note_targets:
            section = target.state.get("section")
            text = target.state.get("text")
            if (
                isinstance(section, str)
                and section in source_items
                and isinstance(text, str)
            ):
                source_items[section].append(text)

        items: dict[str, list[str]] = {}
        truncated_sections: set[str] = set()
        source_counts: dict[str, int] = {}
        for section in HANDOFF_LIST_SECTIONS:
            section_items = source_items[section]
            source_counts[section] = len(section_items)
            limit = HANDOFF_SECTION_LIMITS[section]
            if len(section_items) > limit:
                truncated_sections.add(section)
            items[section] = list(section_items[-limit:])

        markdown = self._render_handoff_markdown(goal, items)
        # The legacy policy trims RECENT_COMPLETED first.  If that cannot
        # satisfy the total budget, discard the oldest retained entries from
        # the other bounded sections, then trim the scalar GOAL as a final
        # safety valve.  Durable source notes are never changed.
        budget_order = (
            "RECENT_COMPLETED",
            "CONSENSUS_HISTORY",
            "ACTIVE_THREADS",
            "PENDING_ISSUES",
            "KEY_DECISIONS",
        )
        while len(markdown) > HANDOFF_MAX_CHARS:
            trimmed = False
            for section in budget_order:
                if items[section]:
                    items[section].pop(0)
                    truncated_sections.add(section)
                    trimmed = True
                    break
            if not trimmed:
                overflow = len(markdown) - HANDOFF_MAX_CHARS
                if not goal:
                    break
                goal = goal[: max(0, len(goal) - overflow)]
                truncated_sections.add("GOAL")
            markdown = self._render_handoff_markdown(goal, items)

        section_payload: dict[str, JsonValue] = {
            "GOAL": {
                "value": goal,
                "truncated": "GOAL" in truncated_sections,
            }
        }
        for section in HANDOFF_LIST_SECTIONS:
            section_payload[section] = {
                "items": tuple(items[section]),
                "truncated": section in truncated_sections,
                "source_count": source_counts[section],
            }

        return {
            "room_id": room_id,
            # This is the position in the authoritative continuity-note
            # stream.  The governance broker does not expose a global room
            # event sequence, so the source stream and position are explicit.
            "as_of_event_seq": as_of_event_seq,
            "source": {
                "kind": "continuity-note",
                "room_revision": room.revision,
                "goal_revision": goal_revision,
                "note_count": len(note_targets),
            },
            "sections": section_payload,
            "truncated": bool(truncated_sections),
            "truncated_sections": tuple(
                section
                for section in HANDOFF_SECTIONS
                if section in truncated_sections
            ),
            "markdown": markdown,
        }

    @staticmethod
    def _validate_context_sections(
        sections: Sequence[str] | None,
    ) -> tuple[str, ...]:
        if sections is None:
            return HANDOFF_SECTIONS
        if isinstance(sections, str):
            raise ValueError("sections must be a sequence of section names")
        selected = tuple(sections)
        if not selected:
            raise ValueError("sections must contain at least one section name")
        if not all(type(section) is str for section in selected):
            raise ValueError("sections must contain only strings")
        unknown = tuple(
            section for section in selected if section not in HANDOFF_SECTIONS
        )
        if unknown:
            raise ValueError(
                "unknown context section(s): " + ", ".join(unknown)
            )
        if len(set(selected)) != len(selected):
            raise ValueError("sections must not contain duplicates")
        return selected

    @staticmethod
    def _render_handoff_markdown(
        goal: str,
        items: Mapping[str, Sequence[str]],
    ) -> str:
        lines = ["## GOAL"]
        if goal:
            lines.append(goal)
        for section in HANDOFF_LIST_SECTIONS:
            lines.extend(("", f"## {section}"))
            lines.extend(f"- {item}" for item in items[section])
        return "\n".join(lines)

    def _checkpoint_id(
        self,
        idempotency_key: str | None,
        *,
        idempotency_scope: str,
    ) -> str:
        if idempotency_key:
            digest = hashlib.sha256(
                (
                    "coordination.checkpoint.create\0"
                    f"{idempotency_scope}\0{idempotency_key}"
                ).encode("utf-8")
            ).hexdigest()
            return digest[:32]
        return self._ids.new_id("checkpoint-created")

    @staticmethod
    def _room_goal_target_id(room_id: str) -> str:
        return f"room-goal:{room_id}"

    @staticmethod
    def _continuity_note_sort_key(
        target: TargetState,
    ) -> tuple[int, int, str]:
        sequence = RoomsService._continuity_note_sequence(target)
        created_at = target.state.get("created_at")
        timestamp = (
            created_at
            if isinstance(created_at, int) and not isinstance(created_at, bool)
            else target.updated_at
        )
        note_id = target.state.get("note_id")
        return (
            sequence,
            timestamp,
            note_id if isinstance(note_id, str) else target.target_id,
        )

    @staticmethod
    def _continuity_note_sequence(target: TargetState) -> int:
        sequence = target.state.get("sequence")
        return (
            sequence
            if isinstance(sequence, int) and not isinstance(sequence, bool)
            else 0
        )

    def _require_room(self, room_id: str) -> TargetState:
        room = self._broker.get_target(room_id)
        if room is None:
            raise RecordNotFoundError("room", room_id)
        if room.state.get("kind") != "room":
            raise InvalidMutationError("target is not a room")
        return room

    def _record_reaction(
        self,
        *,
        message_id: str,
        room_id: str,
        actor_instance_id: str,
        actor_profile_id: str,
        reaction_type: str,
        action: str,
    ) -> MutationSubmission:
        message = self._broker.get_target(f"message:{message_id}")
        if message is None:
            raise RecordNotFoundError("message", message_id)
        if (
            message.state.get("kind") != "message"
            or message.state.get("room_id") != room_id
        ):
            raise InvalidMutationError("message is not in the requested room")

        timestamp = self._clock.now()
        actor = {
            "instance_id": actor_instance_id,
            "profile_id": actor_profile_id,
        }
        actor_key = self._actor_key(actor_instance_id, actor_profile_id)
        event_id = self._ids.new_id("reaction-event")
        event_state: dict[str, JsonValue] = {
            "kind": "reaction-event",
            "scope": room_id,
            "schema_version": 1,
            "event_id": event_id,
            "message_id": message_id,
            "room_id": room_id,
            "actor": actor,
            "actor_key": actor_key,
            "reaction_type": reaction_type,
            "action": action,
            "created_at": timestamp,
        }
        self._submit(
            f"reaction-event:{event_id}",
            0,
            actor_instance_id,
            "reaction.event.append",
            event_state,
        )

        state_target_id = self._reaction_state_target_id(
            message_id,
            actor_instance_id,
            actor_profile_id,
            reaction_type,
        )
        current = self._broker.get_target(state_target_id)
        projection_state: dict[str, JsonValue] = {
            "kind": "reaction-state",
            "scope": room_id,
            "schema_version": 1,
            "message_id": message_id,
            "room_id": room_id,
            "actor": actor,
            "actor_key": actor_key,
            "reaction_type": reaction_type,
            "status": "ACTIVE" if action == "ADD" else "REMOVED",
            "latest_event_id": event_id,
            "latest_action": action,
            "latest_event_at": timestamp,
        }
        return self._submit(
            state_target_id,
            0 if current is None else current.revision,
            actor_instance_id,
            "reaction.state.project",
            projection_state,
        )

    @staticmethod
    def _actor_key(actor_instance_id: str, actor_profile_id: str) -> str:
        return f"{actor_instance_id}:{actor_profile_id}"

    @classmethod
    def _reaction_state_target_id(
        cls,
        message_id: str,
        actor_instance_id: str,
        actor_profile_id: str,
        reaction_type: str,
    ) -> str:
        return (
            f"reaction-state:{message_id}:{cls._actor_key(actor_instance_id, actor_profile_id)}"
            f":{reaction_type}"
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
