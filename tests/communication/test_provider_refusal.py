"""PR 3 tests: Structured Provider Refusal Classification (NOT_EXECUTED).

Covers:
- Early refusal with zero tool calls classifies as NOT_EXECUTED (task outcome NOT_EXECUTED, certainty STARTED, acked=1, stream reusable).
- Real provider output shapes:
  * ag exit 3: Individual quota reached + RESOURCE_EXHAUSTED + Resets in 20h9m47s
  * ag exit 1: invalid model selection (--model "x" --effort ""): model x is not recognized
  * cx exit 1 JSONL: {"type":"error","message":"You've hit your usage limit. ... try again at 12:24 PM."}
  * cx exit 1 non-refusal: WARNING: failed to clean up stale arg0 temp dirs ... (os error 5) -> stays uncertain
- Refusal after tool execution and writable ag refusals remain uncertain (stays in blocked_uncertain).
- Refusal variants: curly apostrophes (\u2019), stderr messages, quoted user text (no false positives).
- Ask result JSON and text surface reason and reset time.
- Diag stream health surfaces last refusal reason and reset time.
- Frozen M1 monotonicity: certainty strictly remains STARTED, never downgraded, no fabricated response records.
"""
from __future__ import annotations

import json
import sqlite3
import sys
import textwrap
from contextlib import closing

import pytest

from peerhub.core.store import CoreStore
from peerhub.extensions.adapters import CliRuntimeTarget
from peerhub.extensions.ask import ask
from peerhub.extensions.diag import ReadonlyDiag
from tests.communication.bridge_helpers import bseed, offset_row, responses, sql
from tests.communication.fakes import CrashInjected, CrashInjector, FakeRuntimeTarget
from tests.communication.harness.bridge import BridgeHarness
from tests.communication.helpers import req

PY = sys.executable


# =====================================================================
# 1. Real provider output shapes as fixtures
# =====================================================================

AG_QUOTA_EXIT_3_OUTPUT = (
    "error: Individual quota reached. Please upgrade your subscription to increase your limits. Resets in 20h9m47s.\n"
    'AGY_ERROR: {"short_error":"RESOURCE_EXHAUSTED (code 429)"}\n'
)

AG_INVALID_MODEL_EXIT_1_OUTPUT = (
    'error: invalid model selection (--model "x" --effort ""): model x is not recognized: '
    'supported models are gemini-3.8-flash-low, gemini-3.8-flash-high\n'
)

CX_USAGE_LIMIT_JSONL = (
    '{"type":"error","message":"You\'ve hit your usage limit. You can continue with a smaller model, '
    'or try again at 12:24 PM."}\n'
)

CX_USAGE_LIMIT_CURLY_JSONL = (
    '{"type":"error","message":"You\u2019ve hit your usage limit. You can continue with a smaller model, '
    'or try again at 12:24 PM."}\n'
)

CX_STALE_TEMP_DIRS_WARNING = (
    "WARNING: failed to clean up stale arg0 temp dirs in C:\\Users\\user\\AppData\\Local\\Temp: "
    "Access is denied. (os error 5)\n"
)

CC_FIVE_HOUR_LIMIT_JSONL = (
    '{"type":"result","subtype":"error_during_execution","is_error":true,'
    '"errors":["You\'ve hit your 5-hour limit. Resets 3pm (Asia/Seoul)"]}\n'
)


def test_cc_five_hour_limit_preserves_hour_only_reset_raw(tmp_path, bridge_h):
    target = _output_target(tmp_path, "cc", CC_FIVE_HOUR_LIMIT_JSONL)
    bseed(bridge_h)
    result = bridge_h.delivery_cycle("b", "s", target)
    assert result.status == "not_executed"
    assert result.detail["reason"] == "usage_limit"
    assert result.detail["reset_time"] == "3pm"


# =====================================================================
# 2. Test 6: Pre-tool quota/auth/limit refusal records NOT_EXECUTED
# =====================================================================

def test_quota_refusal_read_only_not_executed_ag_exit_3(tmp_path, bridge_h):
    """#140: ag with writable=False fails closed pre-spawn (failed_not_started / NOT_STARTED)."""
    ws = tmp_path / "ws_ag_quota"
    ws.mkdir()
    script = textwrap.dedent(f"""
        import sys
        sys.stderr.write({AG_QUOTA_EXIT_3_OUTPUT!r})
        sys.exit(3)
    """)
    target = CliRuntimeTarget(
        "ag",
        ws,
        command=[PY, "-c", script],
        timeout_s=15.0,
        writable=False,
    )

    (rec,) = bseed(bridge_h, stream="s_ag_q")
    res = bridge_h.delivery_cycle("b", "s_ag_q", target)

    assert res.status == "failed_not_started"
    assert res.certainty == "NOT_STARTED"
    assert "ag execution requires --writable" in res.detail.get("error", "")
    assert responses(bridge_h, "s_ag_q") == []


