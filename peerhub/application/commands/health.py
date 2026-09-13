"""Peer health and admission commands."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, ClassVar

from peerhub.application.commands import Command, SubmissionMetadata
from peerhub.core.protocol import JsonValue


@dataclass(frozen=True, slots=True)
class HealthCheckCommand(Command[Any]):
    method: ClassVar[str] = "health.check"
    submission: SubmissionMetadata
    peer: str | None = None
    recover: bool = False

    def encode_params(self) -> Mapping[str, JsonValue]:
        return {
            "peer": self.peer,
            "recover": self.recover,
        }

    @classmethod
    def decode_result(cls, value: Mapping[str, JsonValue]) -> Any:
        return value


@dataclass(frozen=True, slots=True)
class PeerQuarantineCommand(Command[Any]):
    method: ClassVar[str] = "health.admission.quarantine"
    submission: SubmissionMetadata
    peer_id: str
    reason: str = "manual"
    actor_id: str | None = None

    def encode_params(self) -> Mapping[str, JsonValue]:
        params: dict[str, JsonValue] = {
            "peer_id": self.peer_id,
            "reason": self.reason,
        }
        if self.actor_id is not None:
            params["actor_id"] = self.actor_id
        return params

    @classmethod
    def decode_result(cls, value: Mapping[str, JsonValue]) -> Any:
        return value


@dataclass(frozen=True, slots=True)
class PeerRecoverCommand(Command[Any]):
    method: ClassVar[str] = "health.peer.recover"
    submission: SubmissionMetadata
    peer_id: str
    reason: str = "manual"

    def encode_params(self) -> Mapping[str, JsonValue]:
        return {
            "peer_id": self.peer_id,
            "reason": self.reason,
        }

    @classmethod
    def decode_result(cls, value: Mapping[str, JsonValue]) -> Any:
        return value


@dataclass(frozen=True, slots=True)
class HealthPrecheckCommand(Command[Any]):
    method: ClassVar[str] = "health.precheck"
    submission: SubmissionMetadata
    peers: str | None = None
    needs: str | None = None

    def encode_params(self) -> Mapping[str, JsonValue]:
        return {
            "peers": self.peers,
            "needs": self.needs,
        }

    @classmethod
    def decode_result(cls, value: Mapping[str, JsonValue]) -> Any:
        return value


@dataclass(frozen=True, slots=True)
class CheckGateCommand(Command[Any]):
    method: ClassVar[str] = "health.gate.check"
    submission: SubmissionMetadata
    agent: str

    def encode_params(self) -> Mapping[str, JsonValue]:
        return {
            "agent": self.agent,
        }

    @classmethod
    def decode_result(cls, value: Mapping[str, JsonValue]) -> Any:
        return value


@dataclass(frozen=True, slots=True)
class HealthSweepCommand(Command[Any]):
    method: ClassVar[str] = "health.sweep"
    submission: SubmissionMetadata

    def encode_params(self) -> Mapping[str, JsonValue]:
        return {}

    @classmethod
    def decode_result(cls, value: Mapping[str, JsonValue]) -> Any:
        return value
