"""Narrow submission protocol and context types."""

from dataclasses import dataclass


@dataclass(frozen=True)
class RequestContext:
    """Caller identity and context for API submissions."""

    principal: str
    client_id: str
