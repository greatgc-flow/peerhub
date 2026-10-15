"""Strict, privacy-preserving projection of provider-native token usage."""
from __future__ import annotations

import json
from typing import Any, cast

_TELEMETRY_MAX_BYTES = 1_048_576
_TELEMETRY_MAX_EVENT_BYTES = 262_144


class _DuplicateJsonKey(ValueError):
    pass


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise _DuplicateJsonKey(key)
        value[key] = item
    return value


def unknown_usage(reason: str) -> dict[str, Any]:
    return {"schema_version": 1, "scope": None, "source": None, "reported": {}, "normalized": {},
            "coverage": {"state": "UNKNOWN", "reason": reason}}


def parse_provider_usage(kind: str, stdout: str) -> dict[str, Any]:
    """Project only provider-native token counters; never retain event text or opaque IDs."""
    if kind not in ("cc", "cx", "ag"):
        return unknown_usage("unsupported_provider")
    try:
        if len(stdout.encode("utf-8")) > _TELEMETRY_MAX_BYTES:
            return unknown_usage("stream_too_large")
    except UnicodeEncodeError:
        return unknown_usage("invalid_encoding")

    target = {"cc": "result", "cx": "turn.completed", "ag": "result"}[kind]
    if kind == "ag":
        try:
            raw: object = json.loads(stdout.strip(), object_pairs_hook=_unique_json_object)
        except _DuplicateJsonKey:
            return unknown_usage("duplicate_json_key")
        except (ValueError, RecursionError):
            return unknown_usage("malformed_output")
        if not isinstance(raw, dict):
            return unknown_usage("invalid_output")
        events = [cast("dict[str, Any]", raw)]
    else:
        events: list[dict[str, Any]] = []
        for line in stdout.split("\n"):
            if not line.strip():
                continue
            if len(line.encode("utf-8")) > _TELEMETRY_MAX_EVENT_BYTES:
                return unknown_usage("event_too_large")
            try:
                raw = json.loads(line, object_pairs_hook=_unique_json_object)
            except _DuplicateJsonKey:
                return unknown_usage("duplicate_json_key")
            except (ValueError, RecursionError):
                return unknown_usage("malformed_output")
            if not isinstance(raw, dict):
                return unknown_usage("invalid_event")
            events.append(cast("dict[str, Any]", raw))

    allowed_events: set[str]
    if kind == "cc":
        allowed_events = {"system", "assistant", "user", "result", "error", "stream_event"}
    elif kind == "cx":
        allowed_events = {"thread.started", "turn.started", "turn.completed", "turn.failed", "error",
                          "item.started", "item.updated", "item.completed"}
    else:
        allowed_events = set()
    if kind != "ag" and any(not isinstance(event.get("type"), str) or event.get("type") not in allowed_events
                             for event in events):
        return unknown_usage("unknown_event")

    matches = ([e for e in events if e.get("type") == target] if kind != "ag" else
               [e for e in events if isinstance(e.get("response"), str)])
    if len(matches) != 1:
        return unknown_usage("missing_terminal" if not matches else "duplicate_terminal")
    envelope = matches[0]
    usage = envelope.get("usage")
    scope = "session_cumulative"
    source = {"cc": "cc.result.usage", "cx": "cx.turn.completed.usage", "ag": "ag.result.usage"}[kind]
    if usage is None or usage == {}:
        return {"schema_version": 1, "scope": scope, "source": source, "reported": {}, "normalized": {},
                "coverage": {"state": "ABSENT", "reason": "usage_absent"}}
    if not isinstance(usage, dict):
        return unknown_usage("invalid_usage")

    fields = {
        "cc": ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"),
        "cx": ("input_tokens", "cached_input_tokens", "cache_write_input_tokens", "output_tokens", "reasoning_output_tokens"),
        "ag": ("input_tokens", "output_tokens", "thinking_tokens", "cache_read_tokens", "total_tokens"),
    }[kind]
    raw_usage = cast("dict[str, Any]", usage)
    reported: dict[str, int] = {}
    for key in fields:
        if key not in raw_usage:
            continue
        value = raw_usage[key]
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            return unknown_usage("invalid_usage")
        reported[key] = value
    if not reported:
        return {"schema_version": 1, "scope": scope, "source": source, "reported": {}, "normalized": {},
                "coverage": {"state": "UNSUPPORTED", "reason": "no_allowlisted_counters"}}
    normalized: dict[str, int] = {}
    if kind == "cc":
        if all(key in reported for key in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")):
            normalized["input_tokens"] = sum(reported[key] for key in
                                              ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"))
        if "cache_read_input_tokens" in reported:
            normalized["cached_input_tokens"] = reported["cache_read_input_tokens"]
        if "cache_creation_input_tokens" in reported:
            normalized["cache_write_tokens"] = reported["cache_creation_input_tokens"]
        if "output_tokens" in reported:
            normalized["output_tokens"] = reported["output_tokens"]
    elif kind == "cx":
        for source_key, target_key in (("input_tokens", "input_tokens"), ("cached_input_tokens", "cached_input_tokens"),
                                       ("cache_write_input_tokens", "cache_write_tokens"), ("output_tokens", "output_tokens")):
            if source_key in reported:
                normalized[target_key] = reported[source_key]
    else:
        for source_key, target_key in (("input_tokens", "input_tokens"), ("cache_read_tokens", "cached_input_tokens"),
                                       ("output_tokens", "output_tokens")):
            if source_key in reported:
                normalized[target_key] = reported[source_key]
    return {"schema_version": 1, "scope": scope, "source": source, "reported": reported, "normalized": normalized,
            "coverage": {"state": "MEASURED", "reason": None}}


