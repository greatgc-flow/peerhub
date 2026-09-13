"""Governance effect-status commands."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, ClassVar

from peerhub.application.commands import Command, SubmissionMetadata
from peerhub.core.protocol import JsonValue


@dataclass(frozen=True, slots=True)
class EffectStatusCommand(Command[Any]):
    method: ClassVar[str] = "governance.effect.status"
    submission: SubmissionMetadata
    limit: int = 20

    def encode_params(self) -> Mapping[str, JsonValue]:
        return {"limit": self.limit}

    @classmethod
    def decode_result(cls, value: Mapping[str, JsonValue]) -> Any:
        return value
