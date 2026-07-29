"""Domain-oracle verification for Phase 0 controlled-fake fixtures."""

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
    )
)

__all__ = [
    "DOMAIN_REGISTRY",
    "DomainContractError",
    "DomainRunResult",
    "IsolatedDomainContext",
    "write_domain_artifacts",
]