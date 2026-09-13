"""Role-assignment command registration handlers."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, cast

from peerhub.application.commands.roles import (
    AssignRoleCommand,
    ReleaseRoleCommand,
    RoleStatusCommand,
)
from peerhub.application.role_assignment import (
    RoleAssignmentService,
    RoleReleaseResult,
)
from peerhub.core.protocol import CommandEnvelope, JsonValue


def register_role_handlers(*, api: Any, service: RoleAssignmentService) -> None:
    """Register the existing role-assignment wire handlers unchanged."""

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

    def required_text(envelope: CommandEnvelope, name: str) -> str:
        value = envelope.params[name]
        if not isinstance(value, str):
            raise ValueError(f"{name} must be a string")
        return value

    def optional_text(envelope: CommandEnvelope, name: str) -> str | None:
        value = envelope.params.get(name)
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError(f"{name} must be a string or null")
        return value

    def decode_assign(envelope: CommandEnvelope) -> AssignRoleCommand:
        return AssignRoleCommand(
            submission=submission(envelope),
            role=required_text(envelope, "role"),
            peer_node_id=required_text(envelope, "peer_node_id"),
            actor_id=required_text(envelope, "actor_id"),
        )

    def decode_release(envelope: CommandEnvelope) -> ReleaseRoleCommand:
        return ReleaseRoleCommand(
            submission=submission(envelope),
            role=required_text(envelope, "role"),
            actor_id=required_text(envelope, "actor_id"),
            peer_node_id=optional_text(envelope, "peer_node_id"),
        )

    def decode_status(envelope: CommandEnvelope) -> RoleStatusCommand:
        return RoleStatusCommand(submission(envelope))

    def encode_roles(results: Sequence[Any]) -> Mapping[str, JsonValue]:
        roles = [
            {
                "target_id": result.target_id,
                "revision": result.revision,
                "state": result.state,
            }
            for result in results
        ]
        return {"roles": cast(JsonValue, roles)}

    def encode_release(result: RoleReleaseResult) -> Mapping[str, JsonValue]:
        encoded_receipt = (
            None
            if result.submission is None
            else dict(receipt(result.submission))
        )
        target = (
            None
            if result.target is None
            else {
                "target_id": result.target.target_id,
                "revision": result.target.revision,
                "state": result.target.state,
            }
        )
        return {
            "disposition": result.disposition.value,
            "receipt": cast(JsonValue, encoded_receipt),
            "target": cast(JsonValue, target),
        }

    register(descriptor(
        "coordination.role.assign",
        mutating,
        any_scope,
        domain_atomic_required,
        decode_assign,
        lambda command, _context: service.assign_role(
            role=command.role,
            peer_node_id=command.peer_node_id,
            actor_id=command.actor_id,
        ),
        receipt,
        available,
    ))
    register(descriptor(
        "coordination.role.release",
        mutating,
        any_scope,
        domain_atomic_required,
        decode_release,
        lambda command, _context: service.release_role(
            role=command.role,
            actor_id=command.actor_id,
            peer_node_id=command.peer_node_id,
        ),
        encode_release,
        available,
    ))
    register(descriptor(
        "coordination.role.status",
        read_only,
        any_scope,
        idempotency_read_only,
        decode_status,
        lambda _command, _context: service.list_roles(),
        encode_roles,
        available,
    ))