def test_quota_refusal_writable_ag_exit_3_stays_uncertain(tmp_path, bridge_h):
    """Agy emits JSON only at completion, so empty stdout cannot exclude edits."""
    script = tmp_path / "provider.py"
    sentinel = tmp_path / "edited.txt"
    script.write_text(
        "import sys\nfrom pathlib import Path\n"
        "Path('edited.txt').write_text('work completed', encoding='utf-8')\n"
        f"sys.stderr.write({AG_QUOTA_EXIT_3_OUTPUT!r})\n"
        "sys.exit(3)\n",
        encoding="utf-8", newline="\n",
    )
    target = CliRuntimeTarget("ag", tmp_path, command=[PY, str(script)], timeout_s=15, writable=True)
    (rec,) = bseed(bridge_h)

    result = bridge_h.delivery_cycle("b", "s", target)

    assert sentinel.read_text(encoding="utf-8") == "work completed"
    assert (result.status, result.certainty) == ("uncertain", "STARTED")
    assert sql(bridge_h, "SELECT acked, response_record_id FROM bridge_deliveries WHERE record_id=?",
               (rec.record_id,)) == [(0, None)]
    assert offset_row(bridge_h)[0] == 0
    assert responses(bridge_h) == []
    assert sql(bridge_h, "SELECT kind FROM bridge_evidence WHERE delivery_id=? AND kind='refusal_detected'",
               (result.delivery_id,)) == []

    # A repeated delivery must stay blocked without invoking the provider again.
    script.write_text("raise AssertionError('must not replay')\n", encoding="utf-8", newline="\n")
    assert bridge_h.delivery_cycle("b", "s", target).status == "blocked_uncertain"


def test_quota_refusal_pre_tool_not_executed_cx_exit_1(tmp_path, bridge_h):
    """cx exit 1 usage-limit refusal JSONL: classifies as NOT_EXECUTED with reset time 12:24 PM."""
    ws = tmp_path / "ws_cx_quota"
    ws.mkdir()
    script = textwrap.dedent(f"""
        import sys
        sys.stdout.write({CX_USAGE_LIMIT_JSONL!r})
        sys.exit(1)
    """)
    target = CliRuntimeTarget(
        "cx",
        ws,
        command=[PY, "-c", script],
        timeout_s=15.0,
    )

    (rec,) = bseed(bridge_h, stream="s_cx_q")
    res = bridge_h.delivery_cycle("b", "s_cx_q", target)

    assert res.status == "not_executed"
    assert res.certainty == "STARTED"
    assert res.detail.get("task_outcome") == "NOT_EXECUTED"
    assert res.detail.get("reason") == "usage_limit"
    assert res.detail.get("reset_time") == "12:24 PM"

    # Acked=1, no response record, stream unblocked
    assert sql(bridge_h, "SELECT acked FROM bridge_deliveries WHERE record_id=?", (rec.record_id,))[0][0] == 1
    assert responses(bridge_h, "s_cx_q") == []
    assert bridge_h.delivery_cycle("b", "s_cx_q", target).status == "idle"


def test_quota_refusal_pre_tool_not_executed_ag_invalid_model(tmp_path, bridge_h):
    """cc exit 1 invalid model refusal: classifies as NOT_EXECUTED, reason invalid_model, reset_time None."""
    ws = tmp_path / "ws_cc_model"
    ws.mkdir()
    script = textwrap.dedent(f"""
        import sys, json
        sys.stdout.write(json.dumps({{"type": "error", "message": {AG_INVALID_MODEL_EXIT_1_OUTPUT!r}}}) + "\\n")
        sys.exit(1)
    """)
    target = CliRuntimeTarget(
        "cc",
        ws,
        command=[PY, "-c", script],
        timeout_s=15.0,
    )

    (rec,) = bseed(bridge_h, stream="s_ag_m")
    res = bridge_h.delivery_cycle("b", "s_ag_m", target)

    assert res.status == "not_executed"
    assert res.certainty == "STARTED"
    assert res.detail.get("task_outcome") == "NOT_EXECUTED"
    assert res.detail.get("reason") == "invalid_model"
    assert res.detail.get("reset_time") is None

    assert sql(bridge_h, "SELECT acked FROM bridge_deliveries WHERE record_id=?", (rec.record_id,))[0][0] == 1
    assert bridge_h.delivery_cycle("b", "s_ag_m", target).status == "idle"


