"""Task lifecycle and approval commands."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, ClassVar

from peerhub.application.commands import Command, SubmissionMetadata
from peerhub.core.protocol import JsonValue


@dataclass(frozen=True, slots=True)
class TaskCreateCommand(Command[Any]):
    method: ClassVar[str] = "coordination.task.create"
    submission: SubmissionMetadata
    task_id: str
    summary: str
    spec: str
    creator_id: str
    room_id: str | None = None
    current_stage: str = "initial"

    def encode_params(self) -> Mapping[str, JsonValue]:
        return {
            "task_id": self.task_id,
            "summary": self.summary,
            "spec": self.spec,
            "creator_id": self.creator_id,
            "room_id": self.room_id,
            "current_stage": self.current_stage,
        }

    @classmethod
    def decode_result(cls, value: Mapping[str, JsonValue]) -> Any:
        return value


@dataclass(frozen=True, slots=True)
class TaskClaimStartCommand(Command[Any]):
    method: ClassVar[str] = "coordination.task.claim_start"
    submission: SubmissionMetadata
    task_id: str
    actor_id: str
    request_id: str
    coordinator: str
    attempt_id: str
    expected_revision: int | None = None

    def encode_params(self) -> Mapping[str, JsonValue]:
        return {
            "task_id": self.task_id,
            "actor_id": self.actor_id,
            "request_id": self.request_id,
            "coordinator": self.coordinator,
            "attempt_id": self.attempt_id,
            "expected_revision": self.expected_revision,
        }

    @classmethod
    def decode_result(cls, value: Mapping[str, JsonValue]) -> Any:
        return value


@dataclass(frozen=True, slots=True)
class TaskCheckpointCommand(Command[Any]):
    method: ClassVar[str] = "coordination.task.checkpoint"
    submission: SubmissionMetadata
    task_id: str
    actor_id: str
    checkpoint_id: str
    stage: str
    request_id: str
    attempt_id: str
    resume_token_ref: str | None
    completed_units: tuple[str, ...]
    remaining_units: tuple[str, ...]
    expected_revision: int | None

    def encode_params(self) -> Mapping[str, JsonValue]:
        return {
            "task_id": self.task_id,
            "actor_id": self.actor_id,
            "checkpoint_id": self.checkpoint_id,
            "stage": self.stage,
            "request_id": self.request_id,
            "attempt_id": self.attempt_id,
            "resume_token_ref": self.resume_token_ref,
            "completed_units": self.completed_units,
            "remaining_units": self.remaining_units,
            "expected_revision": self.expected_revision,
        }

    @classmethod
    def decode_result(cls, value: Mapping[str, JsonValue]) -> Any:
        return value


@dataclass(frozen=True, slots=True)
class TaskCompleteCommand(Command[Any]):
    method: ClassVar[str] = "coordination.task.complete"
    submission: SubmissionMetadata
    task_id: str
    actor_id: str
    expected_revision: int | None = None

    def encode_params(self) -> Mapping[str, JsonValue]:
        return {
            "task_id": self.task_id,
            "actor_id": self.actor_id,
            "expected_revision": self.expected_revision,
        }

    @classmethod
    def decode_result(cls, value: Mapping[str, JsonValue]) -> Any:
        return value


@dataclass(frozen=True, slots=True)
class TaskFailCommand(Command[Any]):
    method: ClassVar[str] = "coordination.task.fail"
    submission: SubmissionMetadata
    task_id: str
    actor_id: str
    failure_class: str
    reason: str
    expected_revision: int | None = None

    def encode_params(self) -> Mapping[str, JsonValue]:
        return {
            "task_id": self.task_id,
            "actor_id": self.actor_id,
            "failure_class": self.failure_class,
            "reason": self.reason,
            "expected_revision": self.expected_revision,
        }

    @classmethod
    def decode_result(cls, value: Mapping[str, JsonValue]) -> Any:
        return value


@dataclass(frozen=True, slots=True)
class TaskCancelCommand(Command[Any]):
    method: ClassVar[str] = "coordination.task.cancel"
    submission: SubmissionMetadata
    task_id: str
    actor_id: str
    reason: str
    expected_revision: int | None = None

    def encode_params(self) -> Mapping[str, JsonValue]:
        return {
            "task_id": self.task_id,
            "actor_id": self.actor_id,
            "reason": self.reason,
            "expected_revision": self.expected_revision,
        }

    @classmethod
    def decode_result(cls, value: Mapping[str, JsonValue]) -> Any:
        return value


@dataclass(frozen=True, slots=True)
class TaskStatusCommand(Command[Any]):
    method: ClassVar[str] = "coordination.task.status"
    submission: SubmissionMetadata
    task_id: str

    def encode_params(self) -> Mapping[str, JsonValue]:
        return {"task_id": self.task_id}

    @classmethod
    def decode_result(cls, value: Mapping[str, JsonValue]) -> Any:
        return value


@dataclass(frozen=True, slots=True)
class TaskFailoverCommand(Command[Any]):
    method: ClassVar[str] = "coordination.task.failover"
    submission: SubmissionMetadata
    task_id: str
    to_actor_id: str
    reason: str
    expected_revision: int | None

    def encode_params(self) -> Mapping[str, JsonValue]:
        return {
            "task_id": self.task_id,
            "to_actor_id": self.to_actor_id,
            "reason": self.reason,
            "expected_revision": self.expected_revision,
        }

    @classmethod
    def decode_result(cls, value: Mapping[str, JsonValue]) -> Any:
        return value


@dataclass(frozen=True, slots=True)
class ApprovalRequestCommand(Command[Any]):
    method: ClassVar[str] = "governance.approval.request"
    submission: SubmissionMetadata
    task_id: str
    requester_id: str
    approval_id: str
    approver_id: str

    def encode_params(self) -> Mapping[str, JsonValue]:
        return {
            "task_id": self.task_id,
            "requester_id": self.requester_id,
            "approval_id": self.approval_id,
            "approver_id": self.approver_id,
        }

    @classmethod
    def decode_result(cls, value: Mapping[str, JsonValue]) -> Any:
        return value
