"""Domain-oracle verification for Phase 0 controlled-fake fixtures."""

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
from .broker import broker_registrations
from .command_authz import command_authz_registrations
from .contract import (
    DomainContractError,
    DomainRunResult,
    IsolatedDomainContext,
    StaticDomainRegistry,
    write_domain_artifacts,
)
from .health import health_registrations
from .routing import routing_registrations
from .transport import transport_registrations

DOMAIN_REGISTRY = StaticDomainRegistry(
    (
        *transport_registrations(),
        *health_registrations(),
        *routing_registrations(),
        *broker_registrations(),
        *command_authz_registrations(),
        *authority_filesystem_registrations(),
        *authority_identity_registrations(),
        *authority_fence_registrations(),
        *authority_json_custody_registrations(),
    )
)

__all__ = [
    "DOMAIN_REGISTRY",
    "DomainContractError",
    "DomainRunResult",
    "IsolatedDomainContext",
    "write_domain_artifacts",
]