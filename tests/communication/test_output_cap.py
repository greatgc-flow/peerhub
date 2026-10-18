"""Tests for output cap drain-and-truncate and bounded incremental parser (PR 2).

Covers:
- >1 MB stdout flood with a valid completion at the end -> TERMINAL with output_truncated: True
- completion beyond cap is captured and parsed
- refusal beyond cap is seen and raises RuntimeTargetError (uncertain, STARTED)
- oversized single event (>256KB) bounded, marked oversized, rejected as completion evidence
- malformed event does not crash parser and is rejected
- stderr flood (>1MB) drains without kill, marks output_truncated: True, settles valid stdout completion
- both pipes flooding (>1MB each) does not deadlock, drains cleanly, settles TERMINAL
- continuous output hitting total timeout (timeout_s deadline not starved)
- silence timeout triggers when process goes silent
- BoundedProcess head+tail retention policy (exact bounds and middle discarding)
"""
from __future__ import annotations

import json
import os
import sys
import textwrap
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.platform_sensitive

from peerhub.extensions.adapters import CliRuntimeTarget
from peerhub.extensions.adapters.base import IncrementalEventParser
from peerhub.extensions.adapters.process import BoundedProcess, ProcessResult, run_bounded
from peerhub.extensions.bridge import RuntimeTargetError
from tests.communication.bridge_helpers import bseed, records, responses
from tests.communication.harness.bridge import BridgeHarness

PY = sys.executable


def _record(body: str = "test prompt", rid: str = "rec-cap-1"):
    return SimpleNamespace(record_id=rid, body=body, author_peer_id="a")


def _drain(gen):
    return list(gen)


# =====================================================================
# 1. BoundedProcess Direct Unit Tests: Head + Tail Policy & Deadlock
# =====================================================================


def test_bounded_process_head_tail_policy(tmp_path):
    """When output exceeds max_bytes, retained buffer is at most max_bytes via head+tail policy."""
    # Sub-case A: output <= max_bytes -> complete, output_truncated is False
    small = run_bounded(
        [PY, "-c", "import sys; sys.stdout.write('ABCDEFGHIJ')"],
        stdin=None,
        cwd=str(tmp_path),
        env={},
        max_bytes=100,
    )
    assert small.stdout == b"ABCDEFGHIJ"
    assert small.output_truncated is False
    assert small.returncode == 0

    # Sub-case B: output > max_bytes -> at most max_bytes, head + tail kept, middle discarded
    # max_bytes = 100 -> head_limit = 50, tail_limit = 50.
    # Total written = 250 bytes: 50 'H's, 150 'M's, 50 'T's.
    script = (
        "import sys; "
        "sys.stdout.write('H' * 50 + 'M' * 150 + 'T' * 50); "
        "sys.stdout.flush()"
    )
    res = run_bounded(
        [PY, "-c", script],
        stdin=None,
        cwd=str(tmp_path),
        env={},
        max_bytes=100,
    )
    assert res.returncode == 0
    assert len(res.stdout) == 100
    assert res.output_truncated is True
    assert res.stdout[:50] == b"H" * 50
    assert res.stdout[50:] == b"T" * 50
    assert b"M" not in res.stdout


def test_bounded_process_stderr_flood(tmp_path):
    """Stderr flood (>1MB) drains to EOF without kill, output_truncated is True."""
    # Write 1.2 MB to stderr and a small message to stdout
    script = textwrap.dedent("""
        import sys
        chunk = "E" * 65536
        for _ in range(20):  # 20 * 64KB = ~1.3MB
            sys.stderr.write(chunk)
        sys.stderr.flush()
        sys.stdout.write("stdout-ok")
        sys.stdout.flush()
    """)
    res = run_bounded(
        [PY, "-c", script],
        stdin=None,
        cwd=str(tmp_path),
        env={},
        max_bytes=100_000,
        timeout_s=10.0,
    )
    assert res.returncode == 0
    assert res.stdout == b"stdout-ok"
    assert len(res.stderr) == 100_000
    assert res.output_truncated is True


def test_bounded_process_both_pipes_flooding_no_deadlock(tmp_path):
    """Flooding both stdout and stderr concurrently with >1MB each does not deadlock."""
    script = textwrap.dedent("""
        import sys, threading
        def flood_err():
            chunk = "E" * 65536
            for _ in range(20):
                sys.stderr.write(chunk)
            sys.stderr.flush()

        t = threading.Thread(target=flood_err)
        t.start()
        chunk = "O" * 65536
        for _ in range(20):
            sys.stdout.write(chunk)
        sys.stdout.flush()
        t.join()
    """)
    t0 = time.monotonic()
    res = run_bounded(
        [PY, "-c", script],
        stdin=None,
        cwd=str(tmp_path),
        env={},
        max_bytes=100_000,
        timeout_s=15.0,
    )
    elapsed = time.monotonic() - t0
    assert elapsed < 10.0, f"dual flood took too long ({elapsed}s), possible deadlock"
    assert res.returncode == 0
    assert len(res.stdout) == 100_000
    assert len(res.stderr) == 100_000
    assert res.output_truncated is True


def test_continuous_output_hits_total_timeout(tmp_path):
    """Continuous output loop does not starve deadline checks; hits timeout_s."""
    script = textwrap.dedent("""
        import sys, time
        while True:
            sys.stdout.write("flood\\n")
            sys.stdout.flush()
            time.sleep(0.001)
    """)
    proc = BoundedProcess(
        [PY, "-c", script],
        stdin=None,
        cwd=str(tmp_path),
        env={},
        max_bytes=50_000,
        timeout_s=0.5,
    ).start()
    res = proc.wait()
    assert res.timed_out is True
    assert res.termination_confirmed is True
    assert len(res.stdout) <= 50_000


