"""Peer-registry, health, and lease-status command registration handlers."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, cast

from peerhub.application.commands.health import (
    CheckGateCommand,
    HealthCheckCommand,
    HealthPrecheckCommand,
    HealthSweepCommand,
    PeerQuarantineCommand,
    PeerRecoverCommand,
)
from peerhub.application.commands.leases import LeaseStatusCommand
from peerhub.application.commands.peers import (
    BindProfileCommand,
    ListNodesCommand,
    ModelStatusCommand,
    PeerStatusCommand,
    RegisterNodeCommand,
)
from peerhub.application.health_revalidation import (
    HealthRevalidationCoordinator,
    collect_check_gate,
    collect_health_check,
    collect_health_precheck,
    collect_health_sweep,
    execute_peer_quarantine,
    execute_peer_recover,
)
from peerhub.application.lease_status import collect_lease_status
from peerhub.application.peer_registry import (
    PeerRegistryService,
    collect_model_status,
    collect_peer_status,
)
from peerhub.application.handlers._params import optional_text, required_text
from peerhub.core.identity import AuthenticatedSubject
from peerhub.core.protocol import CommandEnvelope, JsonValue
from peerhub.dispatch.service import DispatchService
from peerhub.health.service import HealthService


def register_peer_registry_handlers(
    *,
    api: Any,
    service: PeerRegistryService,
    health: HealthService | None,
    health_revalidation: HealthRevalidationCoordinator | None = None,
    dispatch: DispatchService,
) -> None:
    """Register the existing peer-registry and health wire handlers unchanged."""

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

    def decode_register(envelope: CommandEnvelope) -> RegisterNodeCommand:
        params = envelope.params
        node_id = params["node_id"]
        peer_kind = params["peer_kind"]
        node_type = params.get("node_type", "agent")
        tier = params.get("tier", 4)
        actor_id = params["actor_id"]
        if not isinstance(node_id, str) or not isinstance(peer_kind, str):
            raise ValueError("node_id and peer_kind must be strings")
        if not isinstance(node_type, str):
            raise ValueError("node_type must be a string")
        if not isinstance(tier, int) or isinstance(tier, bool):
            raise ValueError("tier must be an integer")
        if not isinstance(actor_id, str):
            raise ValueError("actor_id must be a string")
        return RegisterNodeCommand(
            submission(envelope),
            node_id,
            peer_kind,
            optional_text(envelope, "profile_id"),
            tier,
            node_type,
            actor_id,
        )

    def decode_list(envelope: CommandEnvelope) -> ListNodesCommand:
        return ListNodesCommand(submission(envelope))

    def decode_bind(envelope: CommandEnvelope) -> BindProfileCommand:
        return BindProfileCommand(
            submission=submission(envelope),
            node_id=required_text(envelope, "node_id"),
            profile_id=required_text(envelope, "profile_id"),
            model_id=required_text(envelope, "model_id"),
            reasoning_effort=optional_text(envelope, "reasoning_effort"),
            actor_id=required_text(envelope, "actor_id"),
        )

    def decode_model_status(
        envelope: CommandEnvelope,
    ) -> ModelStatusCommand:
        return ModelStatusCommand(submission(envelope))

    def encode_nodes(results: Sequence[Any]) -> Mapping[str, JsonValue]:
        nodes = [
            {
                "target_id": result.target_id,
                "revision": result.revision,
                "state": result.state,
            }
            for result in results
        ]
        return {"nodes": cast(JsonValue, nodes)}

    def encode_models(
        results: Sequence[Mapping[str, JsonValue]],
    ) -> Mapping[str, JsonValue]:
        return {"models": cast(JsonValue, list(results))}

    register(descriptor(
        "configuration.instance.register",
        mutating,
        any_scope,
        domain_atomic_required,
        decode_register,
        lambda command, _context: service.register_node(
            node_id=command.node_id,
            peer_kind=command.peer_kind,
            profile_id=command.profile_id,
            tier=command.tier,
            node_type=command.node_type,
            actor_id=command.actor_id,
        ),
        receipt,
        available,
    ))
    register(descriptor(
        "configuration.instance.list",
        read_only,
        any_scope,
        idempotency_read_only,
        decode_list,
        lambda _command, _context: service.list_nodes(),
        encode_nodes,
        available,
    ))
    register(descriptor(
        "configuration.profile.bind",
        mutating,
        any_scope,
        domain_atomic_required,
        decode_bind,
        lambda command, _context: service.bind_profile(
            node_id=command.node_id,
            profile_id=command.profile_id,
            model_id=command.model_id,
            reasoning_effort=command.reasoning_effort,
            actor_id=command.actor_id,
        ),
        receipt,
        available,
    ))
    register(descriptor(
        "configuration.model.status",
        read_only,
        any_scope,
        idempotency_read_only,
        decode_model_status,
        lambda _command, _context: collect_model_status(service, health),
        encode_models,
        available,
    ))

    def decode_peer_status(envelope: CommandEnvelope) -> PeerStatusCommand:
        node_id = envelope.params.get("node_id")
        include_all = envelope.params.get("include_all", False)
        return PeerStatusCommand(
            submission=submission(envelope),
            node_id=str(node_id) if node_id is not None else None,
            include_all=bool(include_all),
        )

    def encode_peer_status(
        results: Sequence[Mapping[str, JsonValue]],
    ) -> Mapping[str, JsonValue]:
        return {"peers": cast(JsonValue, list(results))}

    register(descriptor(
        "configuration.peer.status",
        read_only,
        any_scope,
        idempotency_read_only,
        decode_peer_status,
        lambda command, _context: collect_peer_status(
            service,
            health,
            node_id=command.node_id,
            include_all=command.include_all,
        ),
        encode_peer_status,
        available,
    ))

    def decode_health_check(
        envelope: CommandEnvelope,
    ) -> HealthCheckCommand:
        peer = envelope.params.get("peer")
        recover = envelope.params.get("recover", False)
        return HealthCheckCommand(
            submission=submission(envelope),
            peer=str(peer) if peer is not None else None,
            recover=bool(recover),
        )

    register(descriptor(
        "health.check",
        mutating,
        any_scope,
        domain_atomic_required,
        decode_health_check,
        lambda command, context: collect_health_check(
            service,
            health,
            health_revalidation,
            AuthenticatedSubject(context.principal, "system"),
            peer=command.peer,
            recover=command.recover,
        ),
        lambda result: result,
        available,
    ))

    def decode_peer_quarantine(
        envelope: CommandEnvelope,
    ) -> PeerQuarantineCommand:
        peer_id = (
            envelope.params.get("peer_id")
            or envelope.params.get("peer")
            or ""
        )
        reason = envelope.params.get("reason", "manual")
        actor_id = (
            envelope.params.get("actor_id")
            or envelope.params.get("actor")
        )
        return PeerQuarantineCommand(
            submission=submission(envelope),
            peer_id=str(peer_id),
            reason=str(reason),
            actor_id=str(actor_id) if actor_id is not None else None,
        )

    register(descriptor(
        "health.admission.quarantine",
        mutating,
        any_scope,
        domain_atomic_required,
        decode_peer_quarantine,
        lambda command, context: (
            execute_peer_quarantine(
                service,
                health,
                peer_id=command.peer_id,
                reason=command.reason,
                actor_id=command.actor_id or context.principal,
            )
            if health is not None
            else {
                "peer": command.peer_id,
                "quarantined": False,
                "error": "Health service not configured",
            }
        ),
        lambda result: result,
        available,
    ))

    def decode_peer_recover(
        envelope: CommandEnvelope,
    ) -> PeerRecoverCommand:
        peer_id = envelope.params.get("peer_id", "all")
        reason = envelope.params.get("reason", "manual")
        return PeerRecoverCommand(
            submission=submission(envelope),
            peer_id=str(peer_id),
            reason=str(reason),
        )

    register(descriptor(
        "health.peer.recover",
        mutating,
        any_scope,
        domain_atomic_required,
        decode_peer_recover,
        lambda command, context: (
            execute_peer_recover(
                service,
                health_revalidation,
                AuthenticatedSubject(context.principal, "system"),
                peer_id=command.peer_id,
                reason=command.reason,
            )
            if health_revalidation is not None
            else {"results": ()}
        ),
        lambda result: result,
        available,
    ))

    def decode_health_precheck(
        envelope: CommandEnvelope,
    ) -> HealthPrecheckCommand:
        peers = envelope.params.get("peers")
        needs = envelope.params.get("needs")
        return HealthPrecheckCommand(
            submission=submission(envelope),
            peers=str(peers) if peers is not None else None,
            needs=str(needs) if needs is not None else None,
        )

    register(descriptor(
        "health.precheck",
        read_only,
        any_scope,
        idempotency_read_only,
        decode_health_precheck,
        lambda command, _context: collect_health_precheck(
            service,
            health,
            peers=command.peers,
            needs=command.needs,
        ),
        lambda result: result,
        available,
    ))

    def decode_check_gate(envelope: CommandEnvelope) -> CheckGateCommand:
        agent = envelope.params.get("agent", "")
        return CheckGateCommand(
            submission=submission(envelope),
            agent=str(agent),
        )

    register(descriptor(
        "health.gate.check",
        read_only,
        any_scope,
        idempotency_read_only,
        decode_check_gate,
        lambda command, _context: collect_check_gate(
            service,
            health,
            agent=command.agent,
        ),
        lambda result: result,
        available,
    ))

    def decode_health_sweep(
        envelope: CommandEnvelope,
    ) -> HealthSweepCommand:
        return HealthSweepCommand(submission=submission(envelope))

    register(descriptor(
        "health.sweep",
        read_only,
        any_scope,
        idempotency_read_only,
        decode_health_sweep,
        lambda _command, _context: collect_health_sweep(service, health),
        lambda result: result,
        available,
    ))

    def decode_lease_status(
        envelope: CommandEnvelope,
    ) -> LeaseStatusCommand:
        return LeaseStatusCommand(submission=submission(envelope))

    def encode_lease_status(
        rows: Sequence[Mapping[str, JsonValue]],
    ) -> Mapping[str, JsonValue]:
        return {"leases": tuple(rows)}

    register(descriptor(
        "dispatch.lease.status",
        read_only,
        any_scope,
        idempotency_read_only,
        decode_lease_status,
        lambda _command, _context: collect_lease_status(
            dispatch,
            now=dispatch.current_time(),
        ),
        encode_lease_status,
        available,
    ))
