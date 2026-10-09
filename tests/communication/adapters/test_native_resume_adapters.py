"""Stage 1 native session identities, argv, binding and explicit adapter opt-in."""
import json
import os
from types import SimpleNamespace

import pytest

from peerhub.extensions.adapters import base
from peerhub.extensions.bridge import RuntimeTargetError

pytestmark = pytest.mark.unit

CC = '{"type":"result","subtype":"success","is_error":false,"result":"OK","session_id":"opaque.A_1:-"}\n'
CX = ('{"type":"thread.started","thread_id":"opaque.A_1:-"}\n'
      '{"type":"item.completed","item":{"type":"agent_message","text":"OK"}}\n'
      '{"type":"turn.completed","usage":{"input_tokens":200,"cached_input_tokens":150,"output_tokens":10}}\n')

AG = '{"response":"OK","conversation_id":"opaque.A_1:-","usage":{"input_tokens":200,"cache_read_tokens":150,"output_tokens":10}}'


@pytest.mark.parametrize("kind,stdout,expected", [
    ("cc", CC, "opaque.A_1:-"),
    ("cx", CX, "opaque.A_1:-"),
    ("cc", CC + CC, "opaque.A_1:-"),
    ("cc", '{"type":"system","session_id":"opaque.A_1:-"}\n' + CC, "opaque.A_1:-"),
    ("cc", '{"type":"system","session_id":"other"}\n' + CC, None),
    ("cx", CX + '{"type":"turn.completed","thread_id":"other"}', None),
    ("cx", CX + CX, "opaque.A_1:-"),
    ("cc", CC + '{"type":"result","is_error":false,"result":"OK","session_id":"other"}', None),
    ("cx", CX + '{"type":"thread.started","thread_id":"other"}', None),
    ("cc", '{"type":"result","is_error":false,"result":"OK"}', None),
    ("cx", '{"type":"thread.started"}', None),
    ("cc", '{"type":"system","session_id":"ignored"}', None),
    ("cx", '{"type":"turn.completed","thread_id":"ignored"}', None),
    ("cc", '{"type":"result","is_error":true,"session_id":"failed"}', None),
    ("cc", '{"type":"result","is_error":false,"subtype":"error","session_id":"failed"}', None),
    ("cc", '{"type":"result","session_id":"missing-success"}', None),
    ("cc", CC + '{"type":"result","is_error":false,"result":"OK"}', None),
    ("cc", CC + '{"type":"result","is_error":true,"session_id":"opaque.A_1:-"}', "opaque.A_1:-"),
    ("cc", CC + '{"type":"result","is_error":true,"session_id":"different"}', None),
    ("ag", AG, "opaque.A_1:-"),
    ("ag", '{"response":"OK","session_id":"ignored"}', None),
    ("ag", '{"response":"OK","conversation_id":', None),
    ("ag", '[{"conversation_id":"ignored"}]', None),
    ("ag", 'prefix ' + AG, None),
    ("ag", AG + AG, None),
])
def test_literal_session_id_events(kind, stdout, expected):
    assert base.SPECS[kind].parse_session_id(stdout) == expected


@pytest.mark.parametrize("kind,sample", [("cc", CC), ("cx", CX), ("ag", AG)])
@pytest.mark.parametrize("bad_id", ["", "a" * 201, "bad space", "bad/id", "bad\\id", "bad\n", "badé", "--last;"])
def test_session_id_bounds_and_charset(kind, sample, bad_id):
    stdout = sample.replace('"opaque.A_1:-"', json.dumps(bad_id))
    assert base.SPECS[kind].parse_session_id(stdout) is None
    assert base.SPECS[kind].parse_session_id(sample + stdout) is None


@pytest.mark.parametrize("kind,sample", [("cc", CC), ("cx", CX), ("ag", AG)])
@pytest.mark.parametrize("bad_id", [None, 42, True, {}, []])
def test_session_id_must_be_a_string(kind, sample, bad_id):
    assert base.SPECS[kind].parse_session_id(sample.replace('"opaque.A_1:-"', json.dumps(bad_id))) is None


