"""Room alert commands."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, ClassVar

from peerhub.application.commands import Command, SubmissionMetadata
from peerhub.core.protocol import JsonValue


@dataclass(frozen=True, slots=True)
class AlertRaiseCommand(Command[Any]):
    method: ClassVar[str] = "coordination.alert.raise"
    submission: SubmissionMetadata
    room_id: str
    raiser_instance_id: str
    raiser_profile_id: str
    severity: str
    message: str

    def encode_params(self) -> Mapping[str, JsonValue]:
        return {
            "room_id": self.room_id,
            "raiser_instance_id": self.raiser_instance_id,
            "raiser_profile_id": self.raiser_profile_id,
            "severity": self.severity,
            "message": self.message,
        }

    @classmethod
    def decode_result(cls, value: Mapping[str, JsonValue]) -> Any:
        return value
