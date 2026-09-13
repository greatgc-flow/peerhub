"""Artifact-record command registration handlers."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from peerhub.application.commands.artifacts import (
    ArtifactClaimCommand,
    ArtifactFinalizeCommand,
    ArtifactStatusCommand,
)
from peerhub.core.ports import RequestContext
from peerhub.core.protocol import CommandEnvelope, JsonValue
from peerhub.governance.artifact_records import (
    ArtifactMutationResult,
    ArtifactRecordService,
    ArtifactStatusResult,
)


def register_artifact_handlers(
    *,
    api: Any,
    service: ArtifactRecordService,
) -> None:
    """Register the existing artifact-record wire handlers unchanged."""

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

    def optional_text(
        envelope: CommandEnvelope,
        name: str,
    ) -> str | None:
        value = envelope.params.get(name)
        return None if value is None or value == "" else str(value)

    def decode_claim(envelope: CommandEnvelope) -> ArtifactClaimCommand:
        return ArtifactClaimCommand(
            submission=submission(envelope),
            name=str(envelope.params.get("name", "")),
            owner=str(envelope.params.get("owner", "")),
        )

    def decode_status(envelope: CommandEnvelope) -> ArtifactStatusCommand:
        return ArtifactStatusCommand(
            submission=submission(envelope),
            name=optional_text(envelope, "name"),
            peer=optional_text(envelope, "peer"),
            draft_path=optional_text(envelope, "draft_path"),
        )

    def decode_finalize(
        envelope: CommandEnvelope,
    ) -> ArtifactFinalizeCommand:
        return ArtifactFinalizeCommand(
            submission=submission(envelope),
            name=str(envelope.params.get("name", "")),
            file_path=str(envelope.params.get("file_path", "")),
        )

    def is_draft_registration(envelope: CommandEnvelope) -> bool:
        return all(
            isinstance(envelope.params.get(name), str)
            and bool(envelope.params.get(name))
            for name in ("name", "peer", "draft_path")
        )

    def status_mutability(envelope: CommandEnvelope) -> Mutability:
        if is_draft_registration(envelope):
            return mutating
        return read_only

    def status_idempotency(
        envelope: CommandEnvelope,
    ) -> IdempotencyPolicy:
        if is_draft_registration(envelope):
            return domain_atomic_required
        return idempotency_read_only

    def handle_status(
        command: ArtifactStatusCommand,
        _context: RequestContext,
    ) -> ArtifactMutationResult | ArtifactStatusResult:
        if (
            command.name is not None
            and command.peer is not None
            and command.draft_path is not None
        ):
            return service.register_draft(
                command.name,
                peer=command.peer,
                draft_path=command.draft_path,
            )
        return service.status(command.name)

    def encode_mutation(
        result: ArtifactMutationResult,
    ) -> Mapping[str, JsonValue]:
        return {
            "receipt": dict(receipt(result.submission)),
            "artifact": result.record.state,
        }

    def encode_status(
        result: ArtifactMutationResult | ArtifactStatusResult,
    ) -> Mapping[str, JsonValue]:
        if isinstance(result, ArtifactMutationResult):
            return encode_mutation(result)
        if result.single:
            artifact: JsonValue = (
                {} if not result.items else result.items[0].state
            )
            return {"artifact": artifact}
        items: tuple[JsonValue, ...] = tuple(
            target.state for target in result.items
        )
        return {"items": items}

    register(descriptor(
        "governance.artifact.claim",
        mutating,
        any_scope,
        domain_atomic_required,
        decode_claim,
        lambda command, _context: service.claim(
            command.name,
            command.owner,
        ),
        encode_mutation,
        available,
    ))
    register(descriptor(
        "governance.artifact.status",
        status_mutability,
        any_scope,
        status_idempotency,
        decode_status,
        handle_status,
        encode_status,
        available,
    ))
    register(descriptor(
        "governance.artifact.finalize",
        mutating,
        any_scope,
        domain_atomic_required,
        decode_finalize,
        lambda command, _context: service.finalize(
            command.name,
            command.file_path,
        ),
        encode_mutation,
        available,
    ))