@pytest.mark.parametrize("kind,sample", [("cc", CC), ("cx", CX), ("ag", AG)])
def test_session_id_at_length_limit_and_missing_output(kind, sample):
    assert base.SPECS[kind].parse_session_id(sample.replace("opaque.A_1:-", "a" * 200)) == "a" * 200
    for stdout in ("", "not JSON", "[]", "{}", '{"type":'):
        assert base.SPECS[kind].parse_session_id(stdout) is None


@pytest.mark.parametrize("writable", [False, True])
def test_cc_fresh_and_resume_argv(writable):
    spec = base.CcSpec()
    common = ["--model", "m-test", "--effort", "high"]
    if writable:
        common += ["--permission-mode", "acceptEdits"]
    prefix = ["-p", "-", "--output-format", "stream-json", "--verbose"]
    assert spec.argv("m-test", "high", "question", writable) == prefix + common
    resumed = spec.argv("m-test", "high", "question", writable, "opaque.A_1:-")
    assert resumed == prefix + ["--resume", "opaque.A_1:-"] + common
    assert "latest" not in resumed and "--last" not in resumed


@pytest.mark.parametrize("writable", [False, True])
@pytest.mark.parametrize("model,effort", [(None, None), ("m-test", "high")])
def test_cx_fresh_and_resume_argv(writable, model, effort):
    spec = base.CxSpec()
    common = ["-m", model, "-c", f"model_reasoning_effort={effort}"] if model else []
    common += ["--json", "-"]
    fresh = ["exec", "--skip-git-repo-check", "-s", "workspace-write" if writable else "read-only"]
    assert spec.argv(model, effort, "question", writable) == fresh + common
    resumed = spec.argv(model, effort, "question", writable, "opaque.A_1:-")
    assert resumed == ["exec", "resume", "opaque.A_1:-", "--skip-git-repo-check"] + common
    assert "-s" not in resumed and "latest" not in resumed and "--last" not in resumed


@pytest.mark.parametrize("writable", [False, True])
@pytest.mark.parametrize("model,effort", [(None, None), ("m-test", "high")])
def test_ag_fresh_and_resume_argv(writable, model, effort):
    spec = base.AgSpec()
    fresh = ["-p", "question", "--output-format", "json"]
    if model:
        fresh += ["--model", model, "--effort", effort]
    if writable:
        fresh += ["--mode", "accept-edits"]
    assert spec.argv(model, effort, "question", writable) == fresh
    assert spec.argv(model, effort, "question", writable, "opaque.A_1:-") == (
        fresh + ["--conversation", "opaque.A_1:-"])


@pytest.mark.parametrize("kind,sample,source", [("cc", CC, "cc.result.session_id"), ("cx", CX, "cx.thread.started"),
                                                   ("ag", AG, "ag.conversation_id")])
@pytest.mark.parametrize("resume", [False, True])
def test_terminal_candidate_and_explicit_binding(kind, sample, source, resume, monkeypatch, tmp_path):
    invocations = []
    result = base.ProcessResult(123, 0, sample.encode(), b"", False, False, 0.1)

    def process(argv, **kwargs):
        invocations.append((argv, kwargs))
        return SimpleNamespace(pid=123, start=lambda: None, wait=lambda: result)

    monkeypatch.setattr(base, "BoundedProcess", process)
    adapter = base.CliRuntimeTarget(kind, tmp_path, command=["fake.exe"], resume=resume)
    session = adapter.create_session()
    record = SimpleNamespace(body="question")
    first = list(adapter.deliver(session, record, []))
    expected = {"response": "OK", "vendor_session": {"id": "opaque.A_1:-", "source": source}}
    if kind in ("cx", "ag"):
        expected["usage"] = {"input_tokens": 200, "cached_input_tokens": 150, "output_tokens": 10}
    assert first[-1] == ("terminal", expected)
    assert invocations[-1][0] == ["fake.exe", *base.SPECS[kind].argv(None, None, "question")]
    assert adapter.resumable is resume
    assert adapter.resume_session(session) == ("missing" if resume else "unsupported")
    outcome = adapter.resume_session(session, vendor_session_id=first[-1][1]["vendor_session"]["id"])
    assert outcome == ("ok" if resume else "unsupported")
    assert list(adapter.deliver(session, record, []))[-1] == ("terminal", expected)
    assert invocations[-1][0] == ["fake.exe", *base.SPECS[kind].argv(
        None, None, "question", vendor_session_id="opaque.A_1:-" if resume else None)]
    assert invocations[-1][1]["stdin"] == (None if kind == "ag" else b"question")
    # Vendor bindings do not leak to a different local session.
    list(adapter.deliver(adapter.create_session(), record, []))
    assert invocations[-1][0] == invocations[0][0]