def test_silence_timeout_triggers(tmp_path):
    """Process producing initial output then hanging hits silence timeout."""
    script = textwrap.dedent("""
        import sys, time
        sys.stdout.write("starting\\n")
        sys.stdout.flush()
        time.sleep(30)
    """)
    proc = BoundedProcess(
        [PY, "-c", script],
        stdin=None,
        cwd=str(tmp_path),
        env={},
        timeout_s=10.0,
        silence_timeout_s=0.3,
    ).start()
    res = proc.wait()
    assert res.silence_timed_out is True
    assert res.termination_confirmed is True


# =====================================================================
# 2. Adapter Delivery Tests: Flood, Cap, Completion, Refusal, Events
# =====================================================================


def test_stdout_flood_with_valid_completion_settles_terminal(tmp_path, bridge_h):
    """>1 MB stdout flood followed by a valid completion settles TERMINAL with output_truncated: True."""
    ws = tmp_path / "ws_flood"
    ws.mkdir()
    # Emits 1.2 MB of stdout noise, then a valid cc stream-json result record
    script = textwrap.dedent("""
        import sys, json
        # Flood > 1MB of log lines
        chunk = ("log line " * 10 + "\\n") * 2000  # ~200 KB per block
        for _ in range(6):  # ~1.2 MB
            sys.stdout.write(chunk)
        # Final completion event
        result_ev = {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "result": "task completed successfully after flood",
            "session_id": "sess-flood-1",
            "usage": {"input_tokens": 100, "output_tokens": 50}
        }
        sys.stdout.write(json.dumps(result_ev) + "\\n")
        sys.stdout.flush()
    """)
    target = CliRuntimeTarget(
        "cc",
        ws,
        command=[PY, "-c", script],
        timeout_s=15.0,
        max_bytes=100_000,  # 100KB retained cap, total output ~1.2MB
    )

    (rec,) = bseed(bridge_h, stream="s_flood")
    res = bridge_h.delivery_cycle("b", "s_flood", target)

    # 1. Delivery settled as TERMINAL (not killed by output volume!)
    assert res.status == "delivered"
    assert res.certainty == "TERMINAL"

    # 2. Response record created with expected response body and output_truncated metadata
    resps = responses(bridge_h, "s_flood")
    assert len(resps) == 1
    resp_body = json.loads(resps[0][4])
    assert resp_body == "task completed successfully after flood"

    # 3. Durable evidence details carry output_truncated: True
    ev = bridge_h.execution_evidence(rec.record_id, "b")
    term_evs = [e for e in ev["events"] if e["kind"] == "terminal"]
    assert len(term_evs) == 1
    assert term_evs[0]["detail"].get("output_truncated") is True

    # 4. Exited evidence carried output_truncated: True
    exited_ev = next(e for e in target.evidence if e["event"] == "exited")
    assert exited_ev.get("output_truncated") is True


def test_completion_beyond_cap_incremental_parsing(tmp_path):
    """Completion and usage events beyond the retained window are captured via incremental parsing."""
    ws = tmp_path / "ws_beyond"
    ws.mkdir()
    # Codex format: 1.5MB flood, then item.completed and turn.completed beyond cap
    script = textwrap.dedent("""
        import sys, json
        # 1. Emit thread.started
        sys.stdout.write(json.dumps({"type": "thread.started", "thread_id": "th-beyond-1"}) + "\\n")
        # 2. Flood 1.5MB of filler
        filler = ("x" * 1000 + "\\n") * 1500  # 1.5 MB
        sys.stdout.write(filler)
        # 3. Item completed and turn completed beyond cap
        sys.stdout.write(json.dumps({
            "type": "item.completed",
            "item": {"type": "agent_message", "text": "agent message beyond 1MB"}
        }) + "\\n")
        sys.stdout.write(json.dumps({
            "type": "turn.completed",
            "usage": {"input_tokens": 500, "cached_input_tokens": 100, "cache_write_input_tokens": 50, "output_tokens": 80}
        }) + "\\n")
        sys.stdout.flush()
    """)
    target = CliRuntimeTarget(
        "cx",
        ws,
        command=[PY, "-c", script],
        timeout_s=15.0,
        max_bytes=100_000,
    )
    sess = target.create_session()
    events = list(target.deliver(sess, _record(), []))

    assert [e[0] for e in events] == ["started", "terminal"]
    terminal_payload = events[1][1]
    assert terminal_payload["response"] == "agent message beyond 1MB"
    assert terminal_payload.get("output_truncated") is True
    assert terminal_payload.get("usage") == {
        "input_tokens": 500,
        "cached_input_tokens": 100,
        "cache_write_tokens": 50,
        "output_tokens": 80,
    }
    assert terminal_payload.get("vendor_session") == {"id": "th-beyond-1", "source": "cx.thread.started"}


