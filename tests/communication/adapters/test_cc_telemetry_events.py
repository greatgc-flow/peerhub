"""#366 A3/F1: real cc stream-json output (with rate_limit_event) keeps usage measured and refusals classified."""
import json
import sys
from pathlib import Path

import pytest

from peerhub.cli.monitor_view import _group_lines
from peerhub.extensions.adapters import CliRuntimeTarget, base, telemetry
from peerhub.extensions.adapters.base import IncrementalEventParser
from peerhub.extensions.adapters.telemetry import parse_provider_usage
from peerhub.extensions.usage_normalization import unknown_usage
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


# ---- #376 P2: context of a cc ask, estimated from the stream ------------------------------------------------------------
USAGE = {"input_tokens": 100, "cache_read_input_tokens": 2000, "cache_creation_input_tokens": 30, "output_tokens": 5}
OPUS, HAIKU = "claude-opus-5-5", "claude-haiku-5-5"
WINDOWS = {HAIKU: {"contextWindow": 200_000, "inputTokens": 9}, OPUS: {"contextWindow": 1_000_000, "inputTokens": 9}}
MISSING = object()


def _assistant(model: object = OPUS, usage: object = USAGE, parent: object = None) -> dict[str, object]:
    message = {"role": "assistant", "content": [{"type": "text", "text": "x"}], "model": model, "usage": usage}
    return {"type": "assistant", "message": {k: v for k, v in message.items() if v is not MISSING},
            "parent_tool_use_id": parent, "session_id": "s1"}


def _result(model_usage: object = WINDOWS, usage: object = USAGE) -> dict[str, object]:
    event = {"type": "result", "subtype": "success", "is_error": False, "result": "x", "session_id": "s1",
             "usage": usage, "modelUsage": model_usage}
    return {k: v for k, v in event.items() if v is not MISSING}


def _stream(*events: dict[str, object]) -> str:
    return "".join(json.dumps(event) + "\n" for event in events)


def test_the_recorded_cc_stream_gives_the_context_of_its_last_message():
    facts = parse_provider_usage("cc", REAL_CC_STREAM)
    assert facts["context"] == {"tokens": 2 + 14180 + 11666, "window": 1_000_000}
    assert set(facts) == {"schema_version", "scope", "source", "reported", "normalized", "coverage", "context"}


def test_the_last_main_chain_message_and_the_exact_model_key_give_the_context():
    stream = _stream(_assistant(usage={"input_tokens": 1}),
                     _assistant(HAIKU, {"input_tokens": 150_000}, parent="toolu_1"),
                     _assistant(),
                     _assistant(HAIKU, {"input_tokens": 190_000}, parent="toolu_2"),  # a sub-agent message is never the last one
                     _result())
    assert parse_provider_usage("cc", stream)["context"] == {"tokens": 2130, "window": 1_000_000}
    partial = _stream(_assistant(usage={"cache_read_input_tokens": 7}), _result())  # a missing token kind counts as zero
    assert parse_provider_usage("cc", partial)["context"] == {"tokens": 7, "window": 1_000_000}


