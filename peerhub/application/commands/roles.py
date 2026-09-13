"""Role assignment commands."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, ClassVar

from peerhub.application.commands import Command, SubmissionMetadata
from peerhub.core.protocol import JsonValue


@dataclass(frozen=True, slots=True)
class AssignRoleCommand(Command[Any]):
    method: ClassVar[str] = "coordination.role.assign"
    submission: SubmissionMetadata
    role: str
    peer_node_id: str
    actor_id: str

    def encode_params(self) -> Mapping[str, JsonValue]:
        return {
            "role": self.role,
            "peer_node_id": self.peer_node_id,
            "actor_id": self.actor_id,
        }

    @classmethod
    def decode_result(cls, value: Mapping[str, JsonValue]) -> Any:
        return value


@dataclass(frozen=True, slots=True)
class ReleaseRoleCommand(Command[Any]):
    method: ClassVar[str] = "coordination.role.release"
    submission: SubmissionMetadata
    role: str
    actor_id: str
    peer_node_id: str | None = None

    def encode_params(self) -> Mapping[str, JsonValue]:
        return {
            "role": self.role,
            "actor_id": self.actor_id,
            "peer_node_id": self.peer_node_id,
        }

    @classmethod
    def decode_result(cls, value: Mapping[str, JsonValue]) -> Any:
        return value


@dataclass(frozen=True, slots=True)
class RoleStatusCommand(Command[Any]):
    method: ClassVar[str] = "coordination.role.status"
    submission: SubmissionMetadata

    def encode_params(self) -> Mapping[str, JsonValue]:
        return {}

    @classmethod
    def decode_result(cls, value: Mapping[str, JsonValue]) -> Any:
        return value
