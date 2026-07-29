"""Domain-oracle verification for Phase 0 controlled-fake fixtures."""

from .authority_composed_cutover import (
    authority_composed_cutover_registrations,
)
from .authority_drain import authority_drain_registrations
from .authority_external_effect import (
    authority_external_effect_registrations,
)
from .authority_fence import authority_fence_registrations
from .authority_filesystem import (
    authority_filesystem_registrations,
)
from .authority_identity import (
    authority_identity_registrations,
)
from .authority_json_custody import (
    authority_json_custody_registrations,
)
from .authority_quota import authority_quota_registrations
from .authority_recovery import (
    authority_recovery_registrations,
)
from .authority_shadow import authority_shadow_registrations
from .broker import broker_registrations
from .command_authz import command_authz_registrations
from .consensus import consensus_registrations
from .contract import (
    DomainContractError,
    DomainRunResult,
    IsolatedDomainContext,
    StaticDomainRegistry,
    write_domain_artifacts,
)
from .coordination_room import coordination_room_registrations
from .dispatch_pipe import dispatch_pipe_registrations
from .health import health_registrations
from .process_lifecycle import process_lifecycle_registrations
from .routing import routing_registrations
from .routing_discovery import routing_discovery_registrations
from .transport import transport_registrations

DOMAIN_REGISTRY = StaticDomainRegistry(
    (
        *dispatch_pipe_registrations(),
        *transport_registrations(),
        *health_registrations(),
        *process_lifecycle_registrations(),
        *routing_registrations(),
        *routing_discovery_registrations(),
        *broker_registrations(),
        *command_authz_registrations(),
        *coordination_room_registrations(),
        *consensus_registrations(),
        *authority_filesystem_registrations(),
        *authority_identity_registrations(),
        *authority_shadow_registrations(),
        *authority_fence_registrations(),
        *authority_json_custody_registrations(),
        *authority_external_effect_registrations(),
        *authority_recovery_registrations(),
        *authority_drain_registrations(),
        *authority_quota_registrations(),
        *authority_composed_cutover_registrations(),
    )
)

__all__ = [
    "DOMAIN_REGISTRY",
    "DomainContractError",
    "DomainRunResult",
    "IsolatedDomainContext",
    "write_domain_artifacts",
]