"""#366 A3/F1: real cc stream-json output (with rate_limit_event) keeps usage measured and refusals classified."""
import json
import sys
from pathlib import Path

import pytest

from peerhub.cli.monitor_view import _group_lines
from peerhub.extensions.adapters import CliRuntimeTarget, base, telemetry
from peerhub.extensions.adapters.base import IncrementalEventParser
from peerhub.extensions.adapters.telemetry import parse_provider_usage
from tests.communication.bridge_helpers import bseed


pytestmark = pytest.mark.unit
REAL_CC_STREAM = (Path(__file__).parents[1] / "fixtures" / "telemetry" / "cc-stream-real-2026-11-14.ndjson").read_text(
    encoding="utf-8")
RATE_LIMIT_EVENT = {"type": "rate_limit_event", "rate_limit_info": {"status": "rejected", "rateLimitType": "five_hour"},
                    "session_id": "s1"}
CC_FIVE_HOUR_LIMIT = {"type": "result", "subtype": "error_during_execution", "is_error": True,
                      "errors": ["You've hit your 5-hour limit. Resets 3pm (Asia/Seoul)"]}
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


def _stream_parser(stdout: str) -> IncrementalEventParser:
    parser = IncrementalEventParser("cc")
    parser.feed(stdout.encode("utf-8"))
    parser.close()
    return parser


def test_real_cc_stream_passes_both_parsers():
    facts = parse_provider_usage("cc", REAL_CC_STREAM)
    assert facts["coverage"] == {"state": "MEASURED", "reason": None}
    assert facts["normalized"] == {"uncached_input_tokens": 2, "provider_input_tokens": 25848,
                                   "cache_read_input_tokens": 14180, "cache_creation_input_tokens": 11666,
                                   "output_tokens": 30}
    parser = _stream_parser(REAL_CC_STREAM)
    assert parser.has_success is True
    assert parser.refusal_evidence_incomplete is False
    assert parser.has_tool_calls is False


def test_unknown_cc_event_still_marks_refusal_evidence_incomplete():
    parser = _stream_parser('{"type":"future_event"}\n' + json.dumps(CC_FIVE_HOUR_LIMIT) + "\n")
    assert parser.refusal_evidence_incomplete is True


def test_both_parsers_read_the_one_shared_cc_event_list(monkeypatch):
    assert base.CC_EVENT_TYPES is telemetry.CC_EVENT_TYPES
    widened = (*telemetry.CC_EVENT_TYPES, "probe_event")
    monkeypatch.setattr(telemetry, "CC_EVENT_TYPES", widened)
    monkeypatch.setattr(base, "CC_EVENT_TYPES", widened)
    stdout = '{"type":"probe_event"}\n' + REAL_CC_STREAM
    assert parse_provider_usage("cc", stdout)["coverage"]["state"] == "MEASURED"
    assert _stream_parser(stdout).refusal_evidence_incomplete is False


def _cc_target(tmp_path, stdout: str, exit_code: int) -> CliRuntimeTarget:
    script = tmp_path / "provider.py"
    script.write_text(f"import sys\nsys.stdout.buffer.write({stdout.encode('utf-8')!r})\nsys.exit({exit_code})\n",
                      encoding="utf-8", newline="\n")
    return CliRuntimeTarget("cc", tmp_path, command=[sys.executable, str(script)], timeout_s=15)


def test_successful_cc_run_with_rate_limit_event_is_delivered(tmp_path, bridge_h):
    bseed(bridge_h)
    assert bridge_h.delivery_cycle("b", "s", _cc_target(tmp_path, REAL_CC_STREAM, 0)).status == "delivered"


def test_cc_refusal_with_rate_limit_event_is_classified(tmp_path, bridge_h):
    stdout = "".join(json.dumps(event) + "\n" for event in (RATE_LIMIT_EVENT, CC_FIVE_HOUR_LIMIT))
    bseed(bridge_h)
    result = bridge_h.delivery_cycle("b", "s", _cc_target(tmp_path, stdout, 1))
    assert result.status == "not_executed"
    assert result.detail["reason"] == "usage_limit"