def test_refusal_beyond_cap_classified(tmp_path, bridge_h):
    """Refusal event emitted beyond the cap is captured and raises RuntimeTargetError."""
    ws = tmp_path / "ws_refuse"
    ws.mkdir()
    # 1.5MB flood, then turn.failed beyond cap
    script = textwrap.dedent("""
        import sys, json
        sys.stdout.write(json.dumps({"type": "thread.started", "thread_id": "th-refuse-1"}) + "\\n")
        filler = ("x" * 1000 + "\\n") * 1500
        sys.stdout.write(filler)
        sys.stdout.write(json.dumps({"type": "turn.failed", "error": {"message": "You hit your usage limit"}}) + "\\n")
        sys.stdout.flush()
    """)
    target = CliRuntimeTarget(
        "cx",
        ws,
        command=[PY, "-c", script],
        timeout_s=15.0,
        max_bytes=100_000,
    )

    (rec,) = bseed(bridge_h, stream="s_refuse")
    res = bridge_h.delivery_cycle("b", "s_refuse", target)

    # Must NOT settle as TERMINAL; must be uncertain with STARTED certainty
    assert res.status == "uncertain"
    assert res.certainty == "STARTED"
    assert responses(bridge_h, "s_refuse") == []

    # Direct deliver() raises RuntimeTargetError mentioning the failure
    sess = target.create_session()
    with pytest.raises(RuntimeTargetError, match="turn.failed"):
        _drain(target.deliver(sess, _record(), []))


def test_parser_bounds_oversized_events(tmp_path):
    """A single event >256KB is bounded, marked oversized, and rejected as completion evidence."""
    ws = tmp_path / "ws_oversized"
    ws.mkdir()
    # Emit a single 300KB line that starts looking like JSON but exceeds 256KB
    script = textwrap.dedent("""
        import sys, json
        # 300 KB line without newline
        sys.stdout.write('{"type": "result", "subtype": "success", "is_error": false, "result": "' + ("A" * 300_000) + '"}\\n')
        sys.stdout.flush()
    """)
    target = CliRuntimeTarget(
        "cc",
        ws,
        command=[PY, "-c", script],
        timeout_s=15.0,
        max_bytes=100_000,
    )
    sess = target.create_session()
    # Oversized event cannot be accepted as valid completion evidence
    with pytest.raises(RuntimeTargetError, match="unparseable cc output"):
        _drain(target.deliver(sess, _record(), []))


def test_malformed_event_fails_safely(tmp_path):
    """Malformed event lines do not crash the parser and are rejected."""
    ws = tmp_path / "ws_malformed"
    ws.mkdir()
    script = textwrap.dedent("""
        import sys
        sys.stdout.write("not valid json at all {{{{ unclosed\\n")
        sys.stdout.write("{ 'invalid': 'single quotes' }\\n")
        sys.stdout.flush()
    """)
    target = CliRuntimeTarget(
        "cc",
        ws,
        command=[PY, "-c", script],
        timeout_s=10.0,
        max_bytes=50_000,
    )
    sess = target.create_session()
    with pytest.raises(RuntimeTargetError, match="unparseable cc output"):
        _drain(target.deliver(sess, _record(), []))


# =====================================================================
# 3. Regression Tests for Findings P1/P2
# =====================================================================


def test_cumulative_parser_state_bounded_and_marked_truncated():
    """P1 base.py:214: Parser bounds cumulative message state and result/session/error lists, marking truncated."""
    parser = IncrementalEventParser("cx", max_bytes=1000)

    # Feed 2000 small agent_message events (~120KB total text)
    for i in range(2000):
        ev = {"type": "item.completed", "item": {"type": "agent_message", "text": f"small message chunk {i}"}}
        parser.feed((json.dumps(ev) + "\n").encode("utf-8"))

    # Complete the turn
    parser.feed(b'{"type": "turn.completed"}\n')
    parser.close()

    # 1. Truncated flag set
    assert parser.truncated is True

    # 2. Cumulative messages text is strictly bounded around max_bytes
    joined = parser.parse()
    assert len(joined.encode("utf-8")) <= 2000  # far less than the ~120KB emitted
    assert "small message chunk 0" in joined  # head preserved
    assert "small message chunk 1999" in joined  # tail preserved

    # 3. Error and result lists are strictly bounded
    p_err = IncrementalEventParser("cx", max_bytes=1000)
    for _ in range(100):
        p_err.feed(b'{"type": "error"}\n')
    assert len(p_err.cx_errors) <= 10

    p_cc = IncrementalEventParser("cc", max_bytes=1000)
    for _ in range(100):
        p_cc.feed(b'{"type": "result", "subtype": "success", "is_error": false, "result": "ok"}\n')
    assert len(p_cc.cc_results) <= 10


def test_stream_stdout_pending_capped_on_newline_free_output(tmp_path):
    """P1 base.py:692: stream_stdout.pending is capped when newline-free flood exceeds max_bytes."""
    ws = tmp_path / "ws_pending_cap"
    ws.mkdir()
    # Emits 100KB of continuous newline-free 'X's (>> 1KiB cap, < 256KB event cap),
    # then a newline, then a valid result record
    script = textwrap.dedent("""
        import sys, json
        sys.stdout.write("X" * 100_000)
        sys.stdout.write("\\n")
        # Valid CC result record
        result_ev = {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "result": "completed after newline-free flood",
            "session_id": "sess-pending-1",
        }
        sys.stdout.write(json.dumps(result_ev) + "\\n")
        sys.stdout.flush()
    """)
    received_stream_chunks: list[str] = []
    target = CliRuntimeTarget(
        "cc",
        ws,
        command=[PY, "-c", script],
        timeout_s=15.0,
        max_bytes=1024,  # 1 KiB cap vs 100KB output
        on_output=received_stream_chunks.append,
    )
    sess = target.create_session()
    events = list(target.deliver(sess, _record(), []))
    assert [e[0] for e in events] == ["started", "terminal"]
    assert events[1][1]["response"] == "completed after newline-free flood"
    assert events[1][1].get("output_truncated") is True


