"""Pin the whole static preamble of each PeerHub-owned prompt variant."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from peerhub.extensions.adapters import api
from peerhub.extensions.adapters.base import CliRuntimeTarget, PrespawnError, ProcessResult
from peerhub.extensions.adapters.common import MAX_PROMPT_BYTES
from peerhub.extensions.adapters.cx import CxSpec
from peerhub.extensions.catchup import ProjectedRecords
from peerhub.extensions.mcp import MCPServer


def assert_preamble(render, preamble, first, second):
    rendered = [render(*facts).encode("utf-8") for facts in (first, second)]
    expected = preamble.encode("utf-8")
    assert rendered[0][:len(expected)] == rendered[1][:len(expected)] == expected
    assert rendered[0][len(expected):] != rendered[1][len(expected):]


@pytest.mark.parametrize("history,truncated", [(True, False), (True, True), (False, True)])
def test_catch_up_prompt_preamble(history, truncated):
    def render(position, record_id, author, body, current):
        records = ProjectedRecords([
            SimpleNamespace(position=position, record_id=record_id, author_peer_id=author, kind="prompt", body=body),
        ] if history else [])
        records.boundary = {"truncated": truncated, "omitted_count": position - 1,
                            "omitted_through_position": position - 1} if truncated else {}
        return CliRuntimeTarget._prompt(SimpleNamespace(body=current), records)

    assert_preamble(
        render,
        "PeerHub stream context follows. Use the earlier records as context for the final request.\n\n",
        (4, "r4", "user", "first record", "first request"),
        (8, "r8", "other", "second record", "second request"),
    )


@pytest.mark.parametrize("extra_bytes", [0, 1])
def test_catch_up_preamble_is_optional_at_the_transport_limit(monkeypatch, extra_bytes):
    target = CliRuntimeTarget("cx", Path.cwd())
    history = [SimpleNamespace(position=1, record_id="r1", author_peer_id="user", kind="prompt", body="first")]
    prefix = "[1 r1 user prompt] first\n\n"
    # A multibyte final request pins the limit in bytes, independent of character count.
    current = "é" + "x" * (MAX_PROMPT_BYTES - len(prefix) - 2 + extra_bytes)

    def capture_argv(self, model, effort, prompt, writable, session):
        assert prompt == prefix + current
        raise PrespawnError("captured transport prompt")

    monkeypatch.setattr(target, "_base", lambda: ["unused"])
    monkeypatch.setattr(CxSpec, "argv", capture_argv)
    expected = "captured transport prompt" if not extra_bytes else f"exceeding the cx inline limit of {MAX_PROMPT_BYTES} bytes"
    with pytest.raises(PrespawnError, match=expected):
        list(target.deliver("session", SimpleNamespace(body=current), history))


def test_mcp_agent_context_prompt_preamble():
    def render(role, task):
        response = MCPServer(None)._get_prompt({"name": "peerhub_agent_context",
                                                "arguments": {"role": role, "task": task}})
        return response["messages"][0]["content"]["text"]

    assert_preamble(render, "You are operating within PeerHub.\n"
                    "Invariant: maintain Core imports Extension = 0 and route mutations through public ports.\n\n",
                    ("reviewer", "check this"), ("writer", "draft that"))


def test_ca_stdin_prompt_preamble(monkeypatch):
    rendered = []
    result = ProcessResult(123, 0, b'{"type":"message","stop_reason":"end_turn","content":[]}', b"", False, False, 0.1)

    def process(*args, stdin, **kwargs):
        rendered.append(json.loads(stdin)["messages"][0]["content"])
        return SimpleNamespace(start=lambda: None, wait=lambda: result)

    monkeypatch.setattr(api, "BoundedProcess", process)
    target = api.CaRuntimeTarget(Path.cwd(), command=["fake-ant"], model="claude-test")

    def render(prompt):
        target._call(prompt)
        return rendered[-1]

    assert_preamble(render, "PeerHub ask (text only, no tools, no files).\n\n",
                    ("@first.txt\nRead these facts",), ("Check different facts",))


@pytest.mark.parametrize("section,preamble", [
    ("Inline ag recipe prompt",
     "Use only the inline facts below. Do not use tools. Complete the requested task and return the result.\n\n"),
    ("ag file-read recipe prompt",
     "Use only your file read tool. If a file cannot be read, say so. Complete the task below.\n\n"),
    ("Fresh-stream handoff prompt",
     "Continue the work using the decisions, open work, and file references below.\n\n"),
    ("Batch task prompt",
     "Complete the task below. Report the result and any blockers.\n\n"),
    ("Batch vote prompt",
     "Review the proposal and evidence below. Reply in at most 300 tokens.\n"
     "VERDICT: AGREE or DISAGREE\nBLOCKERS: file:line and the exact fix for each, or none\nNITS: optional, one line each\n\n"),
])
def test_documented_prompt_preamble(section, preamble):
    batch_doc = Path(__file__).resolve().parents[3] / "docs" / "batch.md"
    _, marker, body = batch_doc.read_text(encoding="utf-8").partition(f"### {section}\n\n```text\n")
    assert marker
    template = body.split("\n```", 1)[0]
    assert template == preamble + "<facts>"
    assert_preamble(lambda facts: template.replace("<facts>", facts), preamble,
                    ("first facts",), ("different facts",))
