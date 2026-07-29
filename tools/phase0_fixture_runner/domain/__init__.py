"""Domain-oracle verification for Phase 0 controlled-fake fixtures."""

from .authority_filesystem import (
    authority_filesystem_registrations,
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
    )
)

__all__ = [
    "DOMAIN_REGISTRY",
    "DomainContractError",
    "DomainRunResult",
    "IsolatedDomainContext",
    "write_domain_artifacts",
]