def test_display_failure_isolated_from_evidence_parsing(tmp_path, bridge_h):
    """P1 process.py:154: BrokenPipeError in display callback does not disable evidence parsing."""
    ws = tmp_path / "ws_broken_pipe"
    ws.mkdir()
    # Emits early output that triggers on_output, then completion event
    script = textwrap.dedent("""
        import sys, json, time
        first_ev = {
            "type": "assistant",
            "message": {"content": [{"type": "text", "text": "initial stream text"}]}
        }
        sys.stdout.write(json.dumps(first_ev) + "\\n")
        sys.stdout.flush()
        time.sleep(0.05)
        result_ev = {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "result": "task completed despite broken pipe in display",
            "session_id": "sess-bp-1",
        }
        sys.stdout.write(json.dumps(result_ev) + "\\n")
        sys.stdout.flush()
    """)

    def failing_display(text: str) -> None:
        raise BrokenPipeError("downstream pipe closed")

    target = CliRuntimeTarget(
        "cc",
        ws,
        command=[PY, "-c", script],
        timeout_s=15.0,
        max_bytes=50_000,
        on_output=failing_display,
    )
    (rec,) = bseed(bridge_h, stream="s_bp")
    res = bridge_h.delivery_cycle("b", "s_bp", target)

    assert res.status == "delivered"
    assert res.certainty == "TERMINAL"
    resps = responses(bridge_h, "s_bp")
    assert len(resps) == 1
    assert json.loads(resps[0][4]) == "task completed despite broken pipe in display"


def test_oversized_record_invalidates_earlier_success(tmp_path):
    """P1 base.py:150: Oversized record invalidates earlier success; outcome is marked indeterminate/error."""
    # 1. Direct unit test on IncrementalEventParser
    parser = IncrementalEventParser("cc")
    # Small success record first
    parser.feed(b'{"type": "result", "subtype": "success", "is_error": false, "result": "early success"}\\n')
    # Followed by oversized 300KB line
    parser.feed(b'{"type": "error", "message": "' + (b"E" * 300_000) + b'"}\\n')
    parser.close()
    assert parser.oversized_count > 0
    with pytest.raises(ValueError, match="oversized event encountered"):
        parser.parse()
    assert parser.parse_session_id() is None
    assert parser.parse_usage() is None

    # 2. Integration test via CliRuntimeTarget delivery
    ws = tmp_path / "ws_oversized_inval"
    ws.mkdir()
    script = textwrap.dedent("""
        import sys, json
        # Success first
        sys.stdout.write(json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": "done"}) + "\\n")
        # Then 300KB line
        sys.stdout.write('{"type": "error", "data": "' + ("X" * 300_000) + '"}\\n')
        sys.stdout.flush()
    """)
    target = CliRuntimeTarget("cc", ws, command=[PY, "-c", script], timeout_s=10.0, max_bytes=50_000)
    sess = target.create_session()
    with pytest.raises(RuntimeTargetError, match="unparseable cc output"):
        _drain(target.deliver(sess, _record(), []))


def test_ag_stderr_overflow_preserves_multiline_json_completion(tmp_path, bridge_h):
    """P2 base.py:741: When only stderr overflows, valid multiline AG JSON completes cleanly."""
    ws = tmp_path / "ws_ag_stderr"
    ws.mkdir()
    # Emits 150KB to stderr (exceeding 50KB max_bytes), and multiline formatted JSON to stdout
    script = textwrap.dedent("""
        import sys
        chunk = "E" * 1024
        for _ in range(150):  # 150 KB to stderr
            sys.stderr.write(chunk)
        sys.stderr.flush()
        # Pretty-printed multiline AG response (not single-line JSONL)
        sys.stdout.write('''{
          "response": "valid multiline ag response text",
          "conversation_id": "conv-ag-overflow-1",
          "usage": {
            "input_tokens": 120,
            "output_tokens": 45
          }
        }
        ''')
        sys.stdout.flush()
    """)
    target = CliRuntimeTarget("ag", ws, command=[PY, "-c", script], timeout_s=15.0, max_bytes=50_000, writable=True)
    (rec,) = bseed(bridge_h, stream="s_ag_stderr")
    res = bridge_h.delivery_cycle("b", "s_ag_stderr", target)

    assert res.status == "delivered"
    assert res.certainty == "TERMINAL"
    resps = responses(bridge_h, "s_ag_stderr")
    assert len(resps) == 1
    assert json.loads(resps[0][4]) == "valid multiline ag response text"

    # Evidence carries output_truncated: True because stderr overflowed
    ev = bridge_h.execution_evidence(rec.record_id, "b")
    term_evs = [e for e in ev["events"] if e["kind"] == "terminal"]
    assert len(term_evs) == 1
    assert term_evs[0]["detail"].get("output_truncated") is True
    assert term_evs[0]["detail"].get("usage") == {"input_tokens": 120, "output_tokens": 45}


