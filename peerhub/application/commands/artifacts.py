"""Artifact record commands."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, ClassVar

from peerhub.application.commands import Command, SubmissionMetadata
from peerhub.core.protocol import JsonValue


@dataclass(frozen=True, slots=True)
class ArtifactClaimCommand(Command[Any]):
    method: ClassVar[str] = "governance.artifact.claim"
    submission: SubmissionMetadata
    name: str
    owner: str

    def encode_params(self) -> Mapping[str, JsonValue]:
        return {"name": self.name, "owner": self.owner}

    @classmethod
    def decode_result(cls, value: Mapping[str, JsonValue]) -> Any:
        return value


@dataclass(frozen=True, slots=True)
class ArtifactStatusCommand(Command[Any]):
    method: ClassVar[str] = "governance.artifact.status"
    submission: SubmissionMetadata
    name: str | None = None
    peer: str | None = None
    draft_path: str | None = None

    def encode_params(self) -> Mapping[str, JsonValue]:
        return {
            "name": self.name,
            "peer": self.peer,
            "draft_path": self.draft_path,
        }

    @classmethod
    def decode_result(cls, value: Mapping[str, JsonValue]) -> Any:
        return value


@dataclass(frozen=True, slots=True)
class ArtifactFinalizeCommand(Command[Any]):
    method: ClassVar[str] = "governance.artifact.finalize"
    submission: SubmissionMetadata
    name: str
    file_path: str

    def encode_params(self) -> Mapping[str, JsonValue]:
        return {"name": self.name, "file_path": self.file_path}

    @classmethod
    def decode_result(cls, value: Mapping[str, JsonValue]) -> Any:
        return value