@pytest.mark.parametrize("events", [
    [_assistant(), _result({f"{OPUS}[1m]": {"contextWindow": 1_000_000}})],  # no exact model key
    [_assistant(), _result({OPUS.upper(): {"contextWindow": 1_000_000}})],
    [_assistant(), _result({HAIKU: {"contextWindow": 200_000}})],  # only another model is listed
    [_assistant(), _result(MISSING)], [_assistant(), _result([OPUS])], [_assistant(), _result({OPUS: 1_000_000})],
    [_assistant(), _result({OPUS: {}})], [_assistant(), _result({OPUS: {"contextWindow": True}})],
    [_assistant(), _result({OPUS: {"contextWindow": "1000000"}})], [_assistant(), _result({OPUS: {"contextWindow": 1e6}})],
    [_assistant(), _result({OPUS: {"contextWindow": 0}})], [_assistant(), _result({OPUS: {"contextWindow": -1}})],
    [_assistant(model=MISSING), _result()], [_assistant(model=7), _result()], [_assistant(model=[OPUS]), _result()],
    [_assistant(usage=MISSING), _result()], [_assistant(usage=[1]), _result()],
    [_assistant(usage={"input_tokens": True}), _result()], [_assistant(usage={"input_tokens": "9"}), _result()],
    [_assistant(usage={"input_tokens": -1, "cache_read_input_tokens": 9}), _result()],
    [_assistant(usage={"input_tokens": 1.5}), _result()], [_assistant(usage={"input_tokens": 0}), _result()],
    [_assistant(), _assistant(usage={"input_tokens": "9"}), _result()],  # the last message decides: no earlier one is used
    [_assistant(parent="toolu_1"), _result()],  # only a sub-agent message
    [{"type": "assistant", "message": "text", "session_id": "s1"}, _result()],
    [_result()],
    [_assistant(), _result(usage=MISSING)],  # without a measured usage the stream gives no context either
])
def test_a_stream_without_an_exact_reading_gives_no_context(events: list[dict[str, object]]):
    facts = parse_provider_usage("cc", _stream(*events))
    assert "context" not in facts
    assert set(facts) == {"schema_version", "scope", "source", "reported", "normalized", "coverage"}


def test_an_oversized_or_other_provider_stream_gives_no_context():
    assert parse_provider_usage("cc", REAL_CC_STREAM + "\n" * 1_048_577) == unknown_usage("stream_too_large")
    cx = '{"type":"turn.completed","usage":{"input_tokens":20},"modelUsage":{"m":{"contextWindow":9}}}\n'
    assert "context" not in parse_provider_usage("cx", cx)


CC_MEASURED = {"schema_version": 1, "scope": "session_cumulative", "source": "cc.result.usage",
               "reported": {"input_tokens": 7}, "normalized": {"uncached_input_tokens": 7},
               "coverage": {"state": "MEASURED", "reason": None}}
NO_CONTEXT = {"last_input_tokens": None, "context_window": None, "context_pct": None, "source": "unknown",
              "window_reported_at": None}


def _receipt(tmp_path, candidate: object, kind: str = "cc") -> tuple[dict[str, object], str, str]:
    """(session_telemetry detail, its coverage state, the stored result) of one ask finalized with `candidate`."""
    from tests.communication.bridge.test_retained_sessions import _ProviderTelemetryRuntime, _start_telemetry_delivery
    from tests.communication.harness.bridge import BridgeHarness
    from peerhub.extensions import delivery_projections
    h = BridgeHarness(tmp_path / "ws")
    token, did = _start_telemetry_delivery(h, _ProviderTelemetryRuntime(kind))
    h.finalize_terminal(token, {"response": "ok", "provider_telemetry": candidate}, did)
    with h.cs.transaction() as conn:
        delivery = conn.execute("SELECT * FROM bridge_deliveries WHERE delivery_id=?", (did,)).fetchone()
        detail = conn.execute("SELECT detail FROM bridge_evidence WHERE kind='session_telemetry' AND delivery_id=?",
                              (did,)).fetchone()[0]
        return json.loads(detail), delivery_projections.telemetry_coverage_state(conn, delivery), delivery["result_json"]


def test_the_receipt_stores_the_context_in_its_existing_fields(tmp_path):
    detail, state, result = _receipt(tmp_path, {**CC_MEASURED, "context": {"tokens": 25_848, "window": 1_000_000}})
    assert {key: detail[key] for key in NO_CONTEXT} == {
        "last_input_tokens": 25_848, "context_window": 1_000_000, "context_pct": 2, "source": "cc.stream.context",
        "window_reported_at": detail["observed_at"]}
    assert isinstance(detail["observed_at"], float)
    assert state == "MEASURED"  # the exact key set of the receipt is unchanged, and the usage is still validated
    assert detail["usage"]["reported"] == {"input_tokens": 7}
    assert all(text not in result for text in ("provider_telemetry", "25848", "1000000", "cc.stream.context"))
    assert json.loads(result)["session"]["telemetry"] == {  # the result's own snapshot stays unknown: it is digested
        "last_input_tokens": None, "context_window": None, "context_pct": None, "source": "unknown", "quota": None}