def test_conflicting_session_ids_enforces_consistency_no_binding():
    """P2 base.py:280: Conflicting session IDs across events are rejected as ambiguous (no binding)."""
    # 1. cx provider with conflicting thread IDs
    p_cx_conflict = IncrementalEventParser("cx")
    p_cx_conflict.feed(b'{"type": "thread.started", "thread_id": "th-first"}\n')
    p_cx_conflict.feed(b'{"type": "item.completed", "thread_id": "th-second", "item": {"type": "agent_message", "text": "hello"}}\n')
    p_cx_conflict.feed(b'{"type": "turn.completed"}\n')
    p_cx_conflict.close()
    assert p_cx_conflict.parse_session_id() is None  # ambiguous: last did not win

    # Consistent cx thread IDs
    p_cx_ok = IncrementalEventParser("cx")
    p_cx_ok.feed(b'{"type": "thread.started", "thread_id": "th-same"}\n')
    p_cx_ok.feed(b'{"type": "item.completed", "thread_id": "th-same", "item": {"type": "agent_message", "text": "hello"}}\n')
    p_cx_ok.feed(b'{"type": "turn.completed"}\n')
    p_cx_ok.close()
    assert p_cx_ok.parse_session_id() == "th-same"

    # 2. cc provider with conflicting session IDs
    p_cc_conflict = IncrementalEventParser("cc")
    p_cc_conflict.feed(b'{"type": "assistant", "session_id": "sess-alpha"}\n')
    p_cc_conflict.feed(b'{"type": "result", "session_id": "sess-beta", "subtype": "success", "is_error": false, "result": "ok"}\n')
    p_cc_conflict.close()
    assert p_cc_conflict.parse_session_id() is None  # ambiguous: last did not win

    # Consistent cc session IDs
    p_cc_ok = IncrementalEventParser("cc")
    p_cc_ok.feed(b'{"type": "assistant", "session_id": "sess-consistent"}\n')
    p_cc_ok.feed(b'{"type": "result", "session_id": "sess-consistent", "subtype": "success", "is_error": false, "result": "ok"}\n')
    p_cc_ok.close()
    assert p_cc_ok.parse_session_id() == "sess-consistent"

    # 3. ag provider with conflicting conversation IDs
    p_ag_conflict = IncrementalEventParser("ag")
    p_ag_conflict.feed(b'{"response": "part 1", "conversation_id": "conv-1"}\n')
    p_ag_conflict.feed(b'{"response": "part 2", "conversation_id": "conv-2"}\n')
    p_ag_conflict.close()
    assert p_ag_conflict.parse_session_id() is None


# =====================================================================
# 4. Regression Tests for Round 2 Findings (P1/P2)
# =====================================================================


def test_empty_cx_agent_messages_cost_charged_against_cap():
    """P1 base.py:166: Empty CX agent messages must not grow list unbounded (charged min bytes / count capped)."""
    parser = IncrementalEventParser("cx", max_bytes=1024)
    # Feed 200,000 empty agent_message events (previously 0 bytes each -> 200,000 entries stored)
    ev_bytes = b'{"type": "item.completed", "item": {"type": "agent_message", "text": ""}}\n'
    for _ in range(200_000):
        parser.feed(ev_bytes)
    parser.feed(b'{"type": "turn.completed"}\n')
    parser.close()

    assert parser.truncated is True
    # At 1 KiB cap, total stored messages must be bounded (e.g. <= 1024), never 200,000
    assert len(parser.cx_agent_messages) <= 1024


def test_ag_invalid_toplevel_array_rejected_after_truncation(tmp_path):
    """P2 base.py:853: AG after stdout truncation must reject nested response fragments from top-level array."""
    # 1. Direct unit test on IncrementalEventParser
    p_ag_array = IncrementalEventParser("ag", max_bytes=1024)
    p_ag_array.feed(b'[\n')
    p_ag_array.feed(b'  {"response": "nested in array", "conversation_id": "c-array-1"}\n')
    p_ag_array.feed(b']\n')
    p_ag_array.close()
    with pytest.raises(ValueError, match="well-formed"):
        p_ag_array.parse()
    assert p_ag_array.parse_session_id() is None
    assert p_ag_array.parse_usage() is None

    # 2. Integration test via CliRuntimeTarget delivery:
    # Top-level array containing a response fragment, followed by a large flood that causes truncation.
    ws = tmp_path / "ws_ag_array_trunc"
    ws.mkdir()
    script = textwrap.dedent("""
        import sys, json
        # Emit an invalid top-level array
        sys.stdout.write("[\\n")
        sys.stdout.write(json.dumps({"response": "fragment", "conversation_id": "untrusted-conv"}) + "\\n")
        sys.stdout.write("]\\n")
        # Followed by a flood exceeding max_bytes to force truncation
        sys.stdout.write("P" * 50_000)
        sys.stdout.flush()
    """)
    target = CliRuntimeTarget("ag", ws, command=[PY, "-c", script], timeout_s=15.0, max_bytes=1024, writable=True)
    sess = target.create_session()
    # Must raise RuntimeTargetError and must NEVER yield a terminal event or bind untrusted session ID
    with pytest.raises(RuntimeTargetError, match="unparseable ag output"):
        list(target.deliver(sess, _record(), []))


def test_blocking_display_callback_does_not_mask_later_error(tmp_path):
    """P1 process.py:163: Blocking display callback must not cause later process error to be discarded."""
    ws = tmp_path / "ws_slow_display"
    ws.mkdir()
    # Process emits early CC result event (which looks like a complete successful response),
    # then sleeps 0.2s, then emits a fatal error event or non-zero crash.
    script = textwrap.dedent("""
        import sys, json, time
        first_ev = {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "result": "early success",
            "session_id": "sess-slow-1",
        }
        sys.stdout.write(json.dumps(first_ev) + "\\n")
        sys.stdout.flush()
        time.sleep(0.2)
        # Followed by a subsequent error event
        err_ev = {"type": "result", "subtype": "error", "is_error": True, "error": "fatal later error"}
        sys.stdout.write(json.dumps(err_ev) + "\\n")
        sys.stdout.flush()
    """)

    def slow_display(chunk: str) -> None:
        # Blocks display for 6.0 seconds (longer than the 5.0s thread join timeout)
        time.sleep(6.0)

    target = CliRuntimeTarget(
        "cc",
        ws,
        command=[PY, "-c", script],
        timeout_s=15.0,
        max_bytes=50_000,
        on_output=slow_display,
    )
    sess = target.create_session()
    # Evidence parsing must NOT return earlier partial success as terminal.
    # It must either observe the later error (RuntimeTargetError) or fail indeterminate on join timeout.
    with pytest.raises(RuntimeTargetError):
        list(target.deliver(sess, _record(), []))


