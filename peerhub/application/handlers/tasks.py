"""Task lifecycle and approval command registration handlers."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, cast

from peerhub.application.commands.tasks import (
    ApprovalRequestCommand,
    TaskCancelCommand,
    TaskClaimStartCommand,
    TaskCheckpointCommand,
    TaskCompleteCommand,
    TaskCreateCommand,
    TaskFailCommand,
    TaskFailoverCommand,
    TaskStatusCommand,
)
from peerhub.application.handlers._params import string_tuple as strings
from peerhub.core.protocol import CommandEnvelope, JsonValue
from peerhub.governance.tasks import TaskService


def register_task_handlers(*, api: Any, service: TaskService) -> None:
    """Register the existing task and approval wire handlers unchanged."""

    from peerhub.application.api import (
        CommandAvailability,
        CommandDescriptor,
        IdempotencyPolicy,
        Mutability,
        ScopeKind,
    )

    register = api.register
    submission = api._submission
    receipt = api._receipt
    descriptor = CommandDescriptor
    mutating = Mutability.MUTATING
    read_only = Mutability.READ_ONLY
    any_scope = ScopeKind.ANY
    domain_atomic_required = IdempotencyPolicy.DOMAIN_ATOMIC_REQUIRED
    idempotency_read_only = IdempotencyPolicy.READ_ONLY
    available = CommandAvailability.AVAILABLE

    def text(params: Mapping[str, JsonValue], name: str) -> str:
        if not isinstance(params[name], str):
            raise ValueError(f"{name} must be a string")
        return cast(str, params[name])

    def integer(
        params: Mapping[str, JsonValue], name: str
    ) -> int | None:
        if params[name] is not None and (
            not isinstance(params[name], int)
            or isinstance(params[name], bool)
        ):
            raise ValueError(f"{name} must be an integer or null")
        return cast(int | None, params[name])

    def checkpoint(envelope: CommandEnvelope) -> TaskCheckpointCommand:
        params = envelope.params
        return TaskCheckpointCommand(
            submission(envelope),
            text(params, "task_id"),
            text(params, "actor_id"),
            text(params, "checkpoint_id"),
            text(params, "stage"),
            text(params, "request_id"),
            text(params, "attempt_id"),
            (
                None
                if params["resume_token_ref"] is None
                else text(params, "resume_token_ref")
            ),
            strings(params, "completed_units"),
            strings(params, "remaining_units"),
            integer(params, "expected_revision"),
        )

    def create(envelope: CommandEnvelope) -> TaskCreateCommand:
        params = envelope.params
        return TaskCreateCommand(
            submission(envelope),
            text(params, "task_id"),
            text(params, "summary"),
            text(params, "spec"),
            text(params, "creator_id"),
            None if params["room_id"] is None else text(params, "room_id"),
            text(params, "current_stage"),
        )

    def claim_start(envelope: CommandEnvelope) -> TaskClaimStartCommand:
        params = envelope.params
        return TaskClaimStartCommand(
            submission(envelope),
            text(params, "task_id"),
            text(params, "actor_id"),
            text(params, "request_id"),
            text(params, "coordinator"),
            text(params, "attempt_id"),
            integer(params, "expected_revision"),
        )

    def complete(envelope: CommandEnvelope) -> TaskCompleteCommand:
        params = envelope.params
        return TaskCompleteCommand(
            submission(envelope),
            text(params, "task_id"),
            text(params, "actor_id"),
            integer(params, "expected_revision"),
        )

    def fail(envelope: CommandEnvelope) -> TaskFailCommand:
        params = envelope.params
        return TaskFailCommand(
            submission(envelope),
            text(params, "task_id"),
            text(params, "actor_id"),
            text(params, "failure_class"),
            text(params, "reason"),
            integer(params, "expected_revision"),
        )

    def cancel(envelope: CommandEnvelope) -> TaskCancelCommand:
        params = envelope.params
        return TaskCancelCommand(
            submission(envelope),
            text(params, "task_id"),
            text(params, "actor_id"),
            text(params, "reason"),
            integer(params, "expected_revision"),
        )

    def status(envelope: CommandEnvelope) -> TaskStatusCommand:
        return TaskStatusCommand(
            submission(envelope), text(envelope.params, "task_id")
        )

    def failover(envelope: CommandEnvelope) -> TaskFailoverCommand:
        params = envelope.params
        return TaskFailoverCommand(
            submission(envelope),
            text(params, "task_id"),
            text(params, "to_actor_id"),
            text(params, "reason"),
            integer(params, "expected_revision"),
        )

    def encode_status(result: Any) -> Mapping[str, JsonValue]:
        return {
            "target_id": result.target_id,
            "revision": result.revision,
            "state": result.state,
        }

    register(descriptor(
        "coordination.task.create",
        mutating,
        any_scope,
        domain_atomic_required,
        create,
        lambda command, _context: service.create(
            task_id=command.task_id,
            summary=command.summary,
            spec=command.spec,
            creator_id=command.creator_id,
            room_id=command.room_id,
            current_stage=command.current_stage,
        ),
        receipt,
        available,
    ))
    register(descriptor(
        "coordination.task.claim_start",
        mutating,
        any_scope,
        domain_atomic_required,
        claim_start,
        lambda command, _context: service.claim_start(
            command.task_id,
            actor_id=command.actor_id,
            request_id=command.request_id,
            coordinator=command.coordinator,
            attempt_id=command.attempt_id,
            expected_revision=command.expected_revision,
        ),
        receipt,
        available,
    ))
    register(descriptor(
        "coordination.task.checkpoint",
        mutating,
        any_scope,
        domain_atomic_required,
        checkpoint,
        lambda command, _context: service.checkpoint(
            task_id=command.task_id,
            actor_id=command.actor_id,
            checkpoint_id=command.checkpoint_id,
            stage=command.stage,
            request_id=command.request_id,
            attempt_id=command.attempt_id,
            resume_token_ref=command.resume_token_ref,
            completed_units=command.completed_units,
            remaining_units=command.remaining_units,
            expected_revision=command.expected_revision,
        ),
        receipt,
        available,
    ))
    register(descriptor(
        "coordination.task.complete",
        mutating,
        any_scope,
        domain_atomic_required,
        complete,
        lambda command, _context: service.complete(
            command.task_id,
            actor_id=command.actor_id,
            expected_revision=command.expected_revision,
        ),
        receipt,
        available,
    ))
    register(descriptor(
        "coordination.task.fail",
        mutating,
        any_scope,
        domain_atomic_required,
        fail,
        lambda command, _context: service.fail(
            command.task_id,
            actor_id=command.actor_id,
            failure_class=command.failure_class,
            reason=command.reason,
            expected_revision=command.expected_revision,
        ),
        receipt,
        available,
    ))
    register(descriptor(
        "coordination.task.cancel",
        mutating,
        any_scope,
        domain_atomic_required,
        cancel,
        lambda command, _context: service.cancel(
            command.task_id,
            actor_id=command.actor_id,
            reason=command.reason,
            expected_revision=command.expected_revision,
        ),
        receipt,
        available,
    ))
    register(descriptor(
        "coordination.task.status",
        read_only,
        any_scope,
        idempotency_read_only,
        status,
        lambda command, _context: service.get_target(command.task_id),
        encode_status,
        available,
    ))
    register(descriptor(
        "coordination.task.failover",
        mutating,
        any_scope,
        domain_atomic_required,
        failover,
        lambda command, _context: service.request_failover(
            command.task_id,
            to_actor_id=command.to_actor_id,
            reason=command.reason,
            expected_revision=command.expected_revision,
        ),
        receipt,
        available,
    ))

    def approval(envelope: CommandEnvelope) -> ApprovalRequestCommand:
        params = envelope.params
        values: list[str] = []
        for name in (
            "task_id",
            "requester_id",
            "approval_id",
            "approver_id",
        ):
            value = params[name]
            if not isinstance(value, str):
                raise ValueError(f"{name} must be a string")
            values.append(value)
        return ApprovalRequestCommand(submission(envelope), *values)

    register(descriptor(
        "governance.approval.request",
        mutating,
        any_scope,
        domain_atomic_required,
        approval,
        lambda command, _context: service.request_approval(
            command.task_id,
            requester_id=command.requester_id,
            approval_id=command.approval_id,
            approver_id=command.approver_id,
        ),
        receipt,
        available,
    ))