# =====================================================================
# 3. Test 7: Refusal after tool execution remains uncertain
# =====================================================================

def test_quota_refusal_post_tool_uncertain_cx(tmp_path, bridge_h):
    """cx error after a tool call (command_execution) must NOT be classified as NOT_EXECUTED; remains uncertain."""
    ws = tmp_path / "ws_cx_post_tool"
    ws.mkdir()
    # Emits thread.started, then a tool execution event, then usage limit error
    script = textwrap.dedent("""
        import sys, json
        sys.stdout.write(json.dumps({"type": "thread.started", "thread_id": "th-post-1"}) + "\\n")
        sys.stdout.write(json.dumps({"type": "item.completed", "item": {"type": "command_execution", "command": "git status"}}) + "\\n")
        sys.stdout.write(json.dumps({"type": "error", "message": "You've hit your usage limit. try again at 12:24 PM."}) + "\\n")
        sys.exit(1)
    """)
    target = CliRuntimeTarget(
        "cx",
        ws,
        command=[PY, "-c", script],
        timeout_s=15.0,
    )

    (rec,) = bseed(bridge_h, stream="s_post_cx")
    res = bridge_h.delivery_cycle("b", "s_post_cx", target)

    # Must remain uncertain because preceding tool side effects cannot be ruled out
    assert res.status == "uncertain"
    assert res.certainty == "STARTED"

    # Prompt is NOT acked (acked=0)
    assert sql(bridge_h, "SELECT acked FROM bridge_deliveries WHERE record_id=?", (rec.record_id,))[0][0] == 0

    # Stream is trapped in blocked_uncertain on next cycle
    next_res = bridge_h.delivery_cycle("b", "s_post_cx", target)
    assert next_res.status == "blocked_uncertain"
    assert next_res.certainty == "STARTED"


def test_quota_refusal_post_tool_uncertain_cc(tmp_path, bridge_h):
    """cc error after a tool_use block must remain uncertain."""
    ws = tmp_path / "ws_cc_post_tool"
    ws.mkdir()
    script = textwrap.dedent("""
        import sys, json
        sys.stdout.write(json.dumps({"type": "system", "subtype": "init", "session_id": "s-post-1"}) + "\\n")
        sys.stdout.write(json.dumps({
            "type": "assistant",
            "message": {"content": [{"type": "tool_use", "id": "t1", "name": "edit", "input": {}}]}
        }) + "\\n")
        sys.stdout.write(json.dumps({
            "type": "error",
            "error": {"type": "rate_limit_error", "message": "rate limit reached"}
        }) + "\\n")
        sys.exit(1)
    """)
    target = CliRuntimeTarget(
        "cc",
        ws,
        command=[PY, "-c", script],
        timeout_s=15.0,
    )

    (rec,) = bseed(bridge_h, stream="s_post_cc")
    res = bridge_h.delivery_cycle("b", "s_post_cc", target)

    assert res.status == "uncertain"
    assert res.certainty == "STARTED"
    next_res = bridge_h.delivery_cycle("b", "s_post_cc", target)
    assert next_res.status == "blocked_uncertain"


# =====================================================================
# 4. Test 8: Refusal variants (apostrophes, non-refusals, prompt text)
# =====================================================================

def test_quota_refusal_variants_curly_apostrophe(tmp_path, bridge_h):
    """cx usage limit with curly apostrophe (\u2019) is parsed identically."""
    ws = tmp_path / "ws_curly"
    ws.mkdir()
    script = textwrap.dedent(f"""
        import sys
        sys.stdout.buffer.write({CX_USAGE_LIMIT_CURLY_JSONL.encode("utf-8")!r})
        sys.exit(1)
    """)
    target = CliRuntimeTarget("cx", ws, command=[PY, "-c", script], timeout_s=15.0)

    (rec,) = bseed(bridge_h, stream="s_curly")
    res = bridge_h.delivery_cycle("b", "s_curly", target)

    assert res.status == "not_executed"
    assert res.certainty == "STARTED"
    assert res.detail.get("reason") == "usage_limit"
    assert res.detail.get("reset_time") == "12:24 PM"