def test_cli_app_ask_streaming_does_not_retain_chunks(tmp_path, monkeypatch, capsys):
    """P1 cli/app.py:187: CLI ask streaming must not retain every streamed chunk in a list."""
    import inspect
    from peerhub.cli import app
    from tests.communication.fakes import FakeRuntimeTarget

    # Inspect source of _run_ask_cli to ensure no unbounded streamed list is used
    src = inspect.getsource(app._run_ask_cli)
    assert "streamed: list" not in src
    assert "streamed.append" not in src

    # Also test functionally that streaming works
    from peerhub import cli
    from peerhub.extensions import ask as ask_mod
    rt = FakeRuntimeTarget()
    rt.script_deliver(("started", "exec-stream"), ("terminal", {"response": "done"}))
    real_ask = ask_mod.ask
    monkeypatch.setattr(ask_mod, "ask", lambda *a, **kw: real_ask(*a, **kw, runtime=rt))
    assert cli.main(["--db", str(tmp_path / "core.db"), "ask", "cx", "hello"]) == 0
    captured = capsys.readouterr()
    assert "done" in captured.out


# =====================================================================
# 5. Regression Tests for Round 3 Findings (P1/P2)
# =====================================================================


def test_ag_nested_response_in_wrapper_rejected_after_truncation(tmp_path):
    """P2 base.py:240: AG after truncation must reject nested response in wrapper; only top-level object counts."""
    # 1. Direct unit test on IncrementalEventParser
    p = IncrementalEventParser("ag", max_bytes=1000)
    nested_json = b'{"wrapper":\n{"response":"nested","conversation_id":"id"}\n}'
    p.feed(nested_json)
    # Plus whitespace over the cap
    p.feed(b" " * 2000)
    p.close()
    assert p.ag_invalid_top_level is True
    with pytest.raises(ValueError, match="well-formed"):
        p.parse()
    assert p.parse_session_id() is None
    assert p.parse_usage() is None

    # 2. Integration test via CliRuntimeTarget
    ws = tmp_path / "ws_ag_nested_wrapper"
    ws.mkdir()
    script = textwrap.dedent("""
        import sys
        sys.stdout.write('{"wrapper":\\n{"response":"nested","conversation_id":"id"}\\n}')
        sys.stdout.write(" " * 50_000)
        sys.stdout.flush()
    """)
    target = CliRuntimeTarget("ag", ws, command=[PY, "-c", script], timeout_s=15.0, max_bytes=1024, writable=True)
    sess = target.create_session()
    with pytest.raises(RuntimeTargetError, match="unparseable ag output"):
        list(target.deliver(sess, _record(), []))


def test_display_queue_bounded_and_drops_without_blocking_evidence(tmp_path):
    """P1 process.py:183: Blocked display callback must not retain unbounded data; bounded, drops, sets display_truncated."""
    import threading

    # Emits 200KB of output at 1 KiB cap
    script = textwrap.dedent("""
        import sys
        chunk = "D" * 1024
        for _ in range(200):
            sys.stdout.write(chunk)
        sys.stdout.flush()
    """)
    block_evt = threading.Event()
    received_in_display: list[bytes] = []
    received_in_evidence: list[bytes] = []

    def blocked_display(chunk: bytes) -> None:
        received_in_display.append(chunk)
        block_evt.wait(timeout=2.0)

    def evidence_callback(chunk: bytes) -> None:
        received_in_evidence.append(chunk)

    proc = BoundedProcess(
        [PY, "-c", script],
        stdin=None,
        cwd=str(tmp_path),
        env={},
        max_bytes=1024,
        timeout_s=10.0,
        on_stdout=evidence_callback,
        on_display=blocked_display,
    ).start()

    res = proc.wait()
    block_evt.set()

    assert res.returncode == 0
    # Evidence was not blocked: received all 200KB
    assert sum(len(c) for c in received_in_evidence) == 200 * 1024
    # Display queue bounded: display_truncated is True
    assert res.display_truncated is True
    assert proc.display_truncated is True
    # The queue bytes retained did not balloon to 200KB (bounded around 1 KiB)
    assert proc._display_bytes <= 4096


def test_cx_message_order_head_stops_accepting_after_tail_retention():
    """P2 base.py:172: CX message order: cap 1000, A(400) B(300) C(100) -> A,B,C (head stops after tail retention starts)."""
    parser = IncrementalEventParser("cx", max_bytes=1000)
    a = "A" * 400
    b = "B" * 300
    c = "C" * 100

    for text in (a, b, c):
        ev = {"type": "item.completed", "item": {"type": "agent_message", "text": text}}
        parser.feed((json.dumps(ev) + "\n").encode("utf-8"))

    parser.feed(b'{"type": "turn.completed"}\n')
    parser.close()

    assert parser.truncated is True
    # Must preserve chronological order [A, B, C], NEVER [A, C, B]
    assert parser.cx_agent_messages == [a, b, c]


# =====================================================================
# 6. Regression Tests for Round 4 Findings (P2)
# =====================================================================


