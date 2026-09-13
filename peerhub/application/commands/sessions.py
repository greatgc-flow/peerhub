"""Session lifecycle commands."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, ClassVar

from peerhub.application.commands import Command, SubmissionMetadata
from peerhub.core.protocol import JsonValue


@dataclass(frozen=True, slots=True)
class SessionOpenCommand(Command[Any]):
    method: ClassVar[str] = "coordination.session.open"
    submission: SubmissionMetadata
    workspace_scope_id: str
    room_id: str
    actor_principal_id: str
    instance_id: str
    profile_id: str
    session_fingerprint: str
    heartbeat_timeout_ms: int

    def encode_params(self) -> Mapping[str, JsonValue]:
        return {
            "workspace_scope_id": self.workspace_scope_id,
            "room_id": self.room_id,
            "actor_principal_id": self.actor_principal_id,
            "instance_id": self.instance_id,
            "profile_id": self.profile_id,
            "session_fingerprint": self.session_fingerprint,
            "heartbeat_timeout_ms": self.heartbeat_timeout_ms,
        }

    @classmethod
    def decode_result(cls, value: Mapping[str, JsonValue]) -> Any:
        return value


@dataclass(frozen=True, slots=True)
class SessionCloseCommand(Command[Any]):
    method: ClassVar[str] = "coordination.session.close"
    submission: SubmissionMetadata
    session_id: str
    session_generation: int
    workspace_scope_id: str
    room_id: str
    actor_principal_id: str
    instance_id: str
    profile_id: str

    def encode_params(self) -> Mapping[str, JsonValue]:
        return {
            "session_id": self.session_id,
            "session_generation": self.session_generation,
            "workspace_scope_id": self.workspace_scope_id,
            "room_id": self.room_id,
            "actor_principal_id": self.actor_principal_id,
            "instance_id": self.instance_id,
            "profile_id": self.profile_id,
        }

    @classmethod
    def decode_result(cls, value: Mapping[str, JsonValue]) -> Any:
        return value


@dataclass(frozen=True, slots=True)
class SessionHeartbeatCommand(SessionCloseCommand):
    method: ClassVar[str] = "coordination.session.heartbeat"
    heartbeat_timeout_ms: int

    def encode_params(self) -> Mapping[str, JsonValue]:
        return {
            **SessionCloseCommand.encode_params(self),
            "heartbeat_timeout_ms": self.heartbeat_timeout_ms,
        }
