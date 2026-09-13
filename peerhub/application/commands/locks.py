"""File-lock commands."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, ClassVar

from peerhub.application.commands import Command, SubmissionMetadata
from peerhub.core.protocol import JsonValue


@dataclass(frozen=True, slots=True)
class LockAcquireCommand(Command[Any]):
    method: ClassVar[str] = "governance.lock.acquire"
    submission: SubmissionMetadata
    name: str
    owner: str
    lock_scope: str

    def encode_params(self) -> Mapping[str, JsonValue]:
        return {
            "name": self.name,
            "owner": self.owner,
            "lock_scope": self.lock_scope,
        }

    @classmethod
    def decode_result(cls, value: Mapping[str, JsonValue]) -> Any:
        return value


@dataclass(frozen=True, slots=True)
class LockReleaseCommand(Command[Any]):
    method: ClassVar[str] = "governance.lock.release"
    submission: SubmissionMetadata
    name: str
    owner: str | None

    def encode_params(self) -> Mapping[str, JsonValue]:
        return {
            "name": self.name,
            "owner": self.owner,
        }

    @classmethod
    def decode_result(cls, value: Mapping[str, JsonValue]) -> Any:
        return value


@dataclass(frozen=True, slots=True)
class LockStatusCommand(Command[Any]):
    method: ClassVar[str] = "governance.lock.status"
    submission: SubmissionMetadata

    def encode_params(self) -> Mapping[str, JsonValue]:
        return {}

    @classmethod
    def decode_result(cls, value: Mapping[str, JsonValue]) -> Any:
        return value
