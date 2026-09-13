"""File-lock command registration handlers."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, cast

from peerhub.application.commands.locks import (
    LockAcquireCommand,
    LockReleaseCommand,
    LockStatusCommand,
)
from peerhub.core.protocol import CommandEnvelope, JsonValue
from peerhub.governance.contract import TargetState
from peerhub.governance.file_locks import FileLockService, FileUnlockResult


def register_lock_handlers(*, api: Any, service: FileLockService) -> None:
    """Register the existing file-lock wire handlers unchanged."""

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

    def decode_acquire(envelope: CommandEnvelope) -> LockAcquireCommand:
        return LockAcquireCommand(
            submission=submission(envelope),
            name=str(envelope.params.get("name", "")),
            owner=str(envelope.params.get("owner", "")),
            lock_scope=str(envelope.params.get("lock_scope", "file")),
        )

    def decode_release(envelope: CommandEnvelope) -> LockReleaseCommand:
        owner = envelope.params.get("owner")
        return LockReleaseCommand(
            submission=submission(envelope),
            name=str(envelope.params.get("name", "")),
            owner=str(owner) if owner is not None else None,
        )

    def decode_status(envelope: CommandEnvelope) -> LockStatusCommand:
        return LockStatusCommand(submission(envelope))

    register(descriptor(
        "governance.lock.acquire",
        mutating,
        any_scope,
        domain_atomic_required,
        decode_acquire,
        lambda command, _context: service.lock_file(
            name=command.name,
            owner=command.owner,
            lock_scope=command.lock_scope,
        ),
        receipt,
        available,
    ))

    def encode_release(result: FileUnlockResult) -> Mapping[str, JsonValue]:
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
            "receipt": encoded_receipt,
            "target": target,
        }

    register(descriptor(
        "governance.lock.release",
        mutating,
        any_scope,
        domain_atomic_required,
        decode_release,
        lambda command, _context: service.unlock_file(
            name=command.name,
            owner=command.owner,
        ),
        encode_release,
        available,
    ))

    def encode_status(items: Sequence[TargetState]) -> Mapping[str, JsonValue]:
        encoded = [
            {
                "target_id": item.target_id,
                "revision": item.revision,
                "state": item.state,
            }
            for item in items
        ]
        return {"items": cast(JsonValue, encoded)}

    register(descriptor(
        "governance.lock.status",
        read_only,
        any_scope,
        idempotency_read_only,
        decode_status,
        lambda _command, _context: service.list_active_locks(),
        encode_status,
        available,
    ))