def test_parser_exception_latched_makes_outcome_indeterminate_never_terminal(tmp_path, monkeypatch):
    """P2 process.py:191: Exception in parser must latch parser failure and make outcome indeterminate (never terminal success)."""
    # 1. Direct unit test on IncrementalEventParser
    p = IncrementalEventParser("cc", max_bytes=1000)
    # Feed early success
    p.feed(b'{"type": "result", "subtype": "success", "is_error": false, "result": "early success", "session_id": "sess-latched-1"}\n')
    # Monkeypatch json.loads to raise RecursionError when encountering "deeply_nested"
    real_loads = json.loads

    def loads_with_recursion_error(s, *args, **kwargs):
        if "deeply_nested" in s:
            raise RecursionError("maximum recursion depth exceeded")
        return real_loads(s, *args, **kwargs)

    monkeypatch.setattr(json, "loads", loads_with_recursion_error)
    # Feeding deeply nested line must raise RecursionError and latch parser_failed
    with pytest.raises(RecursionError):
        p.feed(b'{"deeply_nested": true}\n')
    assert p.parser_failed is True
    # parse() must raise ValueError, parse_session_id and parse_usage must return None
    with pytest.raises(ValueError, match="parser failure encountered; outcome indeterminate"):
        p.parse()
    assert p.parse_session_id() is None
    assert p.parse_usage() is None

    # 2. Integration test via CliRuntimeTarget.deliver
    ws = tmp_path / "ws_parser_exception"
    ws.mkdir()
    script = textwrap.dedent("""
        import sys, json
        # Emit early success
        sys.stdout.write(json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": "early success", "session_id": "sess-latched-1"}) + "\\n")
        # Emit deeply nested JSON that triggers RecursionError in the parser
        sys.stdout.write(json.dumps({"deeply_nested": True}) + "\\n")
        # Followed by fatal error result
        sys.stdout.write(json.dumps({"type": "result", "subtype": "error", "is_error": True, "error": "fatal later error"}) + "\\n")
        # Followed by flood exceeding max_bytes to force truncation
        sys.stdout.write("P" * 50_000)
        sys.stdout.flush()
    """)
    target = CliRuntimeTarget("cc", ws, command=[PY, "-c", script], timeout_s=15.0, max_bytes=1024)
    sess = target.create_session()
    # Must raise RuntimeTargetError (uncertain outcome); must NEVER yield a terminal success with "early success"
    with pytest.raises(RuntimeTargetError, match="parser failed"):
        list(target.deliver(sess, _record(), []))


def test_cli_app_ask_prints_final_response_when_display_truncated(tmp_path, monkeypatch, capsys):
    """P2 cli/app.py:198: Final response suppressed whenever anything streamed: print final response when display was truncated."""
    from peerhub import cli
    from peerhub.extensions import ask as ask_mod
    from tests.communication.fakes import FakeRuntimeTarget

    rt = FakeRuntimeTarget()
    # Script deliver with display_truncated: True in terminal result
    rt.script_deliver(
        ("started", "exec-stream-trunc"),
        ("terminal", {"response": "FINAL ANSWER", "display_truncated": True}),
    )
    real_ask = ask_mod.ask

    def fake_ask(*args, **kwargs):
        on_out = kwargs.get("on_output")
        if on_out is not None:
            on_out("WORKING...")
        return real_ask(*args, **kwargs, runtime=rt)

    monkeypatch.setattr(ask_mod, "ask", fake_ask)
    assert cli.main(["--db", str(tmp_path / "core.db"), "ask", "cx", "hello"]) == 0
    captured = capsys.readouterr()
    assert "WORKING..." in captured.out
    assert "FINAL ANSWER" in captured.out


def test_ask_slow_display_queue_overflow_propagates_display_truncated(tmp_path):
    """P2 cli/app.py:198: Real process with slow display queue overflow sets display_truncated on target.deliver."""
    ws = tmp_path / "ws_slow_disp_overflow"
    ws.mkdir()
    script = textwrap.dedent("""
        import sys, json, time
        # Emits working message
        sys.stdout.write(json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "WORKING..."}}) + "\\n")
        sys.stdout.flush()
        # Flood stdout (>50KB at 1KB max_bytes) to overflow display queue
        sys.stdout.write("X" * 50_000 + "\\n")
        sys.stdout.flush()
        # Final answer message
        sys.stdout.write(json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "FINAL ANSWER"}}) + "\\n")
        sys.stdout.write(json.dumps({"type": "turn.completed"}) + "\\n")
        sys.stdout.flush()
    """)
    displayed: list[str] = []

    def slow_display(text: str) -> None:
        displayed.append(text)
        time.sleep(0.05)  # slow display causes display queue to overflow

    target = CliRuntimeTarget("cx", ws, command=[PY, "-c", script], timeout_s=15.0, max_bytes=1024, on_output=slow_display)
    events = list(target.deliver(target.create_session(), _record(), []))
    assert any(ev[0] == "terminal" for ev in events)
    term_payload = next(ev[1] for ev in events if ev[0] == "terminal")
    assert term_payload["display_truncated"] is True
    assert "FINAL ANSWER" in term_payload["response"]


# =====================================================================
# 7. Regression Tests for Round 5 Findings (P2)
# =====================================================================


