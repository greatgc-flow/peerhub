"""Operational-error reporting commands."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, ClassVar

from peerhub.application.commands import Command, SubmissionMetadata
from peerhub.core.protocol import JsonValue


@dataclass(frozen=True, slots=True)
class ReportErrorCommand(Command[Any]):
    method: ClassVar[str] = "telemetry.error.record"
    submission: SubmissionMetadata
    peer_key: str
    pattern: str
    severity: str
    detail: str
    actor_id: str
    threshold: int = 3

    def encode_params(self) -> Mapping[str, JsonValue]:
        return {
            "peer_key": self.peer_key,
            "pattern": self.pattern,
            "severity": self.severity,
            "detail": self.detail,
            "actor_id": self.actor_id,
            "threshold": self.threshold,
        }

    @classmethod
    def decode_result(cls, value: Mapping[str, JsonValue]) -> Any:
        return value
