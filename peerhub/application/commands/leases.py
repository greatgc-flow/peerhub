"""Process-lease status and sweep commands."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, ClassVar

from peerhub.application.commands import Command, SubmissionMetadata
from peerhub.core.protocol import JsonValue


@dataclass(frozen=True, slots=True)
class LeaseStatusCommand(Command[Any]):
    method: ClassVar[str] = "dispatch.lease.status"
    submission: SubmissionMetadata

    def encode_params(self) -> Mapping[str, JsonValue]:
        return {}

    @classmethod
    def decode_result(cls, value: Mapping[str, JsonValue]) -> Any:
        return value


@dataclass(frozen=True, slots=True)
class LeaseSweepCommand(Command[Any]):
    method: ClassVar[str] = "dispatch.lease.sweep"
    submission: SubmissionMetadata
    limit: int = 100
    reap: bool = True

    def encode_params(self) -> Mapping[str, JsonValue]:
        return {
            "limit": self.limit,
            "reap": self.reap,
        }

    @classmethod
    def decode_result(cls, value: Mapping[str, JsonValue]) -> Any:
        return value
