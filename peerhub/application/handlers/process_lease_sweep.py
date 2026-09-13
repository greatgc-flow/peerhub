"""Process-lease sweep command registration handlers."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from peerhub.application.commands.leases import LeaseSweepCommand
from peerhub.application.process_lease_sweep import (
    ProcessLeaseSweepCoordinator,
    ProcessLeaseSweepReport,
)
from peerhub.core.protocol import CommandEnvelope, JsonValue


def register_process_lease_sweep_handlers(
    *,
    api: Any,
    coordinator: ProcessLeaseSweepCoordinator,
) -> None:
    """Register the existing process-lease sweep wire handler unchanged."""

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

    def decode_sweep(envelope: CommandEnvelope) -> LeaseSweepCommand:
        limit = envelope.params.get("limit", 100)
        reap = envelope.params.get("reap", True)
        if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
            raise ValueError("limit must be a positive integer")
        if not isinstance(reap, bool):
            raise ValueError("reap must be a boolean")
        return LeaseSweepCommand(
            submission=submission(envelope),
            limit=limit,
            reap=reap,
        )

    def encode_sweep(
        report: ProcessLeaseSweepReport,
    ) -> Mapping[str, JsonValue]:
        swept: tuple[JsonValue, ...] = tuple({
            "lease_id": item.lease_id,
            "profile_id": item.profile_id,
            "pre_state": item.pre_state.value,
            "post_state": item.post_state.value,
            "process_alive": item.process_alive,
            "process_identity_matches": item.process_identity_matches,
            "actual_process_creation_time": (
                item.actual_process_creation_time
            ),
            "recovery_receipt_id": item.recovery_receipt_id,
            "recovery_decision": item.recovery_decision.value,
            "reaped": item.reaped,
            "reap_signal": item.reap_signal,
            "backoff_duration_seconds": (
                item.backoff_duration_seconds
            ),
        } for item in report.swept)
        return {
            "sweep_id": report.sweep_id,
            "as_of": report.as_of,
            "swept": swept,
        }

    register(descriptor(
        "dispatch.lease.sweep",
        mutating,
        any_scope,
        domain_atomic_required,
        decode_sweep,
        lambda command, context: coordinator.sweep(
            recovery_actor_principal_id=context.principal,
            limit=command.limit,
            reap=command.reap,
        ),
        encode_sweep,
        available,
    ))
