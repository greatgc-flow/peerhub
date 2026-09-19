"""Leadership and capability-matching command registration handlers."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from peerhub.application.capability_matching import (
    CapabilityMatchingCoordinator,
    encode_capability_ranking,
    encode_leadership_election_receipt,
)
from peerhub.application.commands.leadership import (
    DiscoverCandidatesCommand,
    ElectLeaderCommand,
    LeaderClaimCommand,
    LeaderYieldCommand,
)
from peerhub.application.leadership import (
    LeadershipClaimResult,
    LeadershipService,
    LeadershipYieldResult,
)
from peerhub.application.handlers._params import optional_text_or, required_text
from peerhub.core.protocol import CommandEnvelope, JsonValue


def register_leadership_handlers(
    *,
    api: Any,
    service: LeadershipService,
) -> None:
    """Register the existing leadership wire handlers unchanged."""

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
    any_scope = ScopeKind.ANY
    domain_atomic_required = IdempotencyPolicy.DOMAIN_ATOMIC_REQUIRED
    available = CommandAvailability.AVAILABLE

    def decode_claim(envelope: CommandEnvelope) -> LeaderClaimCommand:
        return LeaderClaimCommand(
            submission=submission(envelope),
            peer_node_id=required_text(envelope, "peer_node_id"),
            actor_id=required_text(envelope, "actor_id"),
            reason=optional_text_or(envelope, "reason", ""),
            domain=optional_text_or(envelope, "domain", ""),
        )

    def decode_yield(envelope: CommandEnvelope) -> LeaderYieldCommand:
        return LeaderYieldCommand(
            submission=submission(envelope),
            yielding_peer_id=required_text(envelope, "yielding_peer_id"),
            actor_id=required_text(envelope, "actor_id"),
            reason=optional_text_or(envelope, "reason", ""),
        )

    def encode_claim(
        result: LeadershipClaimResult,
    ) -> Mapping[str, JsonValue]:
        return {
            **receipt(result.submission),
            "disposition": result.disposition.value,
            "status": result.target.state.get("status"),
            "term": result.target.state.get("term"),
            "claim_id": result.target.state.get("claim_id"),
            "challenge_until": result.target.state.get("challenge_until"),
        }

    def encode_yield(
        result: LeadershipYieldResult,
    ) -> Mapping[str, JsonValue]:
        return {
            **receipt(result.submission),
            "owner_mismatch": result.owner_mismatch,
            "previous_leader_peer_node_id": (
                result.previous_leader_peer_node_id
            ),
        }

    register(descriptor(
        "routing.leadership.claim",
        mutating,
        any_scope,
        domain_atomic_required,
        decode_claim,
        lambda command, _context: service.claim_leadership(
            peer_node_id=command.peer_node_id,
            actor_id=command.actor_id,
            reason=command.reason,
            domain=command.domain,
        ),
        encode_claim,
        available,
    ))
    register(descriptor(
        "routing.leadership.yield",
        mutating,
        any_scope,
        domain_atomic_required,
        decode_yield,
        lambda command, _context: service.yield_leadership(
            yielding_peer_id=command.yielding_peer_id,
            actor_id=command.actor_id,
            reason=command.reason,
        ),
        encode_yield,
        available,
    ))


def register_capability_matching_handlers(
    *,
    api: Any,
    coordinator: CapabilityMatchingCoordinator,
) -> None:
    """Register the existing capability-matching wire handlers unchanged."""

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
    read_only = Mutability.READ_ONLY
    any_scope = ScopeKind.ANY
    domain_atomic_required = IdempotencyPolicy.DOMAIN_ATOMIC_REQUIRED
    idempotency_read_only = IdempotencyPolicy.READ_ONLY
    available = CommandAvailability.AVAILABLE

    def text(
        envelope: CommandEnvelope,
        name: str,
        default: str | None = None,
    ) -> str:
        value = envelope.params.get(name, default)
        if not isinstance(value, str):
            raise ValueError(f"{name} must be a string")
        return value

    def decode_discover(
        envelope: CommandEnvelope,
    ) -> DiscoverCandidatesCommand:
        return DiscoverCandidatesCommand(
            submission=submission(envelope),
            needs=text(envelope, "needs", ""),
            effort=text(envelope, "effort", "mid"),
        )

    def decode_elect(envelope: CommandEnvelope) -> ElectLeaderCommand:
        return ElectLeaderCommand(
            submission=submission(envelope),
            actor_id=text(envelope, "actor_id"),
            needs=text(envelope, "needs", "general"),
            effort=text(envelope, "effort", "mid"),
            reason=text(envelope, "reason", ""),
        )

    register(descriptor(
        "routing.candidate.discover",
        read_only,
        any_scope,
        idempotency_read_only,
        decode_discover,
        lambda command, _context: coordinator.discover(
            needs=command.needs,
            effort=command.effort,
        ),
        encode_capability_ranking,
        available,
    ))
    register(descriptor(
        "routing.leadership.elect",
        mutating,
        any_scope,
        domain_atomic_required,
        decode_elect,
        lambda command, _context: coordinator.elect_leader(
            needs=command.needs,
            effort=command.effort,
            reason=command.reason,
            actor_id=command.actor_id,
        ),
        encode_leadership_election_receipt,
        available,
    ))
