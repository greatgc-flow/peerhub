"""Lesson lifecycle and distribution commands."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, ClassVar

from peerhub.application.commands import Command, SubmissionMetadata
from peerhub.core.protocol import JsonValue


@dataclass(frozen=True, slots=True)
class LessonInjectCommand(Command[Any]):
    method: ClassVar[str] = "governance.lesson.inject"
    submission: SubmissionMetadata
    target_peer_id: str
    workspace_id: str
    os: str | None
    shell: str | None
    task_types: frozenset[str]

    def encode_params(self) -> Mapping[str, JsonValue]:
        return {
            "target_peer_id": self.target_peer_id,
            "workspace_id": self.workspace_id,
            "os": self.os,
            "shell": self.shell,
            "task_types": tuple(self.task_types),
        }

    @classmethod
    def decode_result(cls, value: Mapping[str, JsonValue]) -> Any:
        return value


@dataclass(frozen=True, slots=True)
class LessonProposeCommand(Command[Any]):
    method: ClassVar[str] = "governance.lesson.propose"
    submission: SubmissionMetadata
    lesson_id: str
    title: str
    rule: str
    category: str
    severity: str
    proposer_id: str
    affected_peers: tuple[str, ...]
    scope_kind: str
    workspace_id: str | None
    sticky: bool
    os: tuple[str, ...] | None
    shell: tuple[str, ...] | None
    task_types: tuple[str, ...] | None

    def encode_params(self) -> Mapping[str, JsonValue]:
        return {"lesson_id": self.lesson_id, "title": self.title, "rule": self.rule, "category": self.category, "severity": self.severity, "proposer_id": self.proposer_id, "affected_peers": self.affected_peers, "scope_kind": self.scope_kind, "workspace_id": self.workspace_id, "sticky": self.sticky, "os": self.os, "shell": self.shell, "task_types": self.task_types}

    @classmethod
    def decode_result(cls, value: Mapping[str, JsonValue]) -> Any:
        return value


@dataclass(frozen=True, slots=True)
class LessonActivateCommand(Command[Any]):
    method: ClassVar[str] = "governance.lesson.activate"
    submission: SubmissionMetadata
    lesson_id: str
    actor_id: str
    expected_revision: int | None

    def encode_params(self) -> Mapping[str, JsonValue]:
        return {"lesson_id": self.lesson_id, "actor_id": self.actor_id, "expected_revision": self.expected_revision}

    @classmethod
    def decode_result(cls, value: Mapping[str, JsonValue]) -> Any:
        return value


@dataclass(frozen=True, slots=True)
class LessonRetireCommand(Command[Any]):
    method: ClassVar[str] = "governance.lesson.retire"
    submission: SubmissionMetadata
    lesson_id: str
    actor_id: str
    reason: str
    expected_revision: int | None

    def encode_params(self) -> Mapping[str, JsonValue]:
        return {"lesson_id": self.lesson_id, "actor_id": self.actor_id, "reason": self.reason, "expected_revision": self.expected_revision}

    @classmethod
    def decode_result(cls, value: Mapping[str, JsonValue]) -> Any:
        return value


@dataclass(frozen=True, slots=True)
class LessonBroadcastCommand(Command[Any]):
    method: ClassVar[str] = "coordination.lesson.broadcast"
    submission: SubmissionMetadata
    lesson_id: str
    room_id: str
    sender_instance_id: str
    sender_profile_id: str

    def encode_params(self) -> Mapping[str, JsonValue]:
        return {
            "lesson_id": self.lesson_id,
            "room_id": self.room_id,
            "sender_instance_id": self.sender_instance_id,
            "sender_profile_id": self.sender_profile_id,
        }

    @classmethod
    def decode_result(cls, value: Mapping[str, JsonValue]) -> Any:
        return value


@dataclass(frozen=True, slots=True)
class LessonsListCommand(Command[Any]):
    method: ClassVar[str] = "governance.lesson.list"
    submission: SubmissionMetadata
    scope: str | None
    def encode_params(self) -> Mapping[str, JsonValue]: return {"scope": self.scope}
    @classmethod
    def decode_result(cls, value: Mapping[str, JsonValue]) -> Any: return value
