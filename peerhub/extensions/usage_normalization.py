"""Canonical usage mappings shared by provider parsing and fenced receipt validation."""
from __future__ import annotations

from typing import Any


REPORTED_FIELDS: dict[str, set[str]] = {
    "cc": {"input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"},
    "cx": {"input_tokens", "cached_input_tokens", "cache_write_input_tokens", "output_tokens", "reasoning_output_tokens"},
    "ag": {"input_tokens", "output_tokens", "thinking_tokens", "cache_read_tokens", "total_tokens"},
}
NORMALIZED_FIELDS = {
    "provider_input_tokens", "uncached_input_tokens", "cached_input_tokens", "cache_read_input_tokens",
    "cache_creation_input_tokens", "cache_write_tokens", "output_tokens", "reasoning_output_tokens",
    "thinking_tokens", "provider_total_tokens",
}


def normalize_provider_usage(provider_kind: str, reported: dict[str, Any]) -> dict[str, int]:
    """Derive only meanings supported by the source field contract; retain native counters separately."""
    normalized: dict[str, int] = {}
    if provider_kind == "cc":
        if "input_tokens" in reported:
            # Anthropic reports uncached input separately from cache reads/writes.
            normalized["uncached_input_tokens"] = reported["input_tokens"]
        if all(key in reported for key in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")):
            normalized["provider_input_tokens"] = sum(
                reported[key] for key in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"))
        for source, target in (
            ("cache_read_input_tokens", "cache_read_input_tokens"),
            ("cache_creation_input_tokens", "cache_creation_input_tokens"),
            ("output_tokens", "output_tokens"),
        ):
            if source in reported:
                normalized[target] = reported[source]
    elif provider_kind == "cx":
        if "input_tokens" in reported:
            normalized["provider_input_tokens"] = reported["input_tokens"]
        for source, target in (
            ("cached_input_tokens", "cached_input_tokens"),
            ("cache_write_input_tokens", "cache_write_tokens"),
            ("output_tokens", "output_tokens"),
            ("reasoning_output_tokens", "reasoning_output_tokens"),
        ):
            if source in reported:
                normalized[target] = reported[source]
    elif provider_kind == "ag":
        if "input_tokens" in reported:
            normalized["provider_input_tokens"] = reported["input_tokens"]
        for source, target in (
            ("cache_read_tokens", "cached_input_tokens"),
            ("output_tokens", "output_tokens"),
            ("thinking_tokens", "thinking_tokens"),
            ("total_tokens", "provider_total_tokens"),
        ):
            if source in reported:
                normalized[target] = reported[source]
    return normalized