def test_display_event_exceeding_max_bytes_sets_display_truncated(tmp_path):
    """P2 base.py:888: Display event larger than max_bytes is dropped; sets display_truncated so final answer is not suppressed."""
    ws = tmp_path / "ws_disp_event_max_bytes"
    ws.mkdir()
    # Emits WORKING, then a large event split into two flushes (800 + 800 bytes at max_bytes=1000).
    # BoundedProcess display queue drains the first 800 bytes, so proc does NOT flag display_truncated.
    # But base.py stream_stdout accumulates pending (800 + 800 = 1600 > 1000) and drops the event.
    script = textwrap.dedent("""
        import sys, json, time
        sys.stdout.write(json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "WORKING..."}}) + "\\n")
        sys.stdout.flush()
        time.sleep(0.05)
        # Large event split across two flushes without newline in between
        p1 = '{"type": "item.completed", "item": {"type": "agent_message", "text": "' + ('A' * 700)
        sys.stdout.write(p1)
        sys.stdout.flush()
        time.sleep(0.05)
        p2 = ('A' * 700) + '"}}\\n'
        sys.stdout.write(p2)
        sys.stdout.write(json.dumps({"type": "turn.completed"}) + "\\n")
        sys.stdout.flush()
    """)
    streamed: list[str] = []
    target = CliRuntimeTarget("cx", ws, command=[PY, "-c", script], timeout_s=15.0, max_bytes=1000, on_output=streamed.append)
    sess = target.create_session()
    events = list(target.deliver(sess, _record(), []))
    assert any(ev[0] == "terminal" for ev in events)
    term_payload = next(ev[1] for ev in events if ev[0] == "terminal")
    assert "WORKING..." in streamed
    # base.py dropped the large event when pending (800) + part (800) > 1000
    assert term_payload.get("display_truncated") is True



def test_display_event_newline_free_overflow_sets_display_truncated(tmp_path):
    """P2 base.py:888: Newline-free chunk overflow in stream_stdout sets display_truncated whenever data is dropped."""
    ws = tmp_path / "ws_disp_newline_free"
    ws.mkdir()
    script = textwrap.dedent("""
        import sys, json, time
        # 1. Early streamed event
        sys.stdout.write(json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "WORKING..."}}) + "\\n")
        sys.stdout.flush()
        time.sleep(0.05)
        # 2. Newline-free junk split into two flushes (600 + 600 bytes at max_bytes=1000)
        sys.stdout.write("J" * 600)
        sys.stdout.flush()
        time.sleep(0.05)
        sys.stdout.write("J" * 600)
        sys.stdout.write("\\n")
        sys.stdout.flush()
        # 3. Completed turn
        sys.stdout.write(json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "FINAL ANSWER"}}) + "\\n")
        sys.stdout.write(json.dumps({"type": "turn.completed"}) + "\\n")
        sys.stdout.flush()
    """)
    streamed: list[str] = []
    target = CliRuntimeTarget("cx", ws, command=[PY, "-c", script], timeout_s=15.0, max_bytes=1000, on_output=streamed.append)
    sess = target.create_session()
    events = list(target.deliver(sess, _record(), []))
    assert any(ev[0] == "terminal" for ev in events)
    term_payload = next(ev[1] for ev in events if ev[0] == "terminal")
    assert term_payload.get("display_truncated") is True



def test_display_thread_alive_after_join_sets_display_truncated(tmp_path):
    """P2 process.py:318: Display thread not finished by 0.1s join sets display_truncated."""
    import threading

    block_evt = threading.Event()

    def slow_display(chunk: bytes) -> None:
        block_evt.wait(timeout=0.5)

    proc = BoundedProcess(
        [PY, "-c", "import sys; sys.stdout.write('hello\\n')"],
        stdin=None,
        cwd=str(tmp_path),
        env={},
        timeout_s=5.0,
        on_display=slow_display,
    ).start()
    res = proc.wait()
    block_evt.set()

    assert res.display_truncated is True
    assert proc.display_truncated is True


def test_cli_app_ask_prints_final_answer_when_display_thread_slow(tmp_path, monkeypatch, capsys):
    """P2 process.py:318: When display thread does not finish within 0.1s, display_truncated causes CLI to prefer printing final answer."""
    import threading
    from peerhub import cli
    from peerhub.extensions import ask as ask_mod

    ws = tmp_path / "ws_cli_slow_disp"
    ws.mkdir()
    script = textwrap.dedent("""
        import sys, json, time
        sys.stdout.write(json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "WORKING"}}) + "\\n")
        sys.stdout.flush()
        time.sleep(0.05)
        sys.stdout.write(json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "THE FINAL ANSWER"}}) + "\\n")
        sys.stdout.write(json.dumps({"type": "turn.completed"}) + "\\n")
        sys.stdout.flush()
    """)

    block_evt = threading.Event()
    first_chunk = True
    orig_on_output = None

    def slow_on_output(text: str) -> None:
        nonlocal first_chunk
        if first_chunk:
            first_chunk = False
            if orig_on_output is not None:
                orig_on_output(text)
        else:
            block_evt.wait(timeout=5.0)
            if orig_on_output is not None:
                orig_on_output(text)

    target = CliRuntimeTarget("cx", ws, command=[PY, "-c", script], timeout_s=15.0, max_bytes=50_000, on_output=slow_on_output)
    real_ask = ask_mod.ask

    def fake_ask(*args, **kwargs):
        nonlocal orig_on_output
        orig_on_output = kwargs.get("on_output")
        kwargs["runtime"] = target
        return real_ask(*args, **kwargs)


    monkeypatch.setattr(ask_mod, "ask", fake_ask)
    try:
        ret = cli.main(["--db", str(tmp_path / "core.db"), "ask", "cx", "hello"])
        assert ret == 0
        captured = capsys.readouterr()
        # cli.main itself must have printed the final answer because display_truncated is set,
        # rather than waiting for the blocked daemon thread that OS exit would kill.
        assert "WORKING" in captured.out
        assert "THE FINAL ANSWER" in captured.out
    finally:
        block_evt.set()



