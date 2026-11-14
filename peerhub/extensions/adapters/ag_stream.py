"""Privacy-preserving parser for Antigravity's documented stream-json output."""
from __future__ import annotations

import json
import math
import re
from typing import Any, NamedTuple, cast

MAX_STREAM_BYTES = 1_048_576
MAX_EVENT_BYTES = 262_144
MAX_STEPS = 512
USAGE_FIELDS = ("input_tokens", "output_tokens", "thinking_tokens", "cache_read_tokens", "total_tokens")
STEP_TYPES = frozenset(("user_input", "agent_response", "tool", "checkpoint", "system_message"))
STATES = frozenset(("ACTIVE", "DONE", "ERROR"))  # ERROR: a failed (e.g. auto-denied) tool step, agy 1.5.9 (#371)
RESULT_STATUSES = frozenset(("SUCCESS", "ERROR", "CANCELED", "INTERRUPTED", "INVALID"))
MAX_UNKNOWN_NAMES = 5
MAX_UNKNOWN_NAME_CHARS = 40


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


def _split_events(text: str) -> tuple[str | None, list[dict[str, Any]]]:
    """Every non-blank line must be one JSON object within the size limits: (failure reason or None, events)."""
    try:
        total_bytes = len(text.encode("utf-8"))
    except UnicodeEncodeError:
        return "invalid_encoding", []
    if total_bytes > MAX_STREAM_BYTES:
        return "stream_too_large", []
    events: list[dict[str, Any]] = []
    for line in text.split("\n"):
        if not line.strip():
            continue
        if len(line.encode("utf-8")) > MAX_EVENT_BYTES:
            return "event_too_large", []
        try:
            value: object = json.loads(line, object_pairs_hook=_unique_object)
        except _DuplicateJsonKey:
            return "duplicate_json_key", []
        except (ValueError, RecursionError):
            return "malformed_json", []
        if not isinstance(value, dict):
            return "invalid_event", []
        events.append(cast(dict[str, Any], value))
    return None, events


def _result_facts(value: object) -> dict[str, Any] | str:
    """Bounded facts of one `result` payload, or the reason one of its fields is invalid."""
    result = _mapping(value)
    if result is None or not isinstance(result.get("status"), str) or result["status"] not in RESULT_STATUSES:
        return "invalid_result"
    duration = result.get("duration_seconds")
    safe_duration = _number(duration) if duration is not None else None
    if duration is not None and safe_duration is None:
        return "invalid_duration"
    turns = result.get("num_turns")
    if turns is not None and (isinstance(turns, bool) or not isinstance(turns, int) or turns < 0):
        return "invalid_turn_count"
    safe_usage = _usage(result.get("usage"))
    if safe_usage is False:
        return "invalid_usage"
    facts: dict[str, Any] = {"status": result["status"]}
    if safe_duration is not None:
        facts["duration_seconds"] = safe_duration
    if turns is not None:
        facts["num_turns"] = turns
    if safe_usage is not None:
        facts["usage"] = safe_usage
    return facts


def parse_ag_stream_json(text: str) -> dict[str, Any]:
    """Return only bounded numeric facts; malformed, ambiguous or incomplete streams fail closed.

    Raw event text, response fragments, tools, paths and vendor conversation IDs are never returned.
    """
    reason, events = _split_events(text)
    if reason is not None:
        return _unknown(reason)
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

        facts = _result_facts(event.get("result"))
        if isinstance(facts, str):
            return _unknown(facts)
        result_facts = facts

    if result_facts is None:
        return _unknown("missing_result")
    if active_steps:
        return _unknown("step_unfinished")
    return {"complete": True, "reason": None, "result": result_facts,
            "steps": [steps_by_index[index] for index in sorted(steps_by_index)]}


def encode_user_line(prompt: str) -> bytes:
    """The one stdin message of `agy --input-format stream-json`: pure ASCII, one trailing newline, then EOF."""
    message = {"event": "user", "message": {"role": "user", "content": prompt}}
    return json.dumps(message, ensure_ascii=True, separators=(",", ":")).encode("ascii") + b"\n"