def test_quota_refusal_variants_non_refusal_stale_temp_dirs(tmp_path, bridge_h):
    """cx exit 1 with stale temp dirs warning is NOT a refusal: remains uncertain."""
    ws = tmp_path / "ws_stale_temp"
    ws.mkdir()
    script = textwrap.dedent(f"""
        import sys
        sys.stderr.write({CX_STALE_TEMP_DIRS_WARNING!r})
        sys.exit(1)
    """)
    target = CliRuntimeTarget("cx", ws, command=[PY, "-c", script], timeout_s=15.0)

    (rec,) = bseed(bridge_h, stream="s_stale")
    res = bridge_h.delivery_cycle("b", "s_stale", target)

    assert res.status == "uncertain"
    assert res.certainty == "STARTED"

    # Next cycle stays blocked_uncertain
    next_res = bridge_h.delivery_cycle("b", "s_stale", target)
    assert next_res.status == "blocked_uncertain"


def test_quota_refusal_variants_quoted_prompt_text_no_false_positive(tmp_path, bridge_h):
    """Stderr echoing user prompt that contains 'You've hit your usage limit' is NOT misclassified."""
    ws = tmp_path / "ws_quoted_prompt"
    ws.mkdir()
    prompt_text = 'Explain this error: "You\'ve hit your usage limit. try again at 12:24 PM."'
    script = textwrap.dedent("""
        import sys
        # Echo the prompt in an unrelated error exit
        p = sys.stdin.read()
        sys.stderr.write("fatal: generic compilation error while processing: " + p + "\\n")
        sys.exit(2)
    """)
    target = CliRuntimeTarget("cx", ws, command=[PY, "-c", script], timeout_s=15.0)

    bridge_h.create_peer({"peer_id": "a"})
    bridge_h.create_peer({"peer_id": "b"})
    bridge_h.create_stream({"stream_id": "s_quoted", "members": ["a", "b"]})
    rec = bridge_h.append_record(req(body=prompt_text, key="m_q", author="a", stream="s_quoted"))
    res = bridge_h.delivery_cycle("b", "s_quoted", target)

    # Must NOT be classified as not_executed; stays uncertain
    assert res.status == "uncertain"
    assert res.certainty == "STARTED"
    next_res = bridge_h.delivery_cycle("b", "s_quoted", target)
    assert next_res.status == "blocked_uncertain"


def test_quota_refusal_variants_auth_failure_agy_login(tmp_path, bridge_h):
    """cc auth failure 'Please run `agy login`' classifies as NOT_EXECUTED with auth_failure."""
    ws = tmp_path / "ws_auth"
    ws.mkdir()
    script = textwrap.dedent("""
        import sys, json
        sys.stdout.write(json.dumps({"type": "error", "message": "error: authentication required. Please run `agy login` to authenticate."}) + "\\n")
        sys.exit(1)
    """)
    target = CliRuntimeTarget("cc", ws, command=[PY, "-c", script], timeout_s=15.0)

    (rec,) = bseed(bridge_h, stream="s_auth")
    res = bridge_h.delivery_cycle("b", "s_auth", target)

    assert res.status == "not_executed"
    assert res.certainty == "STARTED"
    assert res.detail.get("reason") == "auth_failure"
    assert sql(bridge_h, "SELECT acked FROM bridge_deliveries WHERE record_id=?", (rec.record_id,))[0][0] == 1


# =====================================================================
# 5. Ask integration, Diag, and CLI formatting
# =====================================================================

