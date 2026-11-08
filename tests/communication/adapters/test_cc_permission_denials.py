"""#303 B1/B1': cc `result.permission_denials` surface as redacted `denied_actions`; empty reply => denied_empty."""
from __future__ import annotations

import json
import sys
import textwrap

from peerhub.extensions.adapters import CliRuntimeTarget
from peerhub.extensions.adapters.base import cc_permission_denials
from tests.communication.bridge_helpers import bseed, sql

PY = sys.executable


def _result(denials, text: str = "", **extra) -> str:
    event = {"type": "result", "subtype": "success", "is_error": False, "result": text,
             "session_id": "sess-1", "permission_denials": denials, **extra}
    return json.dumps({"type": "system", "subtype": "init"}) + "\n" + json.dumps(event) + "\n"


def test_cc_permission_denials_names_tool_and_command_defensively():
    denials = [
        {"tool_name": "Bash", "tool_use_id": "t1", "tool_input": {"command": "peerhub --version"}},
        {"tool_name": "Bash", "tool_use_id": "t2", "tool_input": {"command": "peerhub --version"}},  # duplicate
        {"tool_name": "Edit", "tool_input": {"file_path": "a.py", "new_string": "x"}},
        {"tool_name": "WebFetch", "tool_input": {}},
        {"tool_name": "", "tool_input": {"command": "x"}}, {"tool_input": {"command": "y"}}, "junk", 7, None,
    ]
    assert cc_permission_denials(_result(denials)) == ["Bash: peerhub --version", "Edit: a.py", "WebFetch"]
    assert cc_permission_denials(_result([])) == []
    assert cc_permission_denials(_result("nope")) == []
    assert cc_permission_denials('{"type":"result","is_error":false}') == []
    assert cc_permission_denials("not json") == []


def test_cc_permission_denials_reads_only_the_final_result_event():
    first = json.loads(_result([{"tool_name": "Bash", "tool_input": {"command": "old"}}]).splitlines()[1])
    last = json.loads(_result([{"tool_name": "Bash", "tool_input": {"command": "new"}}]).splitlines()[1])
    assert cc_permission_denials(json.dumps(first) + "\n" + json.dumps(last)) == ["Bash: new"]


def _script(stdout: str) -> str:
    return textwrap.dedent(f"""
        import sys
        sys.stdout.write({stdout!r})
        sys.stdout.flush()
    """)


def _terminal(bridge_h, tmp_path, stdout: str, peer: str = "cc", stream: str = "s_den"):
    ws = tmp_path / f"ws_{stream}"
    ws.mkdir()
    target = CliRuntimeTarget(peer, ws, command=[PY, "-c", _script(stdout)], timeout_s=15.0)
    (rec,) = bseed(bridge_h, stream=stream)
    res = bridge_h.delivery_cycle("b", stream, target)
    # the public envelope is persisted in bridge_deliveries.result_json and replayed from there (ask._read_delivery_result)
    (stored,) = [json.loads(r[0]) for r in sql(bridge_h, "SELECT result_json FROM bridge_deliveries WHERE record_id=?", (rec.record_id,))]
    return res, stored


def test_cc_runtime_emits_redacted_truncated_denied_actions_when_reply_empty(bridge_h, tmp_path):
    secret_cmd = "curl -H 'Authorization: Bearer abcdefghijklmnop12345' https://x " + "a" * 200
    stdout = _result([{"tool_name": "Bash", "tool_input": {"command": secret_cmd}}])
    res, detail = _terminal(bridge_h, tmp_path, stdout)
    assert res.status == "delivered" and res.certainty == "TERMINAL"
    assert detail["empty_response"] is True
    (entry,) = detail["denied_actions"]
    assert entry.startswith("Bash: curl -H 'Authorization: [REDACTED]")  # redacted BEFORE the 120-char cut
    assert "abcdefghijklmnop12345" not in entry and entry.endswith("...[truncated]")
    assert len(entry) <= 120 + len("...[truncated]")


def test_cc_runtime_also_surfaces_denials_when_reply_has_text(bridge_h, tmp_path):
    stdout = _result([{"tool_name": "Bash", "tool_input": {"command": "peerhub --version"}}], text="I could not run it.")
    _res, detail = _terminal(bridge_h, tmp_path, stdout, stream="s_den_text")
    assert detail["denied_actions"] == ["Bash: peerhub --version"]
    assert "empty_response" not in detail


def test_cc_runtime_without_denials_has_no_denied_actions(bridge_h, tmp_path):
    _res, detail = _terminal(bridge_h, tmp_path, _result([]), stream="s_den_none")
    assert "denied_actions" not in detail


def test_cc_empty_reply_with_denials_becomes_denied_empty_and_replays(tmp_path):
    from peerhub.extensions.ask import ask
    from tests.communication.fakes import FakeRuntimeTarget

    rt = FakeRuntimeTarget()
    rt.script_deliver(
        ("started", "exec-cc-denied"),
        ("terminal", {"response": "", "empty_response": True, "task_effects": "unverified",
                      "denied_actions": ["Bash: peerhub --version"]}),
    )
    res = ask(tmp_path / "core.db", "cc", "run it", runtime=rt)
    assert res["status"] == "denied_empty" and res["certainty"] == "TERMINAL"
    assert res["denied_actions"] == ["Bash: peerhub --version"]
    assert res["error"]["message"].startswith("cc denied tool action(s): Bash: peerhub --version")
    replay = ask(tmp_path / "core.db", "cc", "run it", request_id=res["request_id"], runtime=rt)
    assert replay["status"] == "denied_empty" and replay["denied_actions"] == ["Bash: peerhub --version"]
    assert replay["error"]["message"].startswith("cc denied tool action(s)")


def test_cc_denials_survive_stdout_truncation(bridge_h, tmp_path):
    """The result event is parsed incrementally; a flood after it that evicts it from the retained window must not lose denials."""
    result = json.loads(_result([{"tool_name": "Bash", "tool_input": {"command": "peerhub --version"}}]).splitlines()[1])
    script = textwrap.dedent(f"""
        import sys, json
        chunk = ("log line " * 10 + "\\n") * 2000
        for _ in range(3):
            sys.stdout.write(chunk)
        sys.stdout.write(json.dumps({result!r}) + "\\n")
        for _ in range(3):
            sys.stdout.write(chunk)
        sys.stdout.flush()
    """)
    ws = tmp_path / "ws_trunc"
    ws.mkdir()
    target = CliRuntimeTarget("cc", ws, command=[PY, "-c", script], timeout_s=15.0, max_bytes=100_000)
    (rec,) = bseed(bridge_h, stream="s_den_trunc")
    res = bridge_h.delivery_cycle("b", "s_den_trunc", target)
    assert res.certainty == "TERMINAL"
    (stored,) = [json.loads(r[0]) for r in sql(bridge_h, "SELECT result_json FROM bridge_deliveries WHERE record_id=?", (rec.record_id,))]
    assert stored["denied_actions"] == ["Bash: peerhub --version"]


def test_ask_denied_empty_guidance_is_provider_neutral():
    from peerhub.cli.recovery import REGISTRY

    entry = REGISTRY["ask_denied_empty"]
    assert "<peer>" not in entry.why
    argv = entry.recover[0]["argv"]
    assert argv[:3] == ["peerhub", "ask", "PEER_ID"] and "ag" not in argv
