"""Governance effect-status command registration handlers."""

from __future__ import annotations

from typing import Any

from peerhub.application.broker_status import (
    MAX_VISIBLE_EFFECT_DELIVERIES,
    collect_effect_status,
)
from peerhub.application.commands.effects import EffectStatusCommand
from peerhub.core.protocol import CommandEnvelope
from peerhub.governance.broker import GovernanceBroker


def register_effect_status_handlers(
    *,
    api: Any,
    broker: GovernanceBroker,
) -> None:
    """Register the existing governance effect-status wire handler unchanged."""

    from peerhub.application.api import (
        CommandAvailability,
        CommandDescriptor,
        IdempotencyPolicy,
        Mutability,
        ScopeKind,
    )

    register = api.register
    submission = api._submission
    descriptor = CommandDescriptor
    read_only = Mutability.READ_ONLY
    any_scope = ScopeKind.ANY
    idempotency_read_only = IdempotencyPolicy.READ_ONLY
    available = CommandAvailability.AVAILABLE

    def decode_effect_status(
        envelope: CommandEnvelope,
    ) -> EffectStatusCommand:
        limit = envelope.params.get(
            "limit", MAX_VISIBLE_EFFECT_DELIVERIES
        )
        if (
            type(limit) is not int
            or not 1 <= limit <= MAX_VISIBLE_EFFECT_DELIVERIES
        ):
            raise ValueError(
                "limit must be an integer between 1 and "
                f"{MAX_VISIBLE_EFFECT_DELIVERIES}"
            )
        return EffectStatusCommand(
            submission=submission(envelope),
            limit=limit,
        )

    register(descriptor(
        "governance.effect.status",
        read_only,
        any_scope,
        idempotency_read_only,
        decode_effect_status,
        lambda command, _context: collect_effect_status(
            broker, limit=command.limit
        ),
        lambda result: result,
        available,
    ))