@pytest.mark.parametrize(("tokens", "window", "pct"), [(999, 1000, 99), (1000, 1000, 100), (1, 100_000_000, 0), (1387, 1110, None),
                                                       (1, 100_000_001, None), (0, 1000, None), (-1, 1000, None)])
def test_the_stored_percentage_floors_and_an_incoherent_reading_is_unknown(tmp_path, tokens: int, window: int, pct: int | None):
    detail, state, _ = _receipt(tmp_path, {**CC_MEASURED, "context": {"tokens": tokens, "window": window}})
    assert state == "MEASURED"
    if pct is None:
        assert {key: detail[key] for key in NO_CONTEXT} == NO_CONTEXT
    else:
        assert (detail["last_input_tokens"], detail["context_window"], detail["context_pct"]) == (tokens, window, pct)


@pytest.mark.parametrize("context", [
    None, [10, 100], "10/100", 5, {}, {"tokens": 10}, {"window": 100}, {"tokens": 10, "window": 100, "model": "x"},
    {"tokens": True, "window": 100}, {"tokens": 10, "window": True}, {"tokens": "10", "window": 100},
    {"tokens": 10.0, "window": 100}, {"tokens": 10, "window": 100.0}, {"tokens": 10, "window": None}])
def test_a_context_candidate_of_the_wrong_shape_leaves_the_fields_null_and_the_usage_measured(tmp_path, context: object):
    detail, state, _ = _receipt(tmp_path, {**CC_MEASURED, "context": context})
    assert {key: detail[key] for key in NO_CONTEXT} == NO_CONTEXT
    assert state == "MEASURED"


def test_a_context_candidate_of_another_provider_is_never_stored(tmp_path):
    usage = {**CC_MEASURED, "source": "cx.turn.completed.usage", "normalized": {"provider_input_tokens": 7}}
    detail, state, _ = _receipt(tmp_path, {**usage, "context": {"tokens": 10, "window": 100}}, kind="cx")
    assert {key: detail[key] for key in NO_CONTEXT} == NO_CONTEXT
    assert state == "MEASURED"


def test_a_cc_ask_ends_with_an_estimated_context_in_the_receipt_and_the_monitor(tmp_path, bridge_h):
    from peerhub.extensions.monitor_read_model import render_monitor_frame
    bseed(bridge_h)
    result = bridge_h.delivery_cycle("b", "s", _cc_target(tmp_path, REAL_CC_STREAM, 0))
    assert result.status == "delivered"
    with bridge_h.cs.transaction() as conn:
        detail = json.loads(conn.execute("SELECT detail FROM bridge_evidence WHERE kind='session_telemetry'").fetchone()[0])
        stored = conn.execute("SELECT result_json FROM bridge_deliveries").fetchone()[0]
    assert (detail["last_input_tokens"], detail["context_window"], detail["context_pct"]) == (25_848, 1_000_000, 2)
    assert "contextWindow" not in stored and "provider_telemetry" not in stored
    frame = render_monitor_frame(bridge_h.db_path, ["context", "peers"], read_at=detail["observed_at"] + 60)
    assert frame["groups"]["context"]["data"]["sessions"][0]["context"] == {
        "occupancy_tokens": 25_848, "window_tokens": 1_000_000, "percent": 2, "source": "cc.stream.context",
        "observed_at": detail["observed_at"], "window_reported_at": detail["observed_at"],
        "state": "ESTIMATED", "reason": None}
    (peer,) = [item for item in frame["groups"]["peers"]["data"]["peers"] if item["peer_id"] == "b"]
    assert peer["context"] == {"percent": 2, "occupancy_tokens": 25_848, "window_tokens": 1_000_000,
                               "observed_at": detail["observed_at"], "state": "ESTIMATED"}
