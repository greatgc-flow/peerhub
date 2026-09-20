"""Room, thread, message, status, and continuity commands."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, ClassVar

from peerhub.application.commands import Command, SubmissionMetadata
from peerhub.core.protocol import JsonValue


@dataclass(frozen=True, slots=True)
class StatusReadCommand(Command[Any]):
    method: ClassVar[str] = "peerhub.status.read"
    submission: SubmissionMetadata
    room_id: str

    def encode_params(self) -> Mapping[str, JsonValue]:
        return {"room_id": self.room_id}

    @classmethod
    def decode_result(cls, value: Mapping[str, JsonValue]) -> Any:
        return value


@dataclass(frozen=True, slots=True)
class UpdateStatusCommand(Command[Any]):
    """Apply the legacy room-summary fields without clobbering omissions."""

    method: ClassVar[str] = "coordination.mission.update"
    submission: SubmissionMetadata
    room_id: str
    mission: str | None = None
    blocked: str | None = None
    phase: str | None = None

    def encode_params(self) -> Mapping[str, JsonValue]:
        params: dict[str, JsonValue] = {"room_id": self.room_id}
        if self.mission is not None:
            params["mission"] = self.mission
        if self.blocked is not None:
            params["blocked"] = self.blocked
        if self.phase is not None:
            params["phase"] = self.phase
        return params

    @classmethod
    def decode_result(cls, value: Mapping[str, JsonValue]) -> Any:
        return value


@dataclass(frozen=True, slots=True)
class NewTopicCommand(Command[Any]):
    method: ClassVar[str] = "coordination.topic.create"
    submission: SubmissionMetadata
    thread_id: str
    room_id: str
    subject: str
    creator_id: str

    def encode_params(self) -> Mapping[str, JsonValue]:
        return {"thread_id": self.thread_id, "room_id": self.room_id, "subject": self.subject, "creator_id": self.creator_id}

    @classmethod
    def decode_result(cls, value: Mapping[str, JsonValue]) -> Any:
        return value


@dataclass(frozen=True, slots=True)
class ThreadNewCommand(Command[Any]):
    method: ClassVar[str] = "coordination.thread.create"
    submission: SubmissionMetadata
    thread_id: str
    room_id: str
    subject: str
    creator_id: str

    def encode_params(self) -> Mapping[str, JsonValue]:
        return {
            "thread_id": self.thread_id,
            "room_id": self.room_id,
            "subject": self.subject,
            "creator_id": self.creator_id,
        }

    @classmethod
    def decode_result(cls, value: Mapping[str, JsonValue]) -> Any:
        return value


@dataclass(frozen=True, slots=True)
class ThreadAppendCommand(Command[Any]):
    method: ClassVar[str] = "coordination.thread.append"
    submission: SubmissionMetadata
    message_id: str
    room_id: str
    thread_id: str
    author_id: str
    body: str

    def encode_params(self) -> Mapping[str, JsonValue]:
        return {
            "message_id": self.message_id,
            "room_id": self.room_id,
            "thread_id": self.thread_id,
            "author_id": self.author_id,
            "body": self.body,
        }

    @classmethod
    def decode_result(cls, value: Mapping[str, JsonValue]) -> Any:
        return value


@dataclass(frozen=True, slots=True)
class MessageSendCommand(Command[Any]):
    method: ClassVar[str] = "coordination.message.send"
    submission: SubmissionMetadata
    room_id: str
    sender_instance_id: str
    sender_profile_id: str
    recipient_instance_id: str
    recipient_profile_id: str
    body: str
    message_type: str = "MSG"
    thread_ref: str | None = None
    resource_ref: str | None = None
    correlation_id: str | None = None

    def encode_params(self) -> Mapping[str, JsonValue]:
        return {
            "room_id": self.room_id,
            "sender_instance_id": self.sender_instance_id,
            "sender_profile_id": self.sender_profile_id,
            "recipient_instance_id": self.recipient_instance_id,
            "recipient_profile_id": self.recipient_profile_id,
            "body": self.body,
            "message_type": self.message_type,
            "thread_ref": self.thread_ref,
            "resource_ref": self.resource_ref,
            "correlation_id": self.correlation_id,
        }

    @classmethod
    def decode_result(cls, value: Mapping[str, JsonValue]) -> Any:
        return value


@dataclass(frozen=True, slots=True)
class RoomBroadcastCommand(Command[Any]):
    method: ClassVar[str] = "coordination.message.broadcast"
    submission: SubmissionMetadata
    room_id: str
    from_: str
    msg: str
    targets: tuple[str, ...] | None
    msg_type: str = "MSG"
    priority: str | None = None

    def encode_params(self) -> Mapping[str, JsonValue]:
        return {
            "room_id": self.room_id,
            "from_": self.from_,
            "msg": self.msg,
            "targets": self.targets,
            "msg_type": self.msg_type,
            "priority": self.priority,
        }

    @classmethod
    def decode_result(cls, value: Mapping[str, JsonValue]) -> Any:
        return value


@dataclass(frozen=True, slots=True)
class MessageCheckCommand(Command[Any]):
    method: ClassVar[str] = "coordination.message.check"
    submission: SubmissionMetadata
    room_id: str
    caller_instance_id: str
    caller_profile_id: str
    include_read: bool = False

    def encode_params(self) -> Mapping[str, JsonValue]:
        return {
            "room_id": self.room_id,
            "caller_instance_id": self.caller_instance_id,
            "caller_profile_id": self.caller_profile_id,
            "include_read": self.include_read,
        }

    @classmethod
    def decode_result(cls, value: Mapping[str, JsonValue]) -> Any:
        return value


@dataclass(frozen=True, slots=True)
class MessageMarkReadCommand(Command[Any]):
    method: ClassVar[str] = "coordination.message.mark_read"
    submission: SubmissionMetadata
    room_id: str
    recipient_instance_id: str
    recipient_profile_id: str
    up_through_sequence: int

    def encode_params(self) -> Mapping[str, JsonValue]:
        return {
            "room_id": self.room_id,
            "recipient_instance_id": self.recipient_instance_id,
            "recipient_profile_id": self.recipient_profile_id,
            "up_through_sequence": self.up_through_sequence,
        }

    @classmethod
    def decode_result(cls, value: Mapping[str, JsonValue]) -> Any:
        return value


@dataclass(frozen=True, slots=True)
class ThreadPromoteCommand(Command[Any]):
    method: ClassVar[str] = "coordination.thread.promote"
    submission: SubmissionMetadata
    message_id: str
    room_id: str
    thread_id: str
    actor_id: str

    def encode_params(self) -> Mapping[str, JsonValue]:
        return {
            "message_id": self.message_id,
            "room_id": self.room_id,
            "thread_id": self.thread_id,
            "actor_id": self.actor_id,
        }

    @classmethod
    def decode_result(cls, value: Mapping[str, JsonValue]) -> Any:
        return value


@dataclass(frozen=True, slots=True)
class AppendHandoffCommand(Command[Any]):
    method: ClassVar[str] = "coordination.handoff.append"
    submission: SubmissionMetadata
    room_id: str
    section: str
    text: str
    actor_id: str

    def encode_params(self) -> Mapping[str, JsonValue]:
        return {
            "room_id": self.room_id,
            "section": self.section,
            "text": self.text,
            "actor_id": self.actor_id,
        }

    @classmethod
    def decode_result(cls, value: Mapping[str, JsonValue]) -> Any:
        return value


@dataclass(frozen=True, slots=True)
class ContinuityCheckpointCommand(Command[Any]):
    method: ClassVar[str] = "coordination.checkpoint.create"
    submission: SubmissionMetadata
    room_id: str
    actor_id: str

    def encode_params(self) -> Mapping[str, JsonValue]:
        return {"room_id": self.room_id, "actor_id": self.actor_id}

    @classmethod
    def decode_result(cls, value: Mapping[str, JsonValue]) -> Any:
        return value


@dataclass(frozen=True, slots=True)
class ContextFillCommand(Command[Any]):
    method: ClassVar[str] = "coordination.context.fill"
    submission: SubmissionMetadata
    room_id: str
    session_id: str
    sections: tuple[str, ...] | None

    def encode_params(self) -> Mapping[str, JsonValue]:
        return {
            "room_id": self.room_id,
            "session_id": self.session_id,
            "sections": self.sections,
        }

    @classmethod
    def decode_result(cls, value: Mapping[str, JsonValue]) -> Any:
        return value


@dataclass(frozen=True, slots=True)
class ThreadReactCommand(Command[Any]):
    method: ClassVar[str] = "coordination.thread.react"
    submission: SubmissionMetadata
    message_id: str
    room_id: str
    actor_instance_id: str
    actor_profile_id: str
    reaction_type: str
    action: str = "ADD"

    def encode_params(self) -> Mapping[str, JsonValue]:
        return {
            "message_id": self.message_id,
            "room_id": self.room_id,
            "actor_instance_id": self.actor_instance_id,
            "actor_profile_id": self.actor_profile_id,
            "reaction_type": self.reaction_type,
            "action": self.action,
        }

    @classmethod
    def decode_result(cls, value: Mapping[str, JsonValue]) -> Any:
        return value


@dataclass(frozen=True, slots=True)
class ClearRoomCommand(Command[Any]):
    method: ClassVar[str] = "coordination.room.clear"
    submission: SubmissionMetadata
    old_room_id: str
    new_room_id: str
    subject: str
    actor_id: str

    def encode_params(self) -> Mapping[str, JsonValue]:
        return {"old_room_id": self.old_room_id, "new_room_id": self.new_room_id, "subject": self.subject, "actor_id": self.actor_id}

    @classmethod
    def decode_result(cls, value: Mapping[str, JsonValue]) -> Any:
        return value


@dataclass(frozen=True, slots=True)
class CreateRoomCommand(Command[Any]):
    method: ClassVar[str] = "coordination.room.create"
    submission: SubmissionMetadata
    room_id: str
    topic_id: str
    title: str
    creator_id: str
    participants: tuple[str, ...]

    def encode_params(self) -> Mapping[str, JsonValue]:
        return {
            "room_id": self.room_id,
            "topic_id": self.topic_id,
            "title": self.title,
            "creator_id": self.creator_id,
            "participants": self.participants,
        }

    @classmethod
    def decode_result(cls, value: Mapping[str, JsonValue]) -> Any:
        return value


@dataclass(frozen=True, slots=True)
class RebuildRoomSessionBindingsCommand(Command[Any]):
    method: ClassVar[str] = "coordination.room.rebuild_session_bindings"
    submission: SubmissionMetadata
    room_id: str

    def encode_params(self) -> Mapping[str, JsonValue]:
        return {"room_id": self.room_id}

    @classmethod
    def decode_result(cls, value: Mapping[str, JsonValue]) -> Any:
        return value


@dataclass(frozen=True, slots=True)
class RoomStatusCommand(Command[Any]):
    method: ClassVar[str] = "peerhub.status.read"
    submission: SubmissionMetadata
    room_id: str

    def encode_params(self) -> Mapping[str, JsonValue]:
        return {"room_id": self.room_id}

    @classmethod
    def decode_result(cls, value: Mapping[str, JsonValue]) -> Any:
        return value
