"""Governance-feedback command registration handlers."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, cast

from peerhub.application.commands.feedback import (
    FeedbackAddCommand,
    FeedbackListCommand,
    FeedbackResolveCommand,
)
from peerhub.application.handlers._params import optional_text, required_text
from peerhub.core.protocol import CommandEnvelope, JsonValue
from peerhub.governance.feedback import FeedbackService


def register_feedback_handlers(*, api: Any, service: FeedbackService) -> None:
    """Register the existing governance-feedback wire handlers unchanged."""

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

    def decode_add(envelope: CommandEnvelope) -> FeedbackAddCommand:
        detail = envelope.params.get("detail", "")
        if not isinstance(detail, str):
            raise ValueError("detail must be a string")
        return FeedbackAddCommand(
            submission=submission(envelope),
            source_peer=required_text(envelope, "source_peer"),
            category=required_text(envelope, "category"),
            severity=required_text(envelope, "severity"),
            title=required_text(envelope, "title"),
            detail=detail,
            actor_id=required_text(envelope, "actor_id"),
        )

    def decode_list(envelope: CommandEnvelope) -> FeedbackListCommand:
        return FeedbackListCommand(submission(envelope))

    def decode_resolve(
        envelope: CommandEnvelope,
    ) -> FeedbackResolveCommand:
        return FeedbackResolveCommand(
            submission=submission(envelope),
            feedback_id=required_text(envelope, "feedback_id"),
            status=required_text(envelope, "status"),
            actor_id=required_text(envelope, "actor_id"),
            owner=optional_text(envelope, "owner"),
        )

    def encode_feedback(
        results: Sequence[Any],
    ) -> Mapping[str, JsonValue]:
        items = [
            {
                "target_id": result.target_id,
                "revision": result.revision,
                "state": result.state,
            }
            for result in results
        ]
        return {"feedback": cast(JsonValue, items)}

    register(descriptor(
        "governance.feedback.create",
        mutating,
        any_scope,
        domain_atomic_required,
        decode_add,
        lambda command, _context: service.add_feedback(
            source_peer=command.source_peer,
            category=command.category,
            severity=command.severity,
            title=command.title,
            detail=command.detail,
            actor_id=command.actor_id,
        ),
        receipt,
        available,
    ))
    register(descriptor(
        "governance.feedback.list",
        read_only,
        any_scope,
        idempotency_read_only,
        decode_list,
        lambda _command, _context: service.list_feedback(),
        encode_feedback,
        available,
    ))
    register(descriptor(
        "governance.feedback.resolve",
        mutating,
        any_scope,
        domain_atomic_required,
        decode_resolve,
        lambda command, _context: service.resolve_feedback(
            command.feedback_id,
            status=command.status,
            owner=command.owner,
            actor_id=command.actor_id,
        ),
        receipt,
        available,
    ))