def test_ask_integration_surfaces_reason_and_reset(tmp_path):
    """ask() returns status not_executed with task_outcome, reason, reset_time, and records activity observation."""
    db = tmp_path / "peerhub.db"
    ws = tmp_path / "ws_ask"
    ws.mkdir()
    script = textwrap.dedent(f"""
        import sys, json
        sys.stdout.write(json.dumps({{"type": "error", "message": {AG_QUOTA_EXIT_3_OUTPUT!r}}}) + "\\n")
        sys.exit(3)
    """)
    target = CliRuntimeTarget("cc", ws, command=[PY, "-c", script], timeout_s=15.0)

    result = ask(db, "cc", "Refactor models", stream_id="impl", runtime=target)

    # 1. Ask result JSON
    assert result["status"] == "not_executed"
    assert result["certainty"] == "STARTED"
    assert result.get("task_outcome") == "NOT_EXECUTED"
    assert result.get("reason") == "quota_exhausted"
    assert result.get("reset_time") == "20h9m47s"
    assert result.get("response") is None

    # 2. Activity observation recorded with refusal reason and reset time
    diag = ReadonlyDiag(db)
    act_sec = diag.render(["activity"]).sections.get("activity")
    assert act_sec is not None and act_sec.status == "OK"
    cc_act = next(p for p in act_sec.data["peers"] if p["peer"] == "cc")
    assert cc_act["last_status"] == "not_executed"

    # 3. Diag inspect_stream_health surfaces last refusal
    s_health = diag.inspect_stream_health("impl")
    assert s_health["status"] == "OK"
    assert "last_refusal" in s_health
    assert s_health["last_refusal"]["reason"] == "quota_exhausted"
    assert s_health["last_refusal"]["reset_time"] == "20h9m47s"
    assert s_health["last_refusal"]["task_outcome"] == "NOT_EXECUTED"


def test_cli_ask_formatting_text_and_json(tmp_path, capsys):
    """CLI prints structured NOT_EXECUTED message in text mode and JSON in --json mode."""
    db = tmp_path / "cli_peerhub.db"
    ws = tmp_path / "ws_cli"
    ws.mkdir()

    # Pre-register peer with fake command pointing to refusal script
    script = tmp_path / "refuse_cx.py"
    script.write_text(textwrap.dedent(f"""
        import sys
        sys.stdout.write({CX_USAGE_LIMIT_JSONL!r})
        sys.exit(1)
    """), encoding="utf-8")

    # Run ask via CliRuntimeTarget directly or through CLI
    # Test JSON mode
    target = CliRuntimeTarget("cx", ws, command=[PY, str(script)], timeout_s=15.0)
    res_json = ask(db, "cx", "Build feature", stream_id="cli_s", runtime=target)
    assert res_json["status"] == "not_executed"
    assert res_json["reason"] == "usage_limit"
    assert res_json["reset_time"] == "12:24 PM"

    from unittest.mock import patch
    from peerhub.cli.app import main

    with patch("peerhub.extensions.ask.ask", return_value=res_json):
        capsys.readouterr()
        code = main(["--db", str(db), "ask", "cx", "Build feature"])
        assert code == 1
        captured = capsys.readouterr()
        assert "Task outcome: NOT_EXECUTED" in captured.err
        assert "usage limit / quota exhausted" in captured.err
        assert "12:24 PM" in captured.err

        # JSON mode
        code_json = main(["--db", str(db), "ask", "cx", "Build feature", "--json"])
        assert code_json == 1
        captured_json = capsys.readouterr()
        parsed = json.loads(captured_json.out)
        assert parsed["status"] == "not_executed"
        assert parsed["reason"] == "usage_limit"
        assert parsed["reset_time"] == "12:24 PM"


def _jsonl(*events):
    return "".join(json.dumps(event) + "\n" for event in events)


def _output_target(tmp_path, kind, stdout, *, stderr="", exit_code=1, max_bytes=1_000_000):
    # Files keep oversized-output fixtures out of Windows' command-line limit.
    script = tmp_path / "provider.py"
    script.write_text(
        "import sys\n"
        f"sys.stdout.buffer.write({stdout.encode('utf-8')!r})\n"
        f"sys.stderr.buffer.write({stderr.encode('utf-8')!r})\n"
        f"sys.exit({exit_code})\n",
        encoding="utf-8", newline="\n",
    )
    return CliRuntimeTarget(kind, tmp_path, command=[PY, str(script)], timeout_s=15, max_bytes=max_bytes, writable=(kind == "ag"))


@pytest.mark.parametrize("kind", ["cx", "cc", "ag"])
@pytest.mark.parametrize("phrase", ["RESOURCE_EXHAUSTED", "invalid model selection", "authentication_error", "You've hit your usage limit"])
def test_successful_answer_about_refusal_is_delivered(tmp_path, bridge_h, kind, phrase):
    answer = f"The diagnostic {phrase} means the provider rejected a request."
    if kind == "cx":
        stdout = _jsonl(
            {"type": "item.completed", "item": {"id": "item_0", "type": "agent_message", "text": answer}},
            {"type": "turn.completed", "usage": {"input_tokens": 10, "output_tokens": 10}},
        )
    elif kind == "cc":
        stdout = _jsonl({"type": "result", "subtype": "success", "is_error": False, "result": answer})
    else:
        stdout = json.dumps({"response": answer, "conversation_id": "c1"})
    target = _output_target(tmp_path, kind, stdout, stderr="warning: usage limit reached in a previous request", exit_code=0)
    bseed(bridge_h)
    result = bridge_h.delivery_cycle("b", "s", target)
    assert result.status == "delivered"
    assert json.loads(responses(bridge_h)[0][4]) == answer


