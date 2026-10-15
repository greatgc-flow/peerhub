"""USM-13/16/19a: the AG stream parser emits only bounded, attributable facts."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from peerhub.extensions.adapters.ag_stream import parse_ag_stream_json

pytestmark = pytest.mark.unit
FIXTURE = Path(__file__).parents[1] / "fixtures" / "telemetry" / "agy-stream-json-v1.ndjson"
COUNTERS = ("input_tokens", "output_tokens", "thinking_tokens", "cache_read_tokens", "total_tokens")
SENTINELS = ("vendor-session-sentinel", "workspace-sentinel", "tool-name-sentinel",
             "response-fragment-sentinel", "answer-fragment-sentinel")


def _events() -> list[dict[str, Any]]:
    return [json.loads(line) for line in FIXTURE.read_text(encoding="utf-8").splitlines()]


def _text(events: list[dict[str, Any]]) -> str:
    return "\n".join(json.dumps(event, separators=(",", ":")) for event in events)


def test_usm_ag_stream_01_sanitized_golden_facts_and_scope():
    facts = parse_ag_stream_json(FIXTURE.read_text(encoding="utf-8"))
    assert facts == {
        "complete": True,
        "reason": None,
        "result": {
            "status": "SUCCESS", "duration_seconds": 6.88, "num_turns": 1,
            "usage": {"input_tokens": 10418, "output_tokens": 589, "thinking_tokens": 551,
                      "cache_read_tokens": 8113, "total_tokens": 11007},
        },
        "steps": [
            {"step_index": 3, "step_type": "agent_response", "duration_seconds": 6.28,
             "usage": {"input_tokens": 10302, "output_tokens": 582, "thinking_tokens": 551,
                       "cache_read_tokens": 8113, "total_tokens": 10884}},
            {"step_index": 4, "step_type": "checkpoint", "duration_seconds": 0.53,
             "usage": {"input_tokens": 116, "output_tokens": 7, "thinking_tokens": 0,
                       "cache_read_tokens": 0, "total_tokens": 123}},
        ],
    }


def test_usm_ag_stream_02_drops_all_raw_and_vendor_identity_fields():
    facts = parse_ag_stream_json(FIXTURE.read_text(encoding="utf-8"))
    serialized = json.dumps(facts, sort_keys=True)
    for sentinel in SENTINELS:
        assert sentinel not in serialized
    assert set(facts) == {"complete", "reason", "result", "steps"}
    assert set(facts["result"]) == {"status", "duration_seconds", "num_turns", "usage"}
    assert all(set(step) <= {"step_index", "step_type", "duration_seconds", "usage"} for step in facts["steps"])


@pytest.mark.parametrize("mutation,reason", [
    ("session_mismatch", "session_mismatch"),
    ("unknown_step_type", "unknown_step_type"),
    ("invalid_counter", "invalid_usage"),
    ("out_of_order", "step_order"),
    ("missing_result", "missing_result"),
    ("unknown_event", "unknown_event"),
    ("unhashable_step_type", "unknown_step_type"),
    ("unhashable_state", "invalid_state"),
    ("unhashable_status", "invalid_result"),
    ("state_regression", "step_state_regression"),
    ("unfinished_step", "step_unfinished"),
    ("conflicting_step_type", "conflicting_step_type"),
    ("nonterminal_result", "invalid_result"),
    ("invalid_active_counter", "invalid_usage"),
])
def test_usm_ag_stream_03_invalid_or_ambiguous_shapes_fail_closed(mutation: str, reason: str):
    events = _events()
    if mutation == "session_mismatch":
        events[-1]["result"]["conversation_id"] = "different-vendor-session"
    elif mutation == "unknown_step_type":
        events[2]["step_update"]["step_type"] = "future_sensitive_step"
    elif mutation == "invalid_counter":
        events[2]["step_update"]["usage"]["input_tokens"] = True
    elif mutation == "out_of_order":
        events[2]["step_update"]["step_index"] = 5
    elif mutation == "missing_result":
        events.pop()
    elif mutation == "unknown_event":
        events.insert(-1, {"event": "future_event", "payload": {"secret": "sentinel"}})
    elif mutation == "unhashable_step_type":
        events[2]["step_update"]["step_type"] = []
    elif mutation == "unhashable_state":
        events[2]["step_update"]["state"] = {}
    elif mutation == "unhashable_status":
        events[-1]["result"]["status"] = []
    elif mutation == "state_regression":
        events.insert(3, {"event": "step_update", "step_update": {
            "conversation_id": events[0]["conversation_id"], "step_index": 3,
            "state": "ACTIVE", "step_type": "agent_response",
        }})
    elif mutation == "unfinished_step":
        events.insert(-1, {"event": "step_update", "step_update": {
            "conversation_id": events[0]["conversation_id"], "step_index": 5,
            "state": "ACTIVE", "step_type": "tool",
        }})
    elif mutation == "conflicting_step_type":
        events.insert(3, {"event": "step_update", "step_update": {
            "conversation_id": events[0]["conversation_id"], "step_index": 3,
            "state": "DONE", "step_type": "tool",
        }})
    elif mutation == "nonterminal_result":
        events[-1]["result"]["status"] = "RUNNING"
    elif mutation == "invalid_active_counter":
        events[1]["step_update"]["usage"] = {"input_tokens": True}

    facts = parse_ag_stream_json(_text(events))
    assert facts["complete"] is False
    assert facts["reason"] == reason
    assert facts["result"] is None and facts["steps"] == []


def test_usm_ag_stream_04_identical_done_replay_collapses_but_conflict_is_unknown():
    events = _events()
    duplicate = json.loads(json.dumps(events[2]))
    events.insert(3, duplicate)
    facts = parse_ag_stream_json(_text(events))
    assert facts["complete"] is True
    assert [step["step_index"] for step in facts["steps"]] == [3, 4]

    events[3]["step_update"]["usage"]["output_tokens"] += 1
    conflicted = parse_ag_stream_json(_text(events))
    assert conflicted["complete"] is False
    assert conflicted["reason"] == "conflicting_step"
    assert conflicted["result"] is None and conflicted["steps"] == []


@pytest.mark.parametrize("case,reason", [
    ("malformed", "malformed_json"),
    ("oversized", "event_too_large"),
])
def test_usm_ag_stream_05_malformed_or_oversized_input_is_bounded(case: str, reason: str):
    payload = ('{"event":"init"' if case == "malformed" else
               '{"event":"init","conversation_id":"x","init":{"padding":"' + "x" * 262_145 + '"}}')
    facts = parse_ag_stream_json(payload)
    assert facts == {"complete": False, "reason": reason, "result": None, "steps": []}


def test_usm_ag_stream_05b_duplicate_json_keys_fail_closed():
    payload = ('{"event":"init","conversation_id":"vendor-session"}\n'
               '{"event":"result","result":{"conversation_id":"vendor-session",'
               '"status":"SUCCESS","usage":{"input_tokens":999,"input_tokens":10418}}}')
    assert parse_ag_stream_json(payload) == {
        "complete": False, "reason": "duplicate_json_key", "result": None, "steps": [],
    }


def test_usm_ag_stream_06_step_limit_is_explicit_and_never_truncated_as_complete():
    events = [{"event": "init", "conversation_id": "vendor-session", "init": {}}]
    events.extend({"event": "step_update", "step_update": {
        "conversation_id": "vendor-session", "step_index": i, "state": "DONE", "step_type": "tool",
    }} for i in range(513))
    events.append({"event": "result", "result": {
        "conversation_id": "vendor-session", "status": "SUCCESS", "num_turns": 1,
    }})
    facts = parse_ag_stream_json(_text(events))
    assert facts == {"complete": False, "reason": "step_limit", "result": None, "steps": []}


def test_usm_ag_stream_06b_active_steps_are_counted_toward_the_limit():
    events = [{"event": "init", "conversation_id": "vendor-session", "init": {}}]
    events.extend({"event": "step_update", "step_update": {
        "conversation_id": "vendor-session", "step_index": i,
        "state": "ACTIVE", "step_type": "tool",
    }} for i in range(513))
    events.append({"event": "result", "result": {
        "conversation_id": "vendor-session", "status": "SUCCESS",
    }})
    assert parse_ag_stream_json(_text(events)) == {
        "complete": False, "reason": "step_limit", "result": None, "steps": [],
    }