@pytest.mark.parametrize("kind,sample", [("cc", CC), ("cx", CX), ("ag", AG)])
def test_invalid_vendor_binding_and_failed_terminal(kind, sample, monkeypatch, tmp_path):
    adapter = base.CliRuntimeTarget(kind, tmp_path, command=["fake.exe"], resume=True)
    assert adapter.resume_session("session", vendor_session_id="bad/id") == "rejected"
    assert adapter.resume_session("session", vendor_session_id="a" * 201) == "rejected"
    result = base.ProcessResult(123, 1, sample.encode(), b"error", False, False, 0.1)
    monkeypatch.setattr(base, "BoundedProcess", lambda *a, **kw: SimpleNamespace(
        pid=123, start=lambda: None, wait=lambda: result))
    events = adapter.deliver("session", SimpleNamespace(body="question"), [])
    assert next(events)[0] == "started"
    with pytest.raises(RuntimeTargetError):
        next(events)


@pytest.mark.parametrize("kind,sample", [("cc", CC), ("cx", CX)])
def test_conflicting_ids_omit_terminal_candidate(kind, sample, monkeypatch, tmp_path):
    stdout = sample + sample.replace("opaque.A_1:-", "other")
    result = base.ProcessResult(123, 0, stdout.encode(), b"", False, False, 0.1)
    monkeypatch.setattr(base, "BoundedProcess", lambda *a, **kw: SimpleNamespace(
        pid=123, start=lambda: None, wait=lambda: result))
    adapter = base.CliRuntimeTarget(kind, tmp_path, command=["fake.exe"])
    events = list(adapter.deliver("session", SimpleNamespace(body="question"), []))
    assert "vendor_session" not in events[-1][1]


@pytest.mark.parametrize("kind", ["cc", "cx", "ag"])
def test_binding_includes_canonical_cwd_and_writable_and_version(tmp_path, kind):
    adapter = base.CliRuntimeTarget(kind, tmp_path)
    provider, _, scope = adapter.binding().partition("#")
    assert provider == "default/default" and len(scope) == 12  # a short digest of cwd+mode: no local path in Records
    assert str(tmp_path) not in adapter.binding()
    assert adapter.binding() == base.CliRuntimeTarget(kind, tmp_path / "child" / "..").binding()
    assert adapter.binding() != base.CliRuntimeTarget(kind, tmp_path / "other").binding()
    assert adapter.binding() != base.CliRuntimeTarget(kind, tmp_path, writable=True).binding()
    assert adapter.fingerprint() == f"adapter:{kind}:v2"


@pytest.mark.parametrize("kind,resume", [("cc", False), ("cc", True), ("cx", False), ("cx", True), ("ag", False), ("ag", True)])
def test_resume_capability_is_explicit_and_controls_remain_unsupported(kind, resume, tmp_path, monkeypatch):
    adapter = base.CliRuntimeTarget(kind, tmp_path, resume=resume)
    monkeypatch.setattr(adapter, "_run", lambda args, timeout: base.ProcessResult(
        123, 0, f"CLI 1.2.3 {base.SPECS[kind].resume_flag}".encode(), b"", False, False, 0.1))
    caps = adapter.discover()["capabilities"]
    assert caps["resume"]["status"] == ("supported" if resume else "unsupported")
    assert caps["resume"]["cli_status"] == "supported"
    assert not adapter.supports_interrupt and not adapter.supports_steer
    assert caps["interrupt"]["status"] == caps["steer"]["status"] == "unsupported"