# Shapes: openai/codex sdk/typescript/src/items.ts. In particular, file_change
# and mcp_tool_call are not named file_edit/tool_call in exec JSONL.
@pytest.mark.parametrize("event_type,item", [
    ("item.started", {"id": "item_0", "type": "command_execution", "command": "echo changed", "aggregated_output": "", "status": "in_progress"}),
    ("item.updated", {"id": "item_0", "type": "command_execution", "command": "echo changed", "aggregated_output": "changed", "status": "in_progress"}),
    ("item.completed", {"id": "item_0", "type": "file_change", "changes": [{"path": "out.txt", "kind": "add"}], "status": "completed"}),
    ("item.started", {"id": "item_0", "type": "mcp_tool_call", "server": "test", "tool": "write", "arguments": {}, "status": "in_progress"}),
    ("item.completed", {"id": "item_0", "type": "mcp_tool_call", "server": "test", "tool": "write", "arguments": {}, "status": "completed", "result": {"content": [], "structured_content": None}}),
    ("item.completed", {"id": "item_0", "type": "web_search", "query": "test"}),
    ("item.started", {"id": "item_0", "type": "future_action"}),
])
@pytest.mark.parametrize("refusal_first", [False, True])
def test_cx_execution_at_any_point_prevents_refusal_settlement(tmp_path, bridge_h, event_type, item, refusal_first):
    action = _jsonl({"type": event_type, "item": item})
    stdout = CX_USAGE_LIMIT_JSONL + action if refusal_first else action + CX_USAGE_LIMIT_JSONL
    target = _output_target(tmp_path, "cx", stdout)
    (rec,) = bseed(bridge_h)
    result = bridge_h.delivery_cycle("b", "s", target)
    assert (result.status, result.certainty) == ("uncertain", "STARTED")
    assert sql(bridge_h, "SELECT acked FROM bridge_deliveries WHERE record_id=?", (rec.record_id,)) == [(0,)]
    assert bridge_h.delivery_cycle("b", "s", target).status == "blocked_uncertain"


@pytest.mark.parametrize("prefix", [
    '{"type":"item.started","item":\n',
    '[{"type":"command_execution"}]\n',
    'unrecognized output\n',
    '{"type":"future_execution_event"}\n',
    '{"type":"item.started"}\n',
    None,
], ids=["truncated-item", "event-array", "plain-text", "unknown-event", "missing-item", "oversized-command"])
def test_incomplete_execution_evidence_stays_uncertain(tmp_path, bridge_h, prefix):
    if prefix is None:
        prefix = _jsonl({"type": "item.started", "item": {"type": "command_execution", "command": "x" * 270_000}})
    target = _output_target(tmp_path, "cx", prefix + CX_USAGE_LIMIT_JSONL, max_bytes=1024)
    bseed(bridge_h)
    result = bridge_h.delivery_cycle("b", "s", target)
    assert (result.status, result.certainty) == ("uncertain", "STARTED")
    assert bridge_h.delivery_cycle("b", "s", target).status == "blocked_uncertain"


def test_refusal_beyond_retention_cap_with_complete_events(tmp_path, bridge_h):
    prefix = _jsonl({"type": "item.completed", "item": {"id": "item_0", "type": "reasoning", "text": "x" * 500}}) * 20
    target = _output_target(tmp_path, "cx", prefix + CX_USAGE_LIMIT_JSONL, max_bytes=1024)
    bseed(bridge_h)
    result = bridge_h.delivery_cycle("b", "s", target)
    assert (result.status, result.certainty) == ("not_executed", "STARTED")


