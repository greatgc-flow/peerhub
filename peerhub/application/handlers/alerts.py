"""Room-alert command registration handlers."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from peerhub.application.alert_raise import (
    AlertRaiseCoordinator,
    AlertRaiseResult,
)
from peerhub.application.commands.alerts import AlertRaiseCommand
from peerhub.application.handlers._params import required_text
from peerhub.core.protocol import CommandEnvelope, JsonValue


def register_alert_handlers(
    *,
    api: Any,
    coordinator: AlertRaiseCoordinator,
) -> None:
    """Register the existing room-alert wire handler unchanged."""

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
    mutating = Mutability.MUTATING
    any_scope = ScopeKind.ANY
    domain_atomic_required = IdempotencyPolicy.DOMAIN_ATOMIC_REQUIRED
    available = CommandAvailability.AVAILABLE

    def decode_alert(envelope: CommandEnvelope) -> AlertRaiseCommand:
        return AlertRaiseCommand(
            submission=submission(envelope),
            room_id=required_text(envelope, "room_id"),
            raiser_instance_id=required_text(
                envelope, "raiser_instance_id"
            ),
            raiser_profile_id=required_text(
                envelope, "raiser_profile_id"
            ),
            severity=required_text(envelope, "severity"),
            message=required_text(envelope, "message"),
        )

    def encode_result(
        result: AlertRaiseResult,
    ) -> Mapping[str, JsonValue]:
        return {
            "alert_id": result.alert_id,
            "alert_target_id": result.alert_target_id,
            "room_id": result.room_id,
            "recipient_profile_ids": result.recipient_profile_ids,
            "inbox_message_target_ids": (
                result.inbox_message_target_ids
            ),
        }

    register(descriptor(
        "coordination.alert.raise",
        mutating,
        any_scope,
        # Like telemetry.error.record, each call is a deliberate new
        # mutation. The boundary still requires a per-call key; the
        # coordinator itself allocates fresh governance request IDs.
        domain_atomic_required,
        decode_alert,
        lambda command, _context: coordinator.raise_alert(
            room_id=command.room_id,
            raiser_instance_id=command.raiser_instance_id,
            raiser_profile_id=command.raiser_profile_id,
            severity=command.severity,
            message=command.message,
        ),
        encode_result,
        available,
    ))
