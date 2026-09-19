"""Shared command-parameter extraction/validation helpers for handlers.

Distinct from ``peerhub.core.protocol.require_text``: that function validates
an already-extracted string value (non-empty after strip, NFC-normalized).
These helpers extract a raw value from a ``CommandEnvelope``/params mapping
and only assert its JSON type, matching the pre-existing local behavior of
the handler modules this was consolidated from (alerts, consensus, duty,
feedback, leadership, lessons, operational_errors, peers, roles, tasks).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import cast

from peerhub.core.protocol import CommandEnvelope, JsonValue


def required_text(envelope: CommandEnvelope, name: str) -> str:
    value = envelope.params[name]
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string")
    return value


def optional_text(envelope: CommandEnvelope, name: str) -> str | None:
    """Extract an optional string param; absence (or an explicit null) returns
    None (feedback.py/peers.py/roles.py's original shape)."""
    value = envelope.params.get(name)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string or null")
    return value


def optional_text_or(envelope: CommandEnvelope, name: str, default: str) -> str:
    """Extract an optional string param, substituting `default` if absent
    (duty.py/leadership.py's original shape -- absence is never None here)."""
    value = envelope.params.get(name, default)
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string")
    return value


def string_tuple(params: Mapping[str, JsonValue], name: str) -> tuple[str, ...]:
    value = params[name]
    if not isinstance(value, (list, tuple)) or not all(
        isinstance(item, str) for item in value
    ):
        raise ValueError(f"{name} must be a sequence of strings")
    return tuple(cast(str, item) for item in value)
