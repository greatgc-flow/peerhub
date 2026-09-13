"""Governance feedback commands."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, ClassVar

from peerhub.application.commands import Command, SubmissionMetadata
from peerhub.core.protocol import JsonValue


@dataclass(frozen=True, slots=True)
class FeedbackAddCommand(Command[Any]):
    method: ClassVar[str] = "governance.feedback.create"
    submission: SubmissionMetadata
    source_peer: str
    category: str
    severity: str
    title: str
    detail: str
    actor_id: str

    def encode_params(self) -> Mapping[str, JsonValue]:
        return {
            "source_peer": self.source_peer,
            "category": self.category,
            "severity": self.severity,
            "title": self.title,
            "detail": self.detail,
            "actor_id": self.actor_id,
        }

    @classmethod
    def decode_result(cls, value: Mapping[str, JsonValue]) -> Any:
        return value


@dataclass(frozen=True, slots=True)
class FeedbackListCommand(Command[Any]):
    method: ClassVar[str] = "governance.feedback.list"
    submission: SubmissionMetadata

    def encode_params(self) -> Mapping[str, JsonValue]:
        return {}

    @classmethod
    def decode_result(cls, value: Mapping[str, JsonValue]) -> Any:
        return value


@dataclass(frozen=True, slots=True)
class FeedbackResolveCommand(Command[Any]):
    method: ClassVar[str] = "governance.feedback.resolve"
    submission: SubmissionMetadata
    feedback_id: str
    status: str
    actor_id: str
    owner: str | None = None

    def encode_params(self) -> Mapping[str, JsonValue]:
        return {
            "feedback_id": self.feedback_id,
            "status": self.status,
            "actor_id": self.actor_id,
            "owner": self.owner,
        }

    @classmethod
    def decode_result(cls, value: Mapping[str, JsonValue]) -> Any:
        return value
