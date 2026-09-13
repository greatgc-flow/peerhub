"""Duty and room-session command registration handlers."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, cast

from peerhub.application.commands.duty import (
    TerminalCloseCommand,
    TerminalDutySweepCommand,
    TerminalHandoffCommand,
    TerminalHeartbeatCommand,
)
from peerhub.application.commands.sessions import (
    SessionCloseCommand,
    SessionHeartbeatCommand,
    SessionOpenCommand,
)
from peerhub.core.protocol import CommandEnvelope, JsonValue
from peerhub.dispatch.duty_lease import (
    DutyLeaseCoordinator,
    DutyLeaseSnapshot,
    DutyOwnerIdentity,
)
from peerhub.dispatch.room_session import (
    RoomParticipationCoordinator,
    RoomSessionEndRequest,
    RoomSessionHeartbeatRequest,
    RoomSessionOpenRequest,
    RoomSessionSnapshot,
)
from peerhub.dispatch.terminal_duty import TerminalDutyService


@dataclass(frozen=True)
class _TerminalCloseResult:
    duty_lease: DutyLeaseSnapshot
    session_close_status: str
    session: RoomSessionSnapshot | None = None
    session_close_reason: str | None = None


def register_duty_handlers(
    *,
    api: Any,
    duty: DutyLeaseCoordinator,
    terminal_duty: TerminalDutyService,
    room_session: RoomParticipationCoordinator | None,
) -> None:
    """Register the existing terminal-duty wire handlers unchanged."""

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

    def text(envelope: CommandEnvelope, name: str) -> str:
        value = envelope.params[name]
        if not isinstance(value, str):
            raise ValueError(f"{name} must be a string")
        return value

    def integer(envelope: CommandEnvelope, name: str) -> int:
        value = envelope.params[name]
        if not isinstance(value, int) or isinstance(value, bool):
            raise ValueError(f"{name} must be an integer")
        return value

    def boolean(envelope: CommandEnvelope, name: str) -> bool:
        value = envelope.params.get(name, False)
        if not isinstance(value, bool):
            raise ValueError(f"{name} must be a boolean")
        return value

    def optional_text(envelope: CommandEnvelope, name: str) -> str:
        value = envelope.params.get(name, "")
        if not isinstance(value, str):
            raise ValueError(f"{name} must be a string")
        return value

    def optional_integer(envelope: CommandEnvelope, name: str) -> int:
        value = envelope.params.get(name, 0)
        if not isinstance(value, int) or isinstance(value, bool):
            raise ValueError(f"{name} must be an integer")
        return value

    def owner(command: TerminalHeartbeatCommand) -> DutyOwnerIdentity:
        return DutyOwnerIdentity(command.instance_id, command.profile_id)

    def decode_handoff(envelope: CommandEnvelope) -> TerminalHandoffCommand:
        return TerminalHandoffCommand(
            submission(envelope),
            text(envelope, "current_lease_id"),
            text(envelope, "room_id"),
            text(envelope, "current_instance_id"),
            text(envelope, "current_profile_id"),
            integer(envelope, "term"),
            integer(envelope, "authority_epoch"),
            text(envelope, "new_instance_id"),
            text(envelope, "new_profile_id"),
            text(envelope, "new_owner_principal_id"),
            integer(envelope, "new_authority_epoch"),
        )

    def decode_heartbeat(
        envelope: CommandEnvelope,
    ) -> TerminalHeartbeatCommand:
        return TerminalHeartbeatCommand(
            submission(envelope),
            text(envelope, "lease_id"),
            text(envelope, "room_id"),
            text(envelope, "instance_id"),
            text(envelope, "profile_id"),
            integer(envelope, "term"),
            integer(envelope, "authority_epoch"),
        )

    def decode_close(envelope: CommandEnvelope) -> TerminalCloseCommand:
        command = TerminalCloseCommand(
            submission(envelope),
            text(envelope, "lease_id"),
            text(envelope, "room_id"),
            text(envelope, "instance_id"),
            text(envelope, "profile_id"),
            integer(envelope, "term"),
            integer(envelope, "authority_epoch"),
            boolean(envelope, "close_session"),
            optional_text(envelope, "session_id"),
            optional_integer(envelope, "session_generation"),
            optional_text(envelope, "workspace_scope_id"),
            optional_text(envelope, "actor_principal_id"),
        )
        if command.close_session and (
            not command.session_id
            or command.session_generation < 1
            or not command.workspace_scope_id
            or not command.actor_principal_id
        ):
            raise ValueError(
                "close_session requires session_id, a positive "
                "session_generation, workspace_scope_id, and "
                "actor_principal_id"
            )
        return command

    def decode_sweep(envelope: CommandEnvelope) -> TerminalDutySweepCommand:
        return TerminalDutySweepCommand(
            submission(envelope),
            text(envelope, "role"),
            text(envelope, "recovery_actor_principal_id"),
            text(envelope, "trigger"),
            text(envelope, "evidence_digest"),
            text(envelope, "policy_id"),
            text(envelope, "policy_revision"),
        )

    def encode_lease(result: Any) -> Mapping[str, JsonValue]:
        return {
            "lease_id": result.lease_id,
            "room_id": result.room_id,
            "role": result.role,
            "state": result.state.value,
            "term": result.term,
            "authority_epoch": result.authority_epoch,
        }

    def close_terminal(command: TerminalCloseCommand) -> _TerminalCloseResult:
        duty_lease = terminal_duty.close_terminal_duty(
            command.lease_id,
            command.room_id,
            DutyOwnerIdentity(command.instance_id, command.profile_id),
            command.term,
            command.authority_epoch,
        )
        if not command.close_session:
            return _TerminalCloseResult(duty_lease, "not_requested")
        if room_session is None:
            return _TerminalCloseResult(
                duty_lease,
                "failed",
                session_close_reason=(
                    "room participation coordinator is unavailable"
                ),
            )
        try:
            session = room_session.end_session(
                RoomSessionEndRequest(
                    session_id=command.session_id,
                    session_generation=command.session_generation,
                    workspace_scope_id=command.workspace_scope_id,
                    room_id=command.room_id,
                    actor_principal_id=command.actor_principal_id,
                    owner=DutyOwnerIdentity(
                        command.instance_id, command.profile_id
                    ),
                )
            )
        except Exception as exc:
            return _TerminalCloseResult(
                duty_lease,
                "failed",
                session_close_reason=f"{type(exc).__name__}: {exc}",
            )
        return _TerminalCloseResult(duty_lease, "ok", session)

    def encode_close(
        result: _TerminalCloseResult,
    ) -> Mapping[str, JsonValue]:
        duty_close: dict[str, JsonValue] = {
            "status": "ok",
            "lease": dict(encode_lease(result.duty_lease)),
        }
        session_close: dict[str, JsonValue] = {
            "status": result.session_close_status,
        }
        if result.session is not None:
            session_close["session_id"] = result.session.session_id
            session_close["session_generation"] = (
                result.session.session_generation
            )
            session_close["state"] = result.session.state.value
        if result.session_close_reason is not None:
            session_close["reason"] = result.session_close_reason
        return {
            "duty_close": duty_close,
            "session_close": session_close,
        }

    def encode_sweep(
        leases: tuple[DutyLeaseSnapshot, ...],
    ) -> Mapping[str, JsonValue]:
        encoded = [dict(encode_lease(lease)) for lease in leases]
        return {
            "expired_count": len(encoded),
            "leases": cast(JsonValue, encoded),
        }

    register(descriptor(
        "coordination.terminal.handoff",
        mutating,
        any_scope,
        domain_atomic_required,
        decode_handoff,
        lambda command, _context: terminal_duty.handoff_terminal_duty(
            command.current_lease_id,
            command.room_id,
            DutyOwnerIdentity(
                command.current_instance_id,
                command.current_profile_id,
            ),
            command.term,
            command.authority_epoch,
            DutyOwnerIdentity(
                command.new_instance_id,
                command.new_profile_id,
            ),
            command.new_owner_principal_id,
            command.new_authority_epoch,
        ),
        encode_lease,
        available,
    ))
    register(descriptor(
        "coordination.terminal.heartbeat",
        mutating,
        any_scope,
        domain_atomic_required,
        decode_heartbeat,
        lambda command, _context: terminal_duty.send_heartbeat(
            command.lease_id,
            command.room_id,
            owner(command),
            command.term,
            command.authority_epoch,
        ),
        encode_lease,
        available,
    ))
    register(descriptor(
        "coordination.terminal.close",
        mutating,
        any_scope,
        domain_atomic_required,
        decode_close,
        lambda command, _context: close_terminal(command),
        encode_close,
        available,
    ))
    register(descriptor(
        "coordination.terminal.duty_sweep",
        mutating,
        any_scope,
        domain_atomic_required,
        decode_sweep,
        lambda command, _context: duty.sweep_expired_leases(
            command.role,
            recovery_actor_principal_id=(
                command.recovery_actor_principal_id
            ),
            trigger=command.trigger,
            evidence_digest=command.evidence_digest,
            policy_id=command.policy_id,
            policy_revision=command.policy_revision,
        ),
        encode_sweep,
        available,
    ))


def register_room_session_handlers(
    *,
    api: Any,
    coordinator: RoomParticipationCoordinator,
) -> None:
    """Register the existing room-session wire handlers unchanged."""

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

    def text(envelope: CommandEnvelope, name: str) -> str:
        value = envelope.params[name]
        if not isinstance(value, str):
            raise ValueError(f"{name} must be a string")
        return value

    def integer(envelope: CommandEnvelope, name: str) -> int:
        value = envelope.params[name]
        if not isinstance(value, int) or isinstance(value, bool):
            raise ValueError(f"{name} must be an integer")
        return value

    def owner(
        command: SessionOpenCommand | SessionCloseCommand,
    ) -> DutyOwnerIdentity:
        return DutyOwnerIdentity(command.instance_id, command.profile_id)

    def decode_open(envelope: CommandEnvelope) -> SessionOpenCommand:
        return SessionOpenCommand(
            submission(envelope),
            text(envelope, "workspace_scope_id"),
            text(envelope, "room_id"),
            text(envelope, "actor_principal_id"),
            text(envelope, "instance_id"),
            text(envelope, "profile_id"),
            text(envelope, "session_fingerprint"),
            integer(envelope, "heartbeat_timeout_ms"),
        )

    def decode_close(envelope: CommandEnvelope) -> SessionCloseCommand:
        return SessionCloseCommand(
            submission(envelope),
            text(envelope, "session_id"),
            integer(envelope, "session_generation"),
            text(envelope, "workspace_scope_id"),
            text(envelope, "room_id"),
            text(envelope, "actor_principal_id"),
            text(envelope, "instance_id"),
            text(envelope, "profile_id"),
        )

    def decode_heartbeat(
        envelope: CommandEnvelope,
    ) -> SessionHeartbeatCommand:
        return SessionHeartbeatCommand(
            submission(envelope),
            text(envelope, "session_id"),
            integer(envelope, "session_generation"),
            text(envelope, "workspace_scope_id"),
            text(envelope, "room_id"),
            text(envelope, "actor_principal_id"),
            text(envelope, "instance_id"),
            text(envelope, "profile_id"),
            integer(envelope, "heartbeat_timeout_ms"),
        )

    def encode_snapshot(
        snapshot: RoomSessionSnapshot,
    ) -> Mapping[str, JsonValue]:
        return {
            "session_id": snapshot.session_id,
            "workspace_scope_id": snapshot.workspace_scope_id,
            "room_id": snapshot.room_id,
            "actor_principal_id": snapshot.actor_principal_id,
            "owner": {
                "instance_id": snapshot.owner.instance_id,
                "profile_id": snapshot.owner.profile_id,
            },
            "session_fingerprint": snapshot.session_fingerprint,
            "session_generation": snapshot.session_generation,
            "resume_parent_session_id": snapshot.resume_parent_session_id,
            "state": snapshot.state.value,
            "heartbeat_expires_at": snapshot.heartbeat_expires_at,
            "created_at": snapshot.created_at,
            "updated_at": snapshot.updated_at,
        }

    register(descriptor(
        "coordination.session.open",
        mutating,
        any_scope,
        domain_atomic_required,
        decode_open,
        lambda command, _context: coordinator.open_session(
            RoomSessionOpenRequest(
                workspace_scope_id=command.workspace_scope_id,
                room_id=command.room_id,
                actor_principal_id=command.actor_principal_id,
                owner=owner(command),
                session_fingerprint=command.session_fingerprint,
                heartbeat_timeout_ms=command.heartbeat_timeout_ms,
            )
        ),
        encode_snapshot,
        available,
    ))
    register(descriptor(
        "coordination.session.close",
        mutating,
        any_scope,
        domain_atomic_required,
        decode_close,
        lambda command, _context: coordinator.end_session(
            RoomSessionEndRequest(
                session_id=command.session_id,
                session_generation=command.session_generation,
                workspace_scope_id=command.workspace_scope_id,
                room_id=command.room_id,
                actor_principal_id=command.actor_principal_id,
                owner=owner(command),
            )
        ),
        encode_snapshot,
        available,
    ))
    register(descriptor(
        "coordination.session.heartbeat",
        mutating,
        any_scope,
        domain_atomic_required,
        decode_heartbeat,
        lambda command, _context: coordinator.heartbeat(
            RoomSessionHeartbeatRequest(
                session_id=command.session_id,
                session_generation=command.session_generation,
                workspace_scope_id=command.workspace_scope_id,
                room_id=command.room_id,
                actor_principal_id=command.actor_principal_id,
                owner=owner(command),
            ),
            heartbeat_timeout_ms=command.heartbeat_timeout_ms,
        ),
        encode_snapshot,
        available,
    ))