@pytest.mark.parametrize("kind,stdout,stderr", [
    ("cx", _jsonl({"type": "item.completed", "item": {"type": "agent_message", "text": "RESOURCE_EXHAUSTED"}}), "fatal: cleanup failed"),
    ("cx", _jsonl({"type": "error", "message": "could not process quoted input: You've hit your usage limit"}), ""),
    ("ag", json.dumps({"response": "changed files", "conversation_id": "c1"}), AG_QUOTA_EXIT_3_OUTPUT),
    ("ag", json.dumps({"error": "Individual quota reached"}), ""),
    ("ag", "", "fatal: could not process quoted input: Individual quota reached"),
    ("ag", "", "error: wrote out.txt\n" + AG_QUOTA_EXIT_3_OUTPUT),
    ("ag", "", "error: invalid model selection\nAGY_ERROR: truncated JSON"),
])
def test_nonzero_exit_does_not_make_response_or_arbitrary_stderr_a_refusal(tmp_path, bridge_h, kind, stdout, stderr):
    target = _output_target(tmp_path, kind, stdout, stderr=stderr)
    bseed(bridge_h)
    assert bridge_h.delivery_cycle("b", "s", target).status == "uncertain"


@pytest.mark.parametrize("tool_event", [None,
    {"type": "stream_event", "event": {"type": "content_block_start", "index": 0, "content_block": {"type": "tool_use", "id": "toolu_1", "name": "Write", "input": {}}}},
    {"type": "assistant", "message": {"content": [{"type": "server_tool_use", "id": "srvtoolu_1", "name": "web_search", "input": {}}]}},
])
def test_cc_result_errors_and_streamed_tools(tmp_path, bridge_h, tool_event):
    # claude-agent-sdk-python ResultMessage: errors is a list on error results.
    failure = {"type": "result", "subtype": "error_during_execution", "is_error": True,
               "errors": ["rate_limit_error: Rate limit exceeded"], "num_turns": 1,
               "session_id": "s1", "duration_ms": 20, "duration_api_ms": 10}
    stdout = (_jsonl(tool_event) if tool_event else "") + _jsonl(failure)
    target = _output_target(tmp_path, "cc", stdout)
    bseed(bridge_h)
    result = bridge_h.delivery_cycle("b", "s", target)
    assert result.status == ("uncertain" if tool_event else "not_executed")
    assert result.certainty == "STARTED"


@pytest.mark.parametrize("point", ["bridge.after_refusal_evidence", "bridge.after_refusal_offset_before_ack"])
def test_refusal_settlement_recovers_after_crash_without_invocation(tmp_path, point):
    crash = CrashInjector(point)
    h = BridgeHarness(tmp_path / "ws", fault_hook=crash)
    (rec,) = bseed(h)
    target = _output_target(tmp_path, "cx", CX_USAGE_LIMIT_JSONL)
    with pytest.raises(CrashInjected):
        h.delivery_cycle("b", "s", target)
    assert sql(h, "SELECT certainty, acked FROM bridge_deliveries WHERE record_id=?", (rec.record_id,)) == [("STARTED", 0)]
    h = BridgeHarness(tmp_path / "ws", h.clock)
    never_invoke = FakeRuntimeTarget()
    result = h.delivery_cycle("b", "s", never_invoke)
    assert (result.status, result.certainty) == ("not_executed", "STARTED")
    assert never_invoke.calls == []
    assert offset_row(h)[0] == rec.position
    assert sql(h, "SELECT acked FROM bridge_deliveries WHERE record_id=?", (rec.record_id,)) == [(1,)]
    assert h.current_mapping("b", "s")["state"] == "LOST"
    assert responses(h) == []


def test_refusal_offset_conflict_keeps_unacked_started_and_recovers(tmp_path):
    state = {"armed": False, "left": 8}

    def bridge_hook(point):
        if point == "bridge.after_refusal_evidence":
            state["armed"] = True

    def store_hook(point):
        if point == "offset.begin" and state["armed"] and state["left"]:
            state["left"] -= 1
            other = CoreStore(h.db_path)
            off = other.get_offset("b", "s")
            other.advance_offset_cas("b", "s", off.read_through_position, off.revision)

    h = BridgeHarness(tmp_path / "ws", fault_hook=bridge_hook, store_hook=store_hook)
    (rec,) = bseed(h)
    result = h.delivery_cycle("b", "s", _output_target(tmp_path, "cx", CX_USAGE_LIMIT_JSONL))
    assert (result.status, result.certainty) == ("ack_failed", "STARTED")
    assert sql(h, "SELECT acked FROM bridge_deliveries WHERE record_id=?", (rec.record_id,)) == [(0,)]
    assert offset_row(h)[0] == 0
    never_invoke = FakeRuntimeTarget()
    assert h.delivery_cycle("b", "s", never_invoke).status == "not_executed"
    assert never_invoke.calls == []
    assert offset_row(h)[0] == rec.position


