"""USM-13/15: provider-native token counters retain source and scope without raw payloads."""
import json
from pathlib import Path

import pytest

from peerhub.extensions.adapters.telemetry import parse_provider_usage


pytestmark = pytest.mark.unit
FIXTURES = Path(__file__).parents[1] / "fixtures" / "telemetry"
SENTINELS = ("cc-vendor-session-sentinel", "cx-vendor-thread-sentinel", "ag-vendor-session-sentinel",
             "answer-fragment-sentinel")


@pytest.mark.parametrize("kind,filename,expected", [
    ("cc", "cc-result-usage-v1.ndjson", {
        "schema_version": 1, "scope": "session_cumulative", "source": "cc.result.usage",
        "reported": {"input_tokens": 10418, "cache_read_input_tokens": 8113,
                     "cache_creation_input_tokens": 100, "output_tokens": 589},
        "normalized": {"input_tokens": 18631, "cached_input_tokens": 8113,
                       "cache_write_tokens": 100, "output_tokens": 589},
        "coverage": {"state": "MEASURED", "reason": None},
    }),
    ("cx", "cx-turn-usage-v1.ndjson", {
        "schema_version": 1, "scope": "session_cumulative", "source": "cx.turn.completed.usage",
        "reported": {"input_tokens": 18351, "cached_input_tokens": 18176,
                     "cache_write_input_tokens": 0, "output_tokens": 10,
                     "reasoning_output_tokens": 0},
        "normalized": {"input_tokens": 18351, "cached_input_tokens": 18176,
                       "cache_write_tokens": 0, "output_tokens": 10},
        "coverage": {"state": "MEASURED", "reason": None},
    }),
    ("ag", "ag-result-usage-v1.json", {
        "schema_version": 1, "scope": "session_cumulative", "source": "ag.result.usage",
        "reported": {"input_tokens": 10418, "output_tokens": 589, "thinking_tokens": 551,
                     "cache_read_tokens": 8113, "total_tokens": 11007},
        "normalized": {"input_tokens": 10418, "cached_input_tokens": 8113, "output_tokens": 589},
        "coverage": {"state": "MEASURED", "reason": None},
    }),
])
def test_usm_usage_01_native_fields_scope_and_privacy(kind, filename, expected):
    raw = (FIXTURES / filename).read_text(encoding="utf-8")
    facts = parse_provider_usage(kind, raw)
    assert facts == expected
    serialized = json.dumps(facts, sort_keys=True)
    assert all(secret not in serialized for secret in SENTINELS)


@pytest.mark.parametrize("kind,raw", [
    ("cc", '{"type":"result","usage":{"input_tokens":true}}'),
    ("cx", '{"type":"turn.completed","usage":{"output_tokens":-1}}'),
    ("cc", '{"type":"result","usage":{"input_tokens":2,"input_tokens":3}}'),
    ("ag", '{"response":"ok","usage":{"total_tokens":1,"total_tokens":2}}'),
])
def test_usm_usage_02_invalid_or_ambiguous_counts_are_unknown(kind, raw):
    facts = parse_provider_usage(kind, raw)
    assert facts["reported"] == facts["normalized"] == {}
    assert facts["coverage"]["state"] == "UNKNOWN"


def test_usm_usage_03_absent_usage_is_not_zero():
    facts = parse_provider_usage("cx", '{"type":"turn.completed","usage":{}}')
    assert facts == {
        "schema_version": 1, "scope": "session_cumulative", "source": "cx.turn.completed.usage", "reported": {},
        "normalized": {}, "coverage": {"state": "ABSENT", "reason": "usage_absent"},
    }


def test_usm_usage_04_unknown_event_invalidates_coverage():
    raw = ('{"type":"future_event","secret":"discard-me"}\n'
           '{"type":"result","usage":{"output_tokens":4}}')
    facts = parse_provider_usage("cc", raw)
    assert facts["coverage"] == {"state": "UNKNOWN", "reason": "unknown_event"}
    assert "discard-me" not in json.dumps(facts)


def test_usm_usage_05_malformed_event_type_fails_closed_without_raising():
    facts = parse_provider_usage("cc", '{"type":{},"secret":"discard"}\n{"type":"result","usage":{"output_tokens":4}}')
    assert facts["coverage"] == {"state": "UNKNOWN", "reason": "unknown_event"}
    assert "discard" not in json.dumps(facts)


@pytest.mark.parametrize("kind,raw,reported", [
    ("cc", '{"type":"result","usage":{"input_tokens":7}}', {"input_tokens": 7}),
    ("ag", '{"response":"ok","usage":{"thinking_tokens":5,"total_tokens":7}}',
     {"thinking_tokens": 5, "total_tokens": 7}),
])
def test_usm_usage_06_partial_native_usage_is_preserved(kind, raw, reported):
    facts = parse_provider_usage(kind, raw)
    assert facts["reported"] == reported
    assert facts["coverage"]["state"] == "MEASURED"
    assert facts["normalized"] == {}
