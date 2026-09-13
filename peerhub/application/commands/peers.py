"""Peer registry, profile, and model-status commands."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, ClassVar

from peerhub.application.commands import Command, SubmissionMetadata
from peerhub.core.protocol import JsonValue


@dataclass(frozen=True, slots=True)
class RegisterNodeCommand(Command[Any]):
    method: ClassVar[str] = "configuration.instance.register"
    submission: SubmissionMetadata
    node_id: str
    peer_kind: str
    profile_id: str | None
    tier: int
    node_type: str
    actor_id: str

    def encode_params(self) -> Mapping[str, JsonValue]:
        return {
            "node_id": self.node_id,
            "peer_kind": self.peer_kind,
            "profile_id": self.profile_id,
            "tier": self.tier,
            "node_type": self.node_type,
            "actor_id": self.actor_id,
        }

    @classmethod
    def decode_result(cls, value: Mapping[str, JsonValue]) -> Any:
        return value


@dataclass(frozen=True, slots=True)
class ListNodesCommand(Command[Any]):
    method: ClassVar[str] = "configuration.instance.list"
    submission: SubmissionMetadata

    def encode_params(self) -> Mapping[str, JsonValue]:
        return {}

    @classmethod
    def decode_result(cls, value: Mapping[str, JsonValue]) -> Any:
        return value


@dataclass(frozen=True, slots=True)
class BindProfileCommand(Command[Any]):
    method: ClassVar[str] = "configuration.profile.bind"
    submission: SubmissionMetadata
    node_id: str
    profile_id: str
    model_id: str
    reasoning_effort: str | None
    actor_id: str

    def encode_params(self) -> Mapping[str, JsonValue]:
        return {
            "node_id": self.node_id,
            "profile_id": self.profile_id,
            "model_id": self.model_id,
            "reasoning_effort": self.reasoning_effort,
            "actor_id": self.actor_id,
        }

    @classmethod
    def decode_result(cls, value: Mapping[str, JsonValue]) -> Any:
        return value


@dataclass(frozen=True, slots=True)
class ModelStatusCommand(Command[Any]):
    method: ClassVar[str] = "configuration.model.status"
    submission: SubmissionMetadata

    def encode_params(self) -> Mapping[str, JsonValue]:
        return {}

    @classmethod
    def decode_result(cls, value: Mapping[str, JsonValue]) -> Any:
        return value


@dataclass(frozen=True, slots=True)
class PeerStatusCommand(Command[Any]):
    method: ClassVar[str] = "configuration.peer.status"
    submission: SubmissionMetadata
    node_id: str | None = None
    include_all: bool = False

    def encode_params(self) -> Mapping[str, JsonValue]:
        return {
            "node_id": self.node_id,
            "include_all": self.include_all,
        }

    @classmethod
    def decode_result(cls, value: Mapping[str, JsonValue]) -> Any:
        return value
