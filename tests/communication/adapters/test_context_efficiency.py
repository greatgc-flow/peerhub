"""Stage 1: reported usage and explicit, compact catch-up prompts."""
from types import SimpleNamespace

import pytest

from peerhub.extensions.adapters import base
from peerhub.extensions.catchup import ProjectedRecords

pytestmark = pytest.mark.unit

SAMPLES = [
    ("cc", '{"type":"system","subtype":"init"}\n'
     '{"type":"result","is_error":false,"result":"OK","usage":{"input_tokens":10,'
     '"cache_read_input_tokens":20514,"cache_creation_input_tokens":9576,"output_tokens":85}}\n',
     {"input_tokens": 30100, "cached_input_tokens": 20514, "cache_write_tokens": 9576, "output_tokens": 85}),
    ("cx", '{"type":"item.completed","item":{"type":"agent_message","text":"OK"}}\n'
     '{"type":"turn.completed","usage":{"input_tokens":18351,"cached_input_tokens":18176,'
     '"cache_write_input_tokens":0,"output_tokens":10,"reasoning_output_tokens":0}}\n',
     {"input_tokens": 18351, "cached_input_tokens": 18176, "cache_write_tokens": 0, "output_tokens": 10}),
    ("ag", '{"response":"OK","usage":{"input_tokens":11632,"output_tokens":2,"thinking_tokens":0,'
     '"cache_read_tokens":0,"total_tokens":11634}}',
     {"input_tokens": 11632, "cached_input_tokens": 0, "output_tokens": 2}),
]


@pytest.mark.parametrize("kind,stdout,expected", SAMPLES)
def test_literal_usage_is_normalized_and_carried_in_terminal(kind, stdout, expected, monkeypatch, tmp_path):
    assert base.SPECS[kind].parse_usage(stdout) == expected
    result = base.ProcessResult(123, 0, stdout.encode(), b"", False, False, 0.1)
    process = SimpleNamespace(pid=123, start=lambda: None, wait=lambda: result)
    monkeypatch.setattr(base, "BoundedProcess", lambda *a, **kw: process)
    adapter = base.CliRuntimeTarget(kind, tmp_path, command=["fake.exe"])
    events = list(adapter.deliver("session", SimpleNamespace(body="question"), []))
    assert events[-1] == ("terminal", {"response": "OK", "usage": expected})


@pytest.mark.parametrize("kind", ["cc", "cx", "ag"])
@pytest.mark.parametrize("stdout", ["", "not JSON", "[]", '{}', '{"usage":{}}'])
def test_absent_usage_is_unknown(kind, stdout):
    assert base.SPECS[kind].parse_usage(stdout) is None


@pytest.mark.parametrize("kind,stdout,expected", [
    ("cc", '{"type":"result","usage":{"input_tokens":10,"output_tokens":0}}', {"output_tokens": 0}),
    ("cc", '{"type":"result","usage":{"cache_read_input_tokens":0}}', {"cached_input_tokens": 0}),
    ("cx", '{"type":"turn.completed","usage":{"input_tokens":20}}', {"input_tokens": 20}),
    ("ag", '{"usage":{"output_tokens":0}}', {"output_tokens": 0}),
    ("ag", '{"usage":{"input_tokens":null,"cache_read_tokens":false,"output_tokens":-1}}', None),
    ("cx", '{"type":"turn.completed","usage":{"output_tokens":"10"}}', None),
    ("cc", '{"type":"assistant","usage":{"input_tokens":10}}', None),
    ("cx", '{"type":"item.completed","usage":{"input_tokens":10}}', None),
])
def test_partial_usage_omits_unknown_counters(kind, stdout, expected):
    assert base.SPECS[kind].parse_usage(stdout) == expected


@pytest.mark.parametrize("truncated,omitted", [(True, 3), (False, 3)])
def test_catch_up_prompt_has_one_boundary_then_identified_records_then_current_ask(truncated, omitted):
    history = ProjectedRecords([
        SimpleNamespace(position=4, record_id="r4", author_peer_id="user", kind="prompt", body="old question"),
        SimpleNamespace(position=5, record_id="r5", author_peer_id="cx", kind="response", body={"fact": "known"}),
    ])
    history.boundary = {"truncated": truncated, "omitted_count": omitted, "omitted_through_position": 3}
    prompt = base.CliRuntimeTarget._prompt(SimpleNamespace(body="current question"), history)
    assert prompt == ('[context truncated: 3 earlier records omitted through position 3]\n\n'
                      '[4 r4 user prompt] old question\n\n'
                      '[5 r5 cx response] {"fact": "known"}\n\ncurrent question')
    assert prompt.count("[context truncated:") == 1


def test_empty_truncated_history_still_reports_boundary():
    history = ProjectedRecords()
    history.boundary = {"truncated": True, "omitted_count": 2, "omitted_through_position": 2}
    assert base.CliRuntimeTarget._prompt(SimpleNamespace(body="now"), history) == (
        "[context truncated: 2 earlier records omitted through position 2]\n\nnow")


@pytest.mark.parametrize("boundary", [None, {"truncated": False, "omitted_count": 0}])
def test_complete_history_has_no_boundary_note(boundary):
    history = ProjectedRecords([
        SimpleNamespace(position=1, record_id="r1", author_peer_id="user", kind="prompt", body="earlier"),
    ])
    history.boundary = boundary
    assert base.CliRuntimeTarget._prompt(SimpleNamespace(body="now"), history) == "[1 r1 user prompt] earlier\n\nnow"
    assert base.CliRuntimeTarget._prompt(SimpleNamespace(body="now"), []) == "now"
