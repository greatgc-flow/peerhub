"""Privacy-preserving parser for Antigravity's documented stream-json output."""
from __future__ import annotations

import json
import math
from typing import Any, cast

MAX_STREAM_BYTES = 1_048_576
MAX_EVENT_BYTES = 262_144
MAX_STEPS = 512
USAGE_FIELDS = ("input_tokens", "output_tokens", "thinking_tokens", "cache_read_tokens", "total_tokens")
STEP_TYPES = frozenset(("user_input", "agent_response", "tool", "checkpoint"))
STATES = frozenset(("ACTIVE", "DONE"))
RESULT_STATUSES = frozenset(("SUCCESS", "ERROR", "CANCELED", "INTERRUPTED", "INVALID"))


class _DuplicateJsonKey(ValueError):
    pass


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJsonKey(key)
        result[key] = value
    return result


def _unknown(reason: str) -> dict[str, Any]:
    return {"complete": False, "reason": reason, "result": None, "steps": []}


def _mapping(value: object) -> dict[str, Any] | None:
    return cast(dict[str, Any], value) if isinstance(value, dict) else None


def _conversation_id(event: dict[str, Any]) -> str | None:
    """Read IDs only for in-memory event correlation; callers never receive them."""
    if event.get("event") == "init":
        value = event.get("conversation_id")
    elif event.get("event") == "step_update":
        update = _mapping(event.get("step_update"))
        value = update.get("conversation_id") if update is not None else None
    elif event.get("event") == "result":
        result = _mapping(event.get("result"))
        value = result.get("conversation_id") if result is not None else None
    else:
        value = None
    if not isinstance(value, str) or not value or len(value) > 512 or any(ord(ch) < 32 for ch in value):
        return None
    return value


def _number(value: object) -> int | float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        return None
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def _usage(value: object) -> dict[str, int] | None | bool:
    """Return safe counters, None for absent/empty usage, False for malformed known fields."""
    if value is None:
        return None
    mapping = _mapping(value)
    if mapping is None:
        return False
    counters: dict[str, int] = {}
    for key in USAGE_FIELDS:
        if key not in mapping:
            continue
        count = mapping[key]
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            return False
        counters[key] = count
    return counters or None


def parse_ag_stream_json(text: str) -> dict[str, Any]:
    """Return only bounded numeric facts; malformed, ambiguous or incomplete streams fail closed.

    Raw event text, response fragments, tools, paths and vendor conversation IDs are never returned.
    """
    try:
        total_bytes = len(text.encode("utf-8"))
    except UnicodeEncodeError:
        return _unknown("invalid_encoding")
    if total_bytes > MAX_STREAM_BYTES:
        return _unknown("stream_too_large")

    events: list[dict[str, Any]] = []
    for line in text.split("\n"):
        if not line.strip():
            continue
        if len(line.encode("utf-8")) > MAX_EVENT_BYTES:
            return _unknown("event_too_large")
        try:
            value: object = json.loads(line, object_pairs_hook=_unique_object)
        except _DuplicateJsonKey:
            return _unknown("duplicate_json_key")
        except (ValueError, RecursionError):
            return _unknown("malformed_json")
        if not isinstance(value, dict):
            return _unknown("invalid_event")
        events.append(cast(dict[str, Any], value))

    if not events or events[0].get("event") != "init":
        return _unknown("missing_init")
    conversation_id = _conversation_id(events[0])
    if conversation_id is None:
        return _unknown("invalid_session_identity")

    steps_by_index: dict[int, dict[str, Any]] = {}
    active_steps: set[int] = set()
    observed_step_types: dict[int, str] = {}
    completed_steps: set[int] = set()
    last_index = -1
    result_facts: dict[str, Any] | None = None
    for event in events[1:]:
        event_type = event.get("event")
        if event_type not in ("step_update", "result"):
            return _unknown("unknown_event")
        if result_facts is not None:
            return _unknown("event_after_result")
        if _conversation_id(event) != conversation_id:
            return _unknown("session_mismatch")

        if event_type == "step_update":
            update = _mapping(event.get("step_update"))
            if update is None:
                return _unknown("invalid_step")
            index, state, step_type = update.get("step_index"), update.get("state"), update.get("step_type")
            if isinstance(index, bool) or not isinstance(index, int) or index < 0:
                return _unknown("invalid_step_index")
            if not isinstance(state, str) or state not in STATES:
                return _unknown("invalid_state")
            if not isinstance(step_type, str) or step_type not in STEP_TYPES:
                return _unknown("unknown_step_type")
            if index < last_index:
                return _unknown("step_order")
            last_index = index
            if index not in observed_step_types and len(observed_step_types) >= MAX_STEPS:
                return _unknown("step_limit")
            prior_type = observed_step_types.get(index)
            if prior_type is not None and prior_type != step_type:
                return _unknown("conflicting_step_type")
            observed_step_types[index] = step_type
            duration = update.get("duration_seconds")
            safe_duration = _number(duration) if duration is not None else None
            if duration is not None and safe_duration is None:
                return _unknown("invalid_duration")
            safe_usage = _usage(update.get("usage"))
            if safe_usage is False:
                return _unknown("invalid_usage")
            if state == "ACTIVE":
                if index in completed_steps:
                    return _unknown("step_state_regression")
                active_steps.add(index)
                continue
            fact: dict[str, Any] = {"step_index": index, "step_type": step_type}
            if safe_duration is not None:
                fact["duration_seconds"] = safe_duration
            if safe_usage is not None:
                fact["usage"] = safe_usage
            prior = steps_by_index.get(index)
            if prior is not None and prior != fact:
                return _unknown("conflicting_step")
            steps_by_index[index] = fact
            active_steps.discard(index)
            completed_steps.add(index)
            continue

        result = _mapping(event.get("result"))
        if (result is None or not isinstance(result.get("status"), str)
                or result["status"] not in RESULT_STATUSES):
            return _unknown("invalid_result")
        duration = result.get("duration_seconds")
        safe_duration = _number(duration) if duration is not None else None
        if duration is not None and safe_duration is None:
            return _unknown("invalid_duration")
        turns = result.get("num_turns")
        if turns is not None and (isinstance(turns, bool) or not isinstance(turns, int) or turns < 0):
            return _unknown("invalid_turn_count")
        safe_usage = _usage(result.get("usage"))
        if safe_usage is False:
            return _unknown("invalid_usage")
        result_facts = {"status": result["status"]}
        if safe_duration is not None:
            result_facts["duration_seconds"] = safe_duration
        if turns is not None:
            result_facts["num_turns"] = turns
        if safe_usage is not None:
            result_facts["usage"] = safe_usage

    if result_facts is None:
        return _unknown("missing_result")
    if active_steps:
        return _unknown("step_unfinished")
    return {"complete": True, "reason": None, "result": result_facts,
            "steps": [steps_by_index[index] for index in sorted(steps_by_index)]}
