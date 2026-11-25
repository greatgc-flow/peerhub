"""Canonical usage mappings shared by provider parsing and fenced receipt validation."""
from __future__ import annotations

import re
from typing import Any, cast


PROVIDER_USAGE_CONTRACT = {
    "cc": ("session_cumulative", "cc.result.usage"),
    "cx": ("session_cumulative", "cx.turn.completed.usage"),
    "ag": ("session_cumulative", "ag.result.usage"),
    "ca": ("delivery", "ca.message.usage"),  # the API peers (#378): one call per ask
    "oa": ("delivery", "oa.response.usage"),
    "ga": ("delivery", "ga.response.usage"),
}
_SAFE_COVERAGE_REASON = re.compile(r"[a-z_]{1,64}\Z")

REPORTED_FIELDS: dict[str, set[str]] = {
    "cc": {"input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"},
    "cx": {"input_tokens", "cached_input_tokens", "cache_write_input_tokens", "output_tokens", "reasoning_output_tokens"},
    "ag": {"input_tokens", "output_tokens", "thinking_tokens", "cache_read_tokens", "total_tokens"},
    "ca": {"input_tokens", "output_tokens"},  # the API peers (#378) send no cached context
    "oa": {"input_tokens", "output_tokens", "total_tokens"},
    "ga": {"input_tokens", "output_tokens"},  # #381: the output is the candidate tokens plus the thinking tokens
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
    elif provider_kind in ("ag", "ca", "oa", "ga"):
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


def unknown_usage(reason: str, *, receipt: bool = False) -> dict[str, Any]:
    """Construct unknown usage in the adapter candidate or persisted receipt format."""
    return {"schema_version": 1, "scope": None, "source": None,
            **({"attribution": "unattributed"} if receipt else {}),
            "reported": {}, "normalized": {}, **({"field_evidence": {}} if receipt else {}),
            "coverage": {"state": "UNKNOWN", "reason": reason}}


def validate_provider_usage(candidate: object, provider_kind: str) -> dict[str, Any]:
    """Validate the adapter candidate again at the fenced persistence boundary."""
    if candidate is None:
        return unknown_usage("not_captured", receipt=True)
    if not isinstance(candidate, dict):
        return unknown_usage("invalid_candidate_shape", receipt=True)
    candidate = cast("dict[str, Any]", candidate)
    if set(candidate) != {"schema_version", "scope", "source", "reported", "normalized", "coverage"}:
        return unknown_usage("invalid_candidate_shape", receipt=True)
    schema_version = candidate.get("schema_version")
    if (provider_kind not in PROVIDER_USAGE_CONTRACT or isinstance(schema_version, bool)
            or schema_version != 1):
        return unknown_usage("unsupported_provider_schema", receipt=True)
    expected_scope, expected_source = PROVIDER_USAGE_CONTRACT[provider_kind]
    scope, source = candidate.get("scope"), candidate.get("source")
    reported, normalized, coverage = candidate.get("reported"), candidate.get("normalized"), candidate.get("coverage")
    if scope is None and source is None:
        if reported != {} or normalized != {} or not isinstance(coverage, dict):
            return unknown_usage("invalid_unknown_candidate", receipt=True)
        coverage = cast("dict[str, Any]", coverage)
        if coverage.get("state") != "UNKNOWN":
            return unknown_usage("invalid_unknown_candidate", receipt=True)
        reason = coverage.get("reason")
        if not isinstance(reason, str) or _SAFE_COVERAGE_REASON.fullmatch(reason) is None:
            return unknown_usage("invalid_coverage_reason", receipt=True)
        return unknown_usage(reason, receipt=True)
    if scope != expected_scope or source != expected_source:
        return unknown_usage("provider_scope_mismatch", receipt=True)
    if not isinstance(reported, dict) or not isinstance(normalized, dict) or not isinstance(coverage, dict):
        return unknown_usage("invalid_candidate_shape", receipt=True)
    reported = cast("dict[str, Any]", reported)
    normalized = cast("dict[str, Any]", normalized)
    coverage = cast("dict[str, Any]", coverage)
    if set(reported) - REPORTED_FIELDS[provider_kind] or set(normalized) - NORMALIZED_FIELDS:
        return unknown_usage("unsupported_usage_field", receipt=True)
    for values in (reported, normalized):
        if any(isinstance(v, bool) or not isinstance(v, int) or v < 0 or v > 2**63 - 1 for v in values.values()):
            return unknown_usage("invalid_usage_counter", receipt=True)
    expected_normalized = normalize_provider_usage(provider_kind, reported)
    if normalized != expected_normalized:
        return unknown_usage("normalization_mismatch", receipt=True)
    state, reason = coverage.get("state"), coverage.get("reason")
    if state not in ("MEASURED", "ABSENT", "UNSUPPORTED", "UNKNOWN"):
        return unknown_usage("invalid_coverage_state", receipt=True)
    if reason is not None and (not isinstance(reason, str) or _SAFE_COVERAGE_REASON.fullmatch(reason) is None):
        return unknown_usage("invalid_coverage_reason", receipt=True)
    if state == "MEASURED" and (not reported or reason is not None):
        return unknown_usage("incomplete_measured_candidate", receipt=True)
    if state != "MEASURED" and (reported or normalized):
        return unknown_usage("non_measured_candidate_has_counters", receipt=True)
    attribution = "delivery" if scope == "delivery" else "unattributed"
    return {"schema_version": 1, "scope": scope, "source": source, "attribution": attribution,
            "reported": dict(reported), "normalized": dict(normalized), "field_evidence": {},
            "coverage": {"state": state, "reason": reason}}