def test_repeated_request_returns_committed_refusal_without_processing_other_work(tmp_path):
    db = tmp_path / "ask.db"
    target = _output_target(tmp_path, "cx", CX_USAGE_LIMIT_JSONL)
    first = ask(db, "cx", "task", stream_id="s", request_id="r1", runtime=target)
    store = CoreStore(db)
    pending = store.append_record(stream_id="s", author_peer_id="user", kind="prompt", body="other task",
                                  targets=["cx"], idempotency_key="other", created_at="2026-10-13T00:00:00Z")
    # Reusing the identical runtime binding must not spawn it again.
    (tmp_path / "provider.py").write_text("raise AssertionError('must not run')\n", encoding="utf-8", newline="\n")
    second = ask(db, "cx", "task", stream_id="s", request_id="r1", runtime=target)
    for key in ("status", "certainty", "delivery_id", "record_id", "reason", "reset_time", "task_outcome", "session_generation"):
        assert second[key] == first[key]
    assert second["status"] == "not_executed"
    assert store.get_offset("cx", "s").read_through_position < pending.position
    with closing(sqlite3.connect(db)) as conn:
        assert conn.execute("SELECT COUNT(*) FROM bridge_deliveries").fetchone() == (1,)


@pytest.mark.parametrize("prefix", [
    {"type": "assistant", "message": {"content": "unrecognized message format"}},
    {"type": "stream_event", "event": {"type": "content_block_delta", "index": 0, "delta": {"type": "input_json_delta", "partial_json": "{}"}}},
])
def test_cc_missing_tool_start_or_malformed_message_is_not_zero_execution(tmp_path, bridge_h, prefix):
    stdout = _jsonl(prefix, {"type": "result", "subtype": "error_during_execution", "is_error": True,
                            "errors": ["rate_limit_error: Rate limit exceeded"]})
    bseed(bridge_h)
    assert bridge_h.delivery_cycle("b", "s", _output_target(tmp_path, "cc", stdout)).status == "uncertain"


def test_truncated_ag_stderr_cannot_prove_startup_refusal(tmp_path, bridge_h):
    stderr = AG_QUOTA_EXIT_3_OUTPUT + "x" * 10000 + AG_QUOTA_EXIT_3_OUTPUT
    bseed(bridge_h)
    result = bridge_h.delivery_cycle("b", "s", _output_target(tmp_path, "ag", "", stderr=stderr, exit_code=3, max_bytes=128))
    assert result.status == "uncertain"


def test_success_after_recoverable_error_is_not_settled_as_refusal(tmp_path, bridge_h):
    stdout = _jsonl(
        {"type": "error", "error": {"type": "rate_limit_error", "message": "Rate limit exceeded"}},
        {"type": "result", "subtype": "success", "is_error": False, "result": "retried successfully"},
    )
    bseed(bridge_h)
    result = bridge_h.delivery_cycle("b", "s", _output_target(tmp_path, "cc", stdout, exit_code=0))
    assert result.status == "delivered"


@pytest.mark.parametrize("started,payload", [
    (True, {}),
    (True, {"outcome": "NOT_EXECUTED", "reason": "unknown"}),
    (True, {"outcome": "EXECUTED", "reason": "usage_limit"}),
    (False, {"outcome": "NOT_EXECUTED", "reason": "usage_limit"}),
])
def test_bridge_does_not_ack_incomplete_refusal_event(bridge_h, started, payload):
    (rec,) = bseed(bridge_h)
    target = FakeRuntimeTarget()
    events = [("started", "exec-1")] if started else []
    target.script_deliver(*events, ("refusal", payload))
    result = bridge_h.delivery_cycle("b", "s", target)
    assert result.status == "uncertain"
    assert result.certainty == ("STARTED" if started else "MAY_HAVE_STARTED")
    assert sql(bridge_h, "SELECT acked FROM bridge_deliveries WHERE record_id=?", (rec.record_id,)) == [(0,)]


@pytest.mark.parametrize("text", [
    "Claude AI usage limit reached|1760000000",
    "You've hit your limit · resets 3pm (Asia/Seoul)",
    "5-hour limit reached ∙ resets 3pm",
    "Weekly limit reached",
])
def test_cc_limit_phrasings_classify_as_usage_limit(text):
    from peerhub.extensions.adapters.base import classify_refusal_text
    assert classify_refusal_text(text)["reason"] == "usage_limit"