def _framing(events: list[dict[str, Any]]) -> str | None:
    """Decision D1: `init` first, one conversation id wherever one appears, exactly one `result` and it is last,
    valid terminal fields, and a string `response` for SUCCESS. Step events are never judged here (D2)."""
    if not events or events[0].get("event") != "init":
        return "missing_init"
    conversation_id = _conversation_id(events[0])
    if conversation_id is None:
        return "invalid_session_identity"
    for event in events[1:]:
        kind = event.get("event")
        holder = event if kind == "init" else _mapping(event.get("step_update")) if kind == "step_update" else None
        if holder is not None and "conversation_id" in holder and holder["conversation_id"] != conversation_id:
            return "session_mismatch"
    results = [index for index, event in enumerate(events) if event.get("event") == "result"]
    if not results:
        return "missing_result"
    if results[0] != len(events) - 1:
        return "event_after_result"
    facts = _result_facts(events[-1].get("result"))
    if isinstance(facts, str):
        return facts
    if _conversation_id(events[-1]) != conversation_id:
        return "session_mismatch"
    if facts["status"] == "SUCCESS" and not isinstance(events[-1]["result"].get("response"), str):
        return "invalid_response"
    return None


def _unknown_step_types(events: list[dict[str, Any]]) -> dict[str, Any]:
    """The count of distinct unknown step types, and their display names in first-seen order: each cut to 40
    characters of [a-z0-9_], de-duplicated after that, at most 5."""
    names: dict[str, None] = {}
    shown: dict[str, None] = {}
    for event in events:
        update = _mapping(event.get("step_update")) if event.get("event") == "step_update" else None
        step_type = update.get("step_type") if update is not None else None
        if isinstance(step_type, str) and step_type not in STEP_TYPES and step_type not in names:
            names[step_type] = None
            if len(shown) < MAX_UNKNOWN_NAMES:
                shown[re.sub(r"[^a-z0-9_]", "_", step_type[:MAX_UNKNOWN_NAME_CHARS])] = None
    return {"unknown_step_types": list(shown), "unknown_step_type_count": len(names)}


def terminal_result(stdout: bytes) -> dict[str, Any]:
    """Observe the terminal event of a captured stream: `complete`, `absent` (no terminal) or `invalid` (decision D).

    Delivery rests on the framing and the terminal only (`_framing`); step events never change the observation.
    The full validator still runs for telemetry: unless it reports complete, `validation` carries its reason and
    the bounded unknown step type names, and the caller records usage as unknown. For SUCCESS only,
    `result.response` (a string) is returned; no other field is read by name except the validated conversation id.
    """
    observed: dict[str, Any] = {"observation": "invalid", "reason": None, "status": None, "usage": None,
                                "conversation_id": None, "terminal_line": None, "response": None, "validation": None}
    try:
        text = stdout.decode("utf-8")
    except UnicodeDecodeError:
        return {**observed, "reason": "invalid_encoding"}
    if not text.strip():
        return {**observed, "observation": "absent", "reason": "no_output"}
    reason, events = _split_events(text)
    reason = reason or _framing(events)
    if reason is not None:
        return {**observed, "observation": "absent" if reason == "missing_result" else "invalid", "reason": reason}
    terminal_line = [line for line in text.split("\n") if line.strip()][-1]
    result = cast(dict[str, Any], events[-1]["result"])
    facts = cast(dict[str, Any], _result_facts(result))
    checked = parse_ag_stream_json(text)
    validation = None if checked["complete"] else {"reason": checked["reason"], **_unknown_step_types(events)}
    return {**observed, "observation": "complete", "status": facts["status"], "usage": facts.get("usage"),
            "conversation_id": result.get("conversation_id"), "terminal_line": terminal_line,
            "response": result.get("response") if facts["status"] == "SUCCESS" else None, "validation": validation}


class QuotaMatch(NamedTuple):
    reason: str
    reset_time: str | None
    reset_phrase: str | None
    line: str


def terminal_quota(terminal_line: str) -> QuotaMatch | None:
    """First quota or usage-limit line among every string (keys and values) of one validated terminal event.

    Full lines are classified before anything is shortened; the reset and its phrase come from the same match.
    """
    from peerhub.extensions.adapters.base import classify_refusal_text, reset_match

    stack: list[object] = [json.loads(terminal_line)]
    while stack:
        node = stack.pop()
        if isinstance(node, str):
            for line in node.splitlines():
                refusal = classify_refusal_text(line)
                if refusal is not None and refusal["reason"] in ("quota_exhausted", "usage_limit"):
                    m = reset_match(line)
                    return QuotaMatch(refusal["reason"], m.group(1).strip() if m else None, m.group(0) if m else None, line)
        elif isinstance(node, dict):
            for key, value in reversed(list(cast(dict[str, Any], node).items())):
                stack.append(value)
                stack.append(key)
        elif isinstance(node, list):
            stack.extend(reversed(cast(list[Any], node)))
    return None
