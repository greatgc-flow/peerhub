"""#366 A3: real cc stream-json output (with rate_limit_event) keeps provider usage measured in monitor USAGE."""
import json

import pytest

from peerhub.cli.monitor_view import _group_lines
from peerhub.extensions.adapters.telemetry import parse_provider_usage


pytestmark = pytest.mark.unit
# Real-shaped Claude Code `-p --output-format stream-json --verbose` output; values are sentinels.
CC_STREAM = "\n".join(json.dumps(event) for event in (
    {"type": "system", "subtype": "init", "session_id": "cc-vendor-session-sentinel", "model": "claude-opus-5-5"},
    {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "answer-fragment-sentinel"}]},
     "session_id": "cc-vendor-session-sentinel"},
    {"type": "rate_limit_event", "rate_limit_info": {"status": "allowed", "resetsAt": 1794650000,
                                                     "rateLimitType": "five_hour"},
     "uuid": "rate-limit-uuid-sentinel", "session_id": "cc-vendor-session-sentinel"},
    {"type": "result", "subtype": "success", "is_error": False, "result": "answer-fragment-sentinel",
     "session_id": "cc-vendor-session-sentinel",
     "usage": {"input_tokens": 2, "cache_read_input_tokens": 14180, "cache_creation_input_tokens": 10321,
               "output_tokens": 8, "service_tier": "standard"}},
)) + "\n"


def _usage_row(telemetry: dict[str, object]) -> str:
    rows, _ = _group_lines("usage", {"data": {"sessions": [{"peer_id": "cc", "telemetry": telemetry}]}},
                           width=120, unicode=False, expand=True)
    return rows[0]


def test_cc_rate_limit_event_keeps_usage_measured_and_shown():
    facts = parse_provider_usage("cc", CC_STREAM)
    assert facts["coverage"] == {"state": "MEASURED", "reason": None}
    assert facts["reported"] == {"input_tokens": 2, "cache_read_input_tokens": 14180,
                                 "cache_creation_input_tokens": 10321, "output_tokens": 8}
    assert all(secret not in json.dumps(facts) for secret in ("sentinel", "five_hour", "allowed"))
    assert _usage_row(facts) == "cc provider-reported tokens: 24,511"


def test_cc_result_without_usage_still_shows_unknown_tokens():
    stream = CC_STREAM.replace(', "usage": {"input_tokens": 2, "cache_read_input_tokens": 14180, '
                               '"cache_creation_input_tokens": 10321, "output_tokens": 8, "service_tier": "standard"}', "")
    facts = parse_provider_usage("cc", stream)
    assert facts["coverage"] == {"state": "ABSENT", "reason": "usage_absent"}
    assert _usage_row(facts) == "cc provider-reported tokens: ?"


@pytest.mark.parametrize("kind,stdout", [
    ("cx", '{"type":"rate_limit_event"}\n{"type":"turn.completed","usage":{"output_tokens":1}}\n'),
    ("cc", '{"type":"future_event"}\n{"type":"result","usage":{"output_tokens":1}}\n'),
])
def test_other_unknown_events_still_fail_closed(kind, stdout):
    assert parse_provider_usage(kind, stdout)["coverage"] == {"state": "UNKNOWN", "reason": "unknown_event"}
