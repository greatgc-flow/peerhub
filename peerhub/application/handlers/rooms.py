"""Room command registration handlers."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, cast

from peerhub.application.commands.rooms import (
    AppendHandoffCommand,
    ClearRoomCommand,
    ContextFillCommand,
    ContinuityCheckpointCommand,
    MessageCheckCommand,
    MessageMarkReadCommand,
    MessageSendCommand,
    NewTopicCommand,
    RoomBroadcastCommand,
    StatusReadCommand,
    ThreadAppendCommand,
    ThreadNewCommand,
    ThreadPromoteCommand,
    ThreadReactCommand,
    UpdateStatusCommand,
)
from peerhub.application.room_broadcast import (
    RoomBroadcastCoordinator,
    RoomBroadcastResult,
)
from peerhub.application.status import collect_room_status
from peerhub.application.thread_new import ThreadNewResult, create_thread_new
from peerhub.core.protocol import CommandEnvelope, JsonValue
from peerhub.dispatch.room_session import RoomParticipationCoordinator
from peerhub.governance.rooms import RoomsService


def register_room_handlers(
    *,
    api: Any,
    service: RoomsService,
    room_session: RoomParticipationCoordinator | None = None,
) -> None:
    """Register the existing room-management wire handlers unchanged."""

    from peerhub.application.api import (
        CommandAvailability,
        CommandDescriptor,
        IdempotencyPolicy,
        Mutability,
        ScopeKind,
    )

    register = api.register
    submission = api._submission
    receipt = api._receipt
    descriptor = CommandDescriptor
    mutating = Mutability.MUTATING
    read_only = Mutability.READ_ONLY
    any_scope = ScopeKind.ANY
    domain_atomic_required = IdempotencyPolicy.DOMAIN_ATOMIC_REQUIRED
    idempotency_read_only = IdempotencyPolicy.READ_ONLY
    available = CommandAvailability.AVAILABLE

    def text(e: CommandEnvelope, n: str) -> str:
        v = e.params[n]
        if not isinstance(v, str):
            raise ValueError(f"{n} must be a string")
        return v

    def topic(e: CommandEnvelope) -> NewTopicCommand:
        return NewTopicCommand(
            submission(e),
            text(e, "thread_id"),
            text(e, "room_id"),
            text(e, "subject"),
            text(e, "creator_id"),
        )

    def thread_new(e: CommandEnvelope) -> ThreadNewCommand:
        return ThreadNewCommand(
            submission(e),
            text(e, "thread_id"),
            text(e, "room_id"),
            text(e, "subject"),
            text(e, "creator_id"),
        )

    def status(e: CommandEnvelope) -> StatusReadCommand:
        room_id = text(e, "room_id")
        if not room_id:
            raise ValueError("room_id must be a nonempty string")
        return StatusReadCommand(submission(e), room_id)

    def update_status(e: CommandEnvelope) -> UpdateStatusCommand:
        room_id = text(e, "room_id")
        if not room_id:
            raise ValueError("room_id must be a nonempty string")

        def optional_update_field(name: str) -> str | None:
            if name not in e.params:
                return None
            value = e.params[name]
            if not isinstance(value, str):
                raise ValueError(f"{name} must be a string")
            return value

        return UpdateStatusCommand(
            submission(e),
            room_id,
            optional_update_field("mission"),
            optional_update_field("blocked"),
            optional_update_field("phase"),
        )

    def append(e: CommandEnvelope) -> ThreadAppendCommand:
        return ThreadAppendCommand(
            submission(e),
            text(e, "message_id"),
            text(e, "room_id"),
            text(e, "thread_id"),
            text(e, "author_id"),
            text(e, "body"),
        )

    def optional_text(e: CommandEnvelope, n: str) -> str | None:
        value = e.params[n]
        if value is not None and not isinstance(value, str):
            raise ValueError(f"{n} must be a string or null")
        return value

    def boolean(e: CommandEnvelope, n: str) -> bool:
        value = e.params[n]
        if not isinstance(value, bool):
            raise ValueError(f"{n} must be a boolean")
        return value

    def integer(e: CommandEnvelope, n: str) -> int:
        value = e.params[n]
        if not isinstance(value, int) or isinstance(value, bool):
            raise ValueError(f"{n} must be an integer")
        return value

    def send(e: CommandEnvelope) -> MessageSendCommand:
        return MessageSendCommand(
            submission(e),
            text(e, "room_id"),
            text(e, "sender_instance_id"),
            text(e, "sender_profile_id"),
            text(e, "recipient_instance_id"),
            text(e, "recipient_profile_id"),
            text(e, "body"),
            text(e, "message_type"),
            optional_text(e, "thread_ref"),
            optional_text(e, "resource_ref"),
            optional_text(e, "correlation_id"),
        )

    def broadcast(e: CommandEnvelope) -> RoomBroadcastCommand:
        raw_targets = e.params["targets"]
        if raw_targets is None:
            targets = None
        elif isinstance(raw_targets, (list, tuple)) and all(
            isinstance(target, str) for target in raw_targets
        ):
            targets = tuple(cast(str, target) for target in raw_targets)
        else:
            raise ValueError("targets must be a sequence of strings or null")
        return RoomBroadcastCommand(
            submission(e),
            text(e, "room_id"),
            text(e, "from_"),
            text(e, "msg"),
            targets,
            text(e, "msg_type"),
            optional_text(e, "priority"),
        )

    coordinator = RoomBroadcastCoordinator(rooms=service)

    def encode_broadcast(
        result: RoomBroadcastResult,
    ) -> Mapping[str, JsonValue]:
        return {"room_id": result.room_id, "delivered": result.delivered}

    def check_inbox(e: CommandEnvelope) -> MessageCheckCommand:
        return MessageCheckCommand(
            submission(e),
            text(e, "room_id"),
            text(e, "caller_instance_id"),
            text(e, "caller_profile_id"),
            boolean(e, "include_read"),
        )

    def mark_read(e: CommandEnvelope) -> MessageMarkReadCommand:
        return MessageMarkReadCommand(
            submission(e),
            text(e, "room_id"),
            text(e, "recipient_instance_id"),
            text(e, "recipient_profile_id"),
            integer(e, "up_through_sequence"),
        )

    def promote(e: CommandEnvelope) -> ThreadPromoteCommand:
        return ThreadPromoteCommand(
            submission(e),
            text(e, "message_id"),
            text(e, "room_id"),
            text(e, "thread_id"),
            text(e, "actor_id"),
        )

    def encode_inbox_messages(
        results: Sequence[Any],
    ) -> Mapping[str, JsonValue]:
        messages = [
            {
                "target_id": result.target_id,
                "revision": result.revision,
                "state": result.state,
            }
            for result in results
        ]
        return {"messages": cast(JsonValue, messages)}

    def append_handoff(e: CommandEnvelope) -> AppendHandoffCommand:
        section = text(e, "section")
        if section not in {
            "RECENT_COMPLETED",
            "PENDING_ISSUES",
            "KEY_DECISIONS",
            "CONSENSUS_HISTORY",
            "ACTIVE_THREADS",
        }:
            raise ValueError(
                "section must be a supported append-only handoff section"
            )
        return AppendHandoffCommand(
            submission(e),
            text(e, "room_id"),
            section,
            text(e, "text"),
            text(e, "actor_id"),
        )

    def checkpoint(e: CommandEnvelope) -> ContinuityCheckpointCommand:
        return ContinuityCheckpointCommand(
            submission(e),
            text(e, "room_id"),
            text(e, "actor_id"),
        )

    def context_fill(e: CommandEnvelope) -> ContextFillCommand:
        session_id = text(e, "session_id")
        if not session_id:
            raise ValueError("session_id must be a nonempty string")
        raw_sections = e.params["sections"]
        if raw_sections is None:
            sections: tuple[str, ...] | None = None
        elif isinstance(raw_sections, (list, tuple)) and all(
            isinstance(section, str) for section in raw_sections
        ):
            sections = tuple(cast(str, section) for section in raw_sections)
        else:
            raise ValueError("sections must be a sequence of strings or null")
        if sections is not None:
            if not sections:
                raise ValueError("sections must not be empty")
            valid_sections = {
                "GOAL",
                "RECENT_COMPLETED",
                "PENDING_ISSUES",
                "KEY_DECISIONS",
                "CONSENSUS_HISTORY",
                "ACTIVE_THREADS",
            }
            if any(section not in valid_sections for section in sections):
                raise ValueError("sections contains an unknown section name")
            if len(set(sections)) != len(sections):
                raise ValueError("sections must not contain duplicates")
        return ContextFillCommand(
            submission(e),
            text(e, "room_id"),
            session_id,
            sections,
        )

    def encode_checkpoint(
        result: Mapping[str, JsonValue],
    ) -> Mapping[str, JsonValue]:
        return result

    def react(e: CommandEnvelope) -> ThreadReactCommand:
        action = text(e, "action")
        if action not in {"ADD", "REMOVE"}:
            raise ValueError("action must be ADD or REMOVE")
        return ThreadReactCommand(
            submission(e),
            text(e, "message_id"),
            text(e, "room_id"),
            text(e, "actor_instance_id"),
            text(e, "actor_profile_id"),
            text(e, "reaction_type"),
            action,
        )

    def record_reaction(command: ThreadReactCommand):
        operation = (
            service.react if command.action == "ADD" else service.unreact
        )
        return operation(
            message_id=command.message_id,
            room_id=command.room_id,
            actor_instance_id=command.actor_instance_id,
            actor_profile_id=command.actor_profile_id,
            reaction_type=command.reaction_type,
        )

    def clear(e: CommandEnvelope) -> ClearRoomCommand:
        return ClearRoomCommand(
            submission(e),
            text(e, "old_room_id"),
            text(e, "new_room_id"),
            text(e, "subject"),
            text(e, "actor_id"),
        )

    def encode_thread_new(result: ThreadNewResult) -> Mapping[str, JsonValue]:
        rcpt = (
            None
            if result.submission is None
            else dict(receipt(result.submission))
        )
        return {
            "thread_id": result.thread_id,
            "created": result.created,
            "message": result.message,
            "receipt": cast(JsonValue, rcpt),
        }

    register(descriptor(
        "peerhub.status.read",
        read_only,
        any_scope,
        idempotency_read_only,
        status,
        lambda c, _: collect_room_status(service, room_id=c.room_id, room_sessions=room_session),
        lambda result: result,
        available,
    ))

    def update_room_summary(command: UpdateStatusCommand):
        fields: dict[str, str] = {}
        if command.mission is not None:
            fields["mission"] = command.mission
        if command.blocked is not None:
            fields["blocked"] = command.blocked
        if command.phase is not None:
            fields["phase"] = command.phase
        return service.update_room_summary(
            command.room_id,
            actor_id=(
                command.submission.actor_id
                or command.submission.client_id
            ),
            **fields,
        )

    register(descriptor(
        "coordination.mission.update",
        mutating,
        any_scope,
        domain_atomic_required,
        update_status,
        lambda c, _: update_room_summary(c),
        receipt,
        available,
    ))

    register(descriptor(
        "coordination.topic.create",
        mutating,
        any_scope,
        domain_atomic_required,
        topic,
        lambda c, _: service.create_thread(
            thread_id=c.thread_id,
            room_id=c.room_id,
            subject=c.subject,
            creator_id=c.creator_id,
        ),
        receipt,
        available,
    ))

    register(descriptor(
        "coordination.thread.create",
        mutating,
        any_scope,
        domain_atomic_required,
        thread_new,
        lambda c, _: create_thread_new(
            service,
            thread_id=c.thread_id,
            room_id=c.room_id,
            subject=c.subject,
            creator_id=c.creator_id,
        ),
        encode_thread_new,
        available,
    ))

    register(descriptor(
        "coordination.thread.append",
        mutating,
        any_scope,
        domain_atomic_required,
        append,
        lambda c, _: service.append_message(
            message_id=c.message_id,
            room_id=c.room_id,
            thread_id=c.thread_id,
            author_id=c.author_id,
            body=c.body,
        ),
        receipt,
        available,
    ))

    register(descriptor(
        "coordination.message.send",
        mutating,
        any_scope,
        domain_atomic_required,
        send,
        lambda c, _: service.send_message(
            room_id=c.room_id,
            sender_instance_id=c.sender_instance_id,
            sender_profile_id=c.sender_profile_id,
            recipient_instance_id=c.recipient_instance_id,
            recipient_profile_id=c.recipient_profile_id,
            body=c.body,
            message_type=c.message_type,
            thread_ref=c.thread_ref,
            resource_ref=c.resource_ref,
            correlation_id=c.correlation_id,
        ),
        receipt,
        available,
    ))

    register(descriptor(
        "coordination.message.broadcast",
        mutating,
        any_scope,
        domain_atomic_required,
        broadcast,
        lambda c, _: coordinator.broadcast(
            room_id=c.room_id,
            from_=c.from_,
            msg=c.msg,
            targets=c.targets,
            msg_type=c.msg_type,
            priority=c.priority,
        ),
        encode_broadcast,
        available,
    ))

    register(descriptor(
        "coordination.message.check",
        read_only,
        any_scope,
        idempotency_read_only,
        check_inbox,
        lambda c, _: service.check_inbox(
            room_id=c.room_id,
            caller_instance_id=c.caller_instance_id,
            caller_profile_id=c.caller_profile_id,
            include_read=c.include_read,
        ),
        encode_inbox_messages,
        available,
    ))

    register(descriptor(
        "coordination.message.mark_read",
        mutating,
        any_scope,
        domain_atomic_required,
        mark_read,
        lambda c, _: service.mark_read(
            room_id=c.room_id,
            recipient_instance_id=c.recipient_instance_id,
            recipient_profile_id=c.recipient_profile_id,
            up_through_sequence=c.up_through_sequence,
        ),
        receipt,
        available,
    ))

    register(descriptor(
        "coordination.thread.promote",
        mutating,
        any_scope,
        domain_atomic_required,
        promote,
        lambda c, _: service.promote_message(
            message_id=c.message_id,
            room_id=c.room_id,
            thread_id=c.thread_id,
            actor_id=c.actor_id,
        ),
        receipt,
        available,
    ))

    register(descriptor(
        "coordination.thread.react",
        mutating,
        any_scope,
        domain_atomic_required,
        react,
        lambda c, _: record_reaction(c),
        receipt,
        available,
    ))

    register(descriptor(
        "coordination.handoff.append",
        mutating,
        any_scope,
        domain_atomic_required,
        append_handoff,
        lambda c, _: service.append_handoff_note(
            room_id=c.room_id,
            section=c.section,
            text=c.text,
            actor_id=c.actor_id,
        ),
        receipt,
        available,
    ))

    register(descriptor(
        "coordination.checkpoint.create",
        mutating,
        any_scope,
        domain_atomic_required,
        checkpoint,
        lambda c, _: service.checkpoint(
            c.room_id,
            actor_id=c.actor_id,
            idempotency_key=c.submission.idempotency_key,
            idempotency_scope=c.submission.client_id,
        ),
        encode_checkpoint,
        available,
    ))

    register(descriptor(
        "coordination.context.fill",
        read_only,
        any_scope,
        idempotency_read_only,
        context_fill,
        lambda c, _: service.context_fill(
            c.room_id,
            session_id=c.session_id,
            sections=c.sections,
        ),
        encode_checkpoint,
        available,
    ))

    register(descriptor(
        "coordination.room.clear",
        mutating,
        any_scope,
        domain_atomic_required,
        clear,
        lambda c, _: service.clear_room(
            c.old_room_id,
            new_room_id=c.new_room_id,
            subject=c.subject,
            actor_id=c.actor_id,
        ),
        receipt,
        available,
    ))
