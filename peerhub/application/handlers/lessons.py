"""Lesson lifecycle and distribution command registration handlers."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, cast

from peerhub.application.commands.lessons import (
    LessonActivateCommand,
    LessonApproveCommand,
    LessonBroadcastCommand,
    LessonInjectCommand,
    LessonProposeCommand,
    LessonQuarantineCommand,
    LessonRetireCommand,
    LessonSupersedeCommand,
    LessonSweepCommand,
    LessonsListCommand,
)
from peerhub.application.lesson_broadcast import (
    LessonBroadcastCoordinator,
    LessonBroadcastResult,
)
from peerhub.application.lesson_inject import (
    LessonInjectionContext,
    LessonInjectionPolicy,
    inject_lessons,
)
from peerhub.application.handlers._params import string_tuple as strings
from peerhub.core.protocol import CommandEnvelope, JsonValue
from peerhub.governance.activity import list_active_lessons
from peerhub.governance.broker import GovernanceBroker
from peerhub.governance.lessons import LessonService
from peerhub.governance.rooms import RoomsService


def register_lesson_handlers(
    *,
    api: Any,
    service: LessonService,
    broker: GovernanceBroker,
    room: RoomsService | None,
) -> None:
    """Register the existing lesson-domain wire handlers unchanged."""

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

    def boolean(
        params: Mapping[str, JsonValue], name: str, default: bool
    ) -> bool:
        value = params.get(name, default)
        if not isinstance(value, bool):
            raise ValueError(f"{name} must be a boolean")
        return value

    def decode_propose(envelope: CommandEnvelope) -> LessonProposeCommand:
        params = envelope.params
        return LessonProposeCommand(
            submission(envelope),
            text(params, "lesson_id"),
            text(params, "title"),
            text(params, "rule"),
            text(params, "category"),
            text(params, "severity"),
            text(params, "proposer_id"),
            strings(params, "affected_peers"),
            text(params, "scope_kind"),
            (
                None
                if params["workspace_id"] is None
                else text(params, "workspace_id")
            ),
            boolean(params, "sticky", False),
            None if params.get("os") is None else strings(params, "os"),
            (
                None
                if params.get("shell") is None
                else strings(params, "shell")
            ),
            (
                None
                if params.get("task_types") is None
                else strings(params, "task_types")
            ),
        )

    def decode_activate(envelope: CommandEnvelope) -> LessonActivateCommand:
        params = envelope.params
        return LessonActivateCommand(
            submission(envelope),
            text(params, "lesson_id"),
            text(params, "actor_id"),
            integer(params, "expected_revision"),
        )

    def decode_retire(envelope: CommandEnvelope) -> LessonRetireCommand:
        params = envelope.params
        return LessonRetireCommand(
            submission(envelope),
            text(params, "lesson_id"),
            text(params, "actor_id"),
            text(params, "reason"),
            integer(params, "expected_revision"),
        )

    def decode_approve(envelope: CommandEnvelope) -> LessonApproveCommand:
        params = envelope.params
        authority_target_id = params["authority_target_id"]
        if authority_target_id is not None and not isinstance(
            authority_target_id, str
        ):
            raise ValueError("authority_target_id must be a string or null")
        return LessonApproveCommand(
            submission(envelope),
            text(params, "lesson_id"),
            text(params, "approved_by_actor_id"),
            authority_target_id,
            integer(params, "expected_revision"),
        )

    def decode_supersede(
        envelope: CommandEnvelope,
    ) -> LessonSupersedeCommand:
        params = envelope.params
        return LessonSupersedeCommand(
            submission(envelope),
            text(params, "lesson_id"),
            text(params, "actor_id"),
            text(params, "replacement_lesson_id"),
            integer(params, "expected_revision"),
        )

    def decode_quarantine(
        envelope: CommandEnvelope,
    ) -> LessonQuarantineCommand:
        params = envelope.params
        return LessonQuarantineCommand(
            submission(envelope),
            text(params, "lesson_id"),
            text(params, "actor_id"),
            text(params, "reason"),
            text(params, "evidence"),
            integer(params, "expected_revision"),
        )

    def decode_sweep(envelope: CommandEnvelope) -> LessonSweepCommand:
        return LessonSweepCommand(
            submission(envelope), text(envelope.params, "actor_id")
        )

    register(descriptor(
        "governance.lesson.propose",
        mutating,
        any_scope,
        domain_atomic_required,
        decode_propose,
        lambda command, _context: service.propose(
            lesson_id=command.lesson_id,
            title=command.title,
            rule=command.rule,
            category=command.category,
            severity=command.severity,
            proposer_id=command.proposer_id,
            affected_peers=command.affected_peers,
            scope_kind=command.scope_kind,
            workspace_id=command.workspace_id,
            sticky=command.sticky,
            os=command.os,
            shell=command.shell,
            task_types=command.task_types,
        ),
        receipt,
        available,
    ))
    register(descriptor(
        "governance.lesson.approve",
        mutating,
        any_scope,
        domain_atomic_required,
        decode_approve,
        lambda command, _context: service.approve(
            command.lesson_id,
            approved_by_actor_id=command.approved_by_actor_id,
            authority_target_id=command.authority_target_id,
            expected_revision=command.expected_revision,
        ),
        receipt,
        available,
    ))
    register(descriptor(
        "governance.lesson.activate",
        mutating,
        any_scope,
        domain_atomic_required,
        decode_activate,
        lambda command, _context: service.activate(
            command.lesson_id,
            actor_id=command.actor_id,
            expected_revision=command.expected_revision,
        ),
        receipt,
        available,
    ))
    register(descriptor(
        "governance.lesson.supersede",
        mutating,
        any_scope,
        domain_atomic_required,
        decode_supersede,
        lambda command, _context: service.supersede(
            command.lesson_id,
            actor_id=command.actor_id,
            replacement_lesson_id=command.replacement_lesson_id,
            expected_revision=command.expected_revision,
        ),
        receipt,
        available,
    ))
    register(descriptor(
        "governance.lesson.quarantine",
        mutating,
        any_scope,
        domain_atomic_required,
        decode_quarantine,
        lambda command, _context: service.quarantine(
            command.lesson_id,
            actor_id=command.actor_id,
            reason=command.reason,
            evidence=command.evidence,
            expected_revision=command.expected_revision,
        ),
        receipt,
        available,
    ))

    def encode_sweep(results: Sequence[Any]) -> Mapping[str, JsonValue]:
        return {
            "retired": cast(
                JsonValue,
                [result.receipt.target_id for result in results],
            )
        }

    register(descriptor(
        "governance.lesson.sweep",
        mutating,
        any_scope,
        domain_atomic_required,
        decode_sweep,
        lambda command, _context: service.sweep_expired(
            actor_id=command.actor_id
        ),
        encode_sweep,
        available,
    ))

    register(descriptor(
        "governance.lesson.retire",
        mutating,
        any_scope,
        domain_atomic_required,
        decode_retire,
        lambda command, _context: service.retire(
            command.lesson_id,
            actor_id=command.actor_id,
            reason=command.reason,
            expected_revision=command.expected_revision,
        ),
        receipt,
        available,
    ))

    def decode_list(envelope: CommandEnvelope) -> LessonsListCommand:
        value = envelope.params["scope"]
        if value is not None and not isinstance(value, str):
            raise ValueError("scope must be a string or null")
        return LessonsListCommand(submission(envelope), value)

    def encode_lessons(
        results: Sequence[Any],
    ) -> Mapping[str, JsonValue]:
        lessons = [
            {
                "target_id": result.target_id,
                "revision": result.revision,
                "state": result.state,
            }
            for result in results
        ]
        return {"lessons": cast(JsonValue, lessons)}

    def decode_inject(envelope: CommandEnvelope) -> LessonInjectCommand:
        params = envelope.params
        os_value = text(params, "os") if params.get("os") is not None else None
        shell_value = (
            text(params, "shell")
            if params.get("shell") is not None
            else None
        )
        task_types = (
            frozenset(strings(params, "task_types"))
            if params.get("task_types") is not None
            else frozenset[str]()
        )
        return LessonInjectCommand(
            submission(envelope),
            text(params, "target_peer_id"),
            text(params, "workspace_id"),
            os_value,
            shell_value,
            task_types,
        )

    def encode_inject(result: str | None) -> Mapping[str, JsonValue]:
        return {"injection_block": result}

    policy = LessonInjectionPolicy()
    register(descriptor(
        "governance.lesson.inject",
        read_only,
        any_scope,
        idempotency_read_only,
        decode_inject,
        lambda command, _context: inject_lessons(
            broker,
            target_peer_id=command.target_peer_id,
            workspace_id=command.workspace_id,
            context=LessonInjectionContext(
                os=command.os,
                shell=command.shell,
                task_types=command.task_types,
            ),
            policy=policy,
        ),
        encode_inject,
        available,
    ))
    register(descriptor(
        "governance.lesson.list",
        read_only,
        any_scope,
        idempotency_read_only,
        decode_list,
        lambda command, _context: list_active_lessons(
            broker, command.scope
        ),
        encode_lessons,
        available,
    ))

    if room is not None:
        coordinator = LessonBroadcastCoordinator(
            broker=broker,
            lessons=service,
            rooms=room,
        )

        def decode_broadcast(
            envelope: CommandEnvelope,
        ) -> LessonBroadcastCommand:
            return LessonBroadcastCommand(
                submission(envelope),
                text(envelope.params, "lesson_id"),
                text(envelope.params, "room_id"),
                text(envelope.params, "sender_instance_id"),
                text(envelope.params, "sender_profile_id"),
            )

        def encode_broadcast(
            result: LessonBroadcastResult,
        ) -> Mapping[str, JsonValue]:
            return {
                "campaign_id": result.campaign_id,
                "campaign_target_id": result.campaign_target_id,
                "lesson_id": result.lesson_id,
                "room_id": result.room_id,
                "recipient_profile_ids": result.recipient_profile_ids,
                "inbox_message_target_ids": result.inbox_message_target_ids,
                "delivery_target_ids": result.delivery_target_ids,
            }

        register(descriptor(
            "coordination.lesson.broadcast",
            mutating,
            any_scope,
            domain_atomic_required,
            decode_broadcast,
            lambda command, _context: coordinator.broadcast(
                lesson_id=command.lesson_id,
                room_id=command.room_id,
                sender_instance_id=command.sender_instance_id,
                sender_profile_id=command.sender_profile_id,
                created_at=command.submission.client_timestamp,
            ),
            encode_broadcast,
            available,
        ))
