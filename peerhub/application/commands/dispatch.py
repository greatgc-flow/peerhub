"""Native dispatch commands.

These wire contracts are kept separate from the compatibility exports in
``application.legacy`` so new application code can depend on their domain.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, ClassVar

from peerhub.application.commands import Command, SubmissionMetadata
from peerhub.core.protocol import JsonValue


@dataclass(frozen=True, slots=True)
class SubmitDispatch(Command[Any]):
    method: ClassVar[str] = "dispatch.submit"
    submission: SubmissionMetadata
    prompt: str

    def encode_params(self) -> Mapping[str, JsonValue]:
        return {"prompt": self.prompt}

    @classmethod
    def decode_result(cls, value: Mapping[str, JsonValue]) -> Any:
        return value


@dataclass(frozen=True, slots=True)
class SubmitManyDispatch(Command[Any]):
    method: ClassVar[str] = "dispatch.submit_many"
    submission: SubmissionMetadata
    prompt: str

    def encode_params(self) -> Mapping[str, JsonValue]:
        return {"prompt": self.prompt}

    @classmethod
    def decode_result(cls, value: Mapping[str, JsonValue]) -> Any:
        return value


@dataclass(frozen=True, slots=True)
class SubmitCoordinatorDispatch(Command[Any]):
    method: ClassVar[str] = "dispatch.submit_coordinator"
    submission: SubmissionMetadata
    prompt: str

    def encode_params(self) -> Mapping[str, JsonValue]:
        return {"prompt": self.prompt}

    @classmethod
    def decode_result(cls, value: Mapping[str, JsonValue]) -> Any:
        return value
