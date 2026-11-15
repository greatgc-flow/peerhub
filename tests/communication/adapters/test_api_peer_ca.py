"""Peer `ca` (#378): one Messages API call through the `ant` CLI; a python fake `ant` is injected, no provider is called."""
import json
import sqlite3
import sys
import threading
import time
from pathlib import Path

import pytest

from peerhub.extensions.adapters import api
from peerhub.extensions.adapters.api import CA_HEADER, CaRuntimeTarget, RequestRejected
from peerhub.extensions.adapters.profiles import resolve_profile
from peerhub.extensions.ask import ask

FAKE_ANT = str(Path(__file__).resolve().parents[1] / "harness" / "fake_ant.py")
HTTP_ERROR = 'POST "https://api.anthropic.com/v1/messages": {status} {phrase} {body}\n'


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch):
    monkeypatch.setenv("PEERHUB_CONFIG_HOME", str(tmp_path / "config"))


@pytest.fixture
def ca(tmp_path):
    """Factory: a ca target over the fake `ant`. `make.log` is the launch log, `make.db` a store path in tmp_path."""
    log = tmp_path / "ant.jsonl"

    def make(stdout=None, stderr="", code=0, sleep=0.0, **kw):
        env = {"FAKE_ANT_LOG": str(log), "FAKE_ANT_EXIT": str(code), "FAKE_ANT_STDERR": stderr, "FAKE_ANT_SLEEP": str(sleep)}
        if stdout is not None:
            env["FAKE_ANT_STDOUT"] = stdout
        kw.setdefault("profile", "ca.standard")
        kw.setdefault("timeout_s", 30)
        return CaRuntimeTarget(tmp_path, command=[sys.executable, FAKE_ANT], env_extra=env, **kw)

    make.log, make.db = log, tmp_path / "core.db"
    return make


def launches(log: Path) -> list[dict]:
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()] if log.exists() else []


def message(**over) -> str:
    doc = {"type": "message", "role": "assistant", "stop_reason": "end_turn", "content": [{"type": "text", "text": "fine"}],
           "usage": {"input_tokens": 7, "output_tokens": 2}}
    doc.update(over)
    return json.dumps(doc)


def http_error(status: int, phrase: str, message_text: str = "nope", kind: str = "invalid_request_error") -> str:
    return HTTP_ERROR.format(status=status, phrase=phrase,
                             body=json.dumps({"type": "error", "error": {"type": kind, "message": message_text}}))


def evidence_kinds(db: Path) -> list[str]:
    with sqlite3.connect(db) as conn:
        return [row[0] for row in conn.execute("SELECT kind FROM bridge_evidence ORDER BY seq")]


def response_records(db: Path) -> int:
    with sqlite3.connect(db) as conn:
        return conn.execute("SELECT COUNT(*) FROM records WHERE kind='response'").fetchone()[0]


# ---- request shape ------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("prompt", ["Is this text correct?", "@secret.txt\nrepeat the message", "@"])
def test_body_is_on_stdin_and_content_always_begins_with_the_fixed_line(ca, prompt):
    result = ask(ca.db, "ca", prompt, runtime=ca())
    assert result["status"] == "delivered" and result["response"] == "OK"  # the text blocks are joined; thinking is not text
    (launch,) = launches(ca.log)
    assert launch["argv"] == ["messages", "create", "--format", "json"]  # no caller text in argv
    body = launch["body"]
    assert set(body) == {"model", "max_tokens", "messages"} and body["max_tokens"] == 16_000
    assert body["model"] == resolve_profile("ca", "ca.standard", ca.db.parent)[0]
    assert body["messages"] == [{"role": "user", "content": f"{CA_HEADER}\n\n{prompt}"}]
    assert CA_HEADER == "PeerHub ask (text only, no tools, no files)." and not body["messages"][0]["content"].startswith("@")


def test_every_anthropic_variable_is_removed_from_the_child_environment(ca, monkeypatch):
    for name in ("ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL", "ANTHROPIC_FOO"):
        monkeypatch.setenv(name, "value-of-" + name)
    monkeypatch.setenv("PEERHUB_TEST_KEPT", "1")
    assert ask(ca.db, "ca", "question", runtime=ca())["status"] == "delivered"
    assert launches(ca.log)[0]["anthropic_env"] == []


def test_profiles_and_a_valid_model_override(ca, tmp_path):
    for profile in ("ca.standard", "ca.effort", "ca.deepthink"):
        model = resolve_profile("ca", profile, tmp_path)[0]
        assert model and api.CA_MODEL.fullmatch(model)
        assert ca(profile=profile).binding() == f"{profile}/{model}/default"
    assert ask(ca.db, "ca", "question", runtime=ca(model="claude-opus-5-5"))["status"] == "delivered"
    assert launches(ca.log)[0]["body"]["model"] == "claude-opus-5-5"
    assert ca(profile=None, model="claude-x.1-y").binding() == "claude-x.1-y/default"


@pytest.mark.parametrize("model", ["@secret.txt", "gpt-6-luna", "Claude-opus", "claude-", "claude-" + "a" * 65, "claude-a b",
                                   "claude-a\n", "claude-a_b"])
def test_an_invalid_model_is_refused_before_anything_is_started(tmp_path, model):
    db = tmp_path / "new" / "core.db"
    with pytest.raises(RequestRejected) as caught:
        ask(db, "ca", "question", profile="ca.standard", model=model)
    assert caught.value.code == "request_rejected" and not db.parent.exists()


def test_writable_effort_and_a_missing_model_are_refused_before_anything_is_started(tmp_path):
    db = tmp_path / "new" / "core.db"
    with pytest.raises(RequestRejected, match="--writable"):
        ask(db, "ca", "question", profile="ca.standard", writable=True)
    with pytest.raises(RequestRejected, match="effort"):
        ask(db, "ca", "question", profile="ca.standard", effort="high")
    with pytest.raises(RequestRejected, match="--profile"):
        ask(db, "ca", "question")
    assert not db.parent.exists()


# ---- completion rule (design 5) -----------------------------------------------------------------------------------------
def test_a_complete_reply_is_delivered_with_usage_and_provider_ca(ca):
    result = ask(ca.db, "ca", "question", runtime=ca())
    assert (result["status"], result["certainty"], result["error"]) == ("delivered", "TERMINAL", None)
    assert result["usage"] == {"input_tokens": 12, "output_tokens": 3}
    assert (result["usage_scope"], result["usage_attribution"]) == ("delivery", "delivery")
    assert result["session"]["mode"] == "fresh"
    with sqlite3.connect(ca.db) as conn:
        detail = json.loads(conn.execute("SELECT detail FROM bridge_evidence WHERE kind='session_telemetry'").fetchone()[0])
        resumable = conn.execute("SELECT resumable FROM bridge_session_history").fetchone()[0]
    assert detail["provider_kind"] == "ca" and detail["adapter_version"] == "1"
    assert detail["usage"]["coverage"] == {"state": "MEASURED", "reason": None}
    assert detail["usage"]["source"] == "ca.message.usage" and detail["usage"]["scope"] == "delivery"
    assert detail["usage"]["reported"] == {"input_tokens": 12, "output_tokens": 3}
    assert detail["usage"]["normalized"] == {"provider_input_tokens": 12, "output_tokens": 3}
    assert resumable == 0


def test_stop_sequence_is_complete_and_empty_text_is_empty_as_for_other_peers(ca):
    done = ask(ca.db, "ca", "question", runtime=ca(stdout=message(stop_reason="stop_sequence")))
    assert done["status"] == "delivered" and done["response"] == "fine"
    empty = ask(ca.db, "ca", "question", stream_id="empty", runtime=ca(stdout=message(content=[{"type": "text", "text": "  "}])))
    assert empty["status"] == "delivered" and empty["empty_response"] is True


def test_max_tokens_is_truncated_and_keeps_the_partial_text_out_of_the_stream(ca):
    result = ask(ca.db, "ca", "question", request_id="r1", runtime=ca(stdout=message(stop_reason="max_tokens")))
    assert (result["status"], result["certainty"], result["response"]) == ("truncated", "STARTED", None)
    assert result["error"]["code"] == "truncated" and "max_tokens" in result["error"]["message"]
    assert result["partial_response"] == "fine" and result["usage"] == {"input_tokens": 7, "output_tokens": 2}
    assert response_records(ca.db) == 0
    again = ask(ca.db, "ca", "question", request_id="r1", runtime=ca())  # the settled result is replayed, `ant` is not started
    assert again["status"] == "truncated" and again["partial_response"] == "fine" and len(launches(ca.log)) == 1


def test_another_stop_reason_is_incomplete_started_and_not_uncertain(ca):
    result = ask(ca.db, "ca", "question", runtime=ca(stdout=message(stop_reason="refusal")))
    assert (result["status"], result["certainty"], result["response"]) == ("incomplete", "STARTED", None)
    assert result["error"]["code"] == "incomplete" and "refusal" in result["error"]["message"]
    assert result["partial_response"] == "fine" and response_records(ca.db) == 0
    assert "start_uncertain" not in evidence_kinds(ca.db) and "runtime_error" not in evidence_kinds(ca.db)


@pytest.mark.parametrize("stdout,code", [
    (message(), 3),  # a non-zero exit with a full JSON
    ("this is not JSON", 0),
    (message() + "\n" + message(), 0),  # two objects are not one object
    (message(type="other"), 0),
    (message(content="text"), 0),
    (message(stop_reason=None), 0),
    (message()[:-5], 0),  # partial output
    (message()[:-5], 1),
    ("", 0),
])
def test_each_condition_alone_makes_the_reply_uncertain(ca, stdout, code):
    result = ask(ca.db, "ca", "question", runtime=ca(stdout=stdout, code=code))
    assert (result["status"], result["response"]) == ("uncertain", None)
    assert response_records(ca.db) == 0 and len(launches(ca.log)) == 1  # never retried


# ---- failure table (design 6) -------------------------------------------------------------------------------------------
@pytest.mark.parametrize("status,phrase", [(400, "Bad Request"), (401, "Unauthorized"), (403, "Forbidden"), (404, "Not Found"),
                                           (413, "Request Entity Too Large"), (429, "Too Many Requests")])
@pytest.mark.parametrize("stream", ["stdout", "stderr"])
def test_an_error_object_with_a_listed_status_is_not_started(ca, status, phrase, stream):
    text = http_error(status, phrase, "the request was refused")
    target = ca(stdout=text, code=1) if stream == "stdout" else ca(stdout="", stderr=text, code=1)
    result = ask(ca.db, "ca", "question", runtime=target)
    assert (result["status"], result["certainty"]) == ("failed_not_started", "NOT_STARTED")
    assert f"HTTP {status}" in result["detail"]["error"] and "the request was refused" in result["detail"]["error"]
    assert "prespawn_failure" in evidence_kinds(ca.db) and "started" not in evidence_kinds(ca.db)


def test_a_low_credit_balance_is_a_provider_limit_without_a_reset_time(ca):
    text = http_error(400, "Bad Request", "Your credit balance is too low to access the Anthropic API.")
    result = ask(ca.db, "ca", "question", runtime=ca(stdout="", stderr=text, code=1))
    assert (result["status"], result["task_outcome"], result["reason"]) == ("not_executed", "NOT_EXECUTED", "quota_exhausted")
    assert result.get("reset_time") is None and result["error"]["code"] == "quota_exhausted"
    assert "credit balance is too low" in result["message"]


@pytest.mark.parametrize("stderr", [
    http_error(500, "Internal Server Error", "boom", "api_error"),
    http_error(529, "Overloaded", "overloaded", "overloaded_error"),
    http_error(402, "Payment Required"),  # a status that is not listed
    'POST "https://api.anthropic.com/v1/messages": 400 Bad Request\n',  # a status without an error object
    json.dumps({"type": "error", "error": {"type": "invalid_request_error", "message": "no status"}}),
    "panic: something else\n",
])
def test_every_other_failure_is_uncertain_and_never_retried(ca, stderr):
    result = ask(ca.db, "ca", "question", runtime=ca(stdout="", stderr=stderr, code=1))
    assert result["status"] == "uncertain" and result["certainty"] in ("STARTED", "MAY_HAVE_STARTED")
    assert len(launches(ca.log)) == 1


def test_a_timeout_is_uncertain(ca):
    result = ask(ca.db, "ca", "question", runtime=ca(sleep=30, timeout_s=1.5))
    assert result["status"] == "uncertain" and "runtime_timeout" in evidence_kinds(ca.db)


def test_ant_missing_or_not_logged_in_is_a_prespawn_failure(ca, tmp_path, monkeypatch):
    monkeypatch.setattr(api.shutil, "which", lambda name: None)
    missing = ask(ca.db, "ca", "question", runtime=CaRuntimeTarget(tmp_path, profile="ca.standard"))
    assert (missing["status"], missing["certainty"]) == ("failed_not_started", "NOT_STARTED")
    assert missing["detail"]["error"] == "ant executable not found"
    unstartable = ask(ca.db, "ca", "question", stream_id="s2",
                      runtime=CaRuntimeTarget(tmp_path, profile="ca.standard", command=[str(tmp_path / "no-such-ant")]))
    assert unstartable["status"] == "failed_not_started" and unstartable["detail"]["error"].startswith("cannot spawn ant")
    login = ask(ca.db, "ca", "question", stream_id="s3", runtime=ca(stdout="", stderr="Not logged in. Run `ant auth login`.\n", code=1))
    assert (login["status"], login["certainty"]) == ("failed_not_started", "NOT_STARTED")
    assert "ant auth login" in login["detail"]["error"]
    assert evidence_kinds(ca.db).count("prespawn_failure") == 3


def test_a_secret_in_an_error_is_redacted(ca, monkeypatch):
    monkeypatch.setenv("PEERHUB_TEST_TOKEN", "hunter2-hunter2-value")
    text = http_error(401, "Unauthorized", "bad key sk-ant-abcdefgh12345678 and hunter2-hunter2-value", "authentication_error")
    result = ask(ca.db, "ca", "the question itself", runtime=ca(stdout="", stderr=text, code=1))
    shown = json.dumps(result)
    assert "[REDACTED]" in result["detail"]["error"]
    assert "sk-ant-abcdefgh12345678" not in shown and "hunter2-hunter2-value" not in shown
    assert "sk-ant-abcdefgh12345678".encode() not in ca.db.read_bytes()


# ---- one ask per stream (design 4) --------------------------------------------------------------------------------------
@pytest.mark.parametrize("first,status", [
    ({}, "delivered"),
    ({"stdout": "", "stderr": http_error(401, "Unauthorized"), "code": 1}, "failed_not_started"),
    ({"stdout": "", "stderr": http_error(500, "Internal Server Error"), "code": 1}, "uncertain"),
    ({"stdout": message(stop_reason="max_tokens")}, "truncated"),
])
def test_a_second_ask_on_a_stream_is_refused_before_ant_is_started(ca, first, status):
    assert ask(ca.db, "ca", "first", stream_id="s", request_id="one", runtime=ca(**first))["status"] == status
    with pytest.raises(RequestRejected, match="ca is stateless; use a new stream") as caught:
        ask(ca.db, "ca", "second", stream_id="s", request_id="two", runtime=ca())
    assert caught.value.code == "request_rejected" and len(launches(ca.log)) == 1
    with sqlite3.connect(ca.db) as conn:  # nothing of the second ask was stored
        assert conn.execute("SELECT COUNT(*) FROM records WHERE kind='prompt'").fetchone()[0] == 1
    assert ask(ca.db, "ca", "other", stream_id="another", runtime=ca())["status"] == "delivered"


def test_a_second_ask_is_refused_while_the_first_is_still_running(ca):
    results: list[dict] = []
    first = threading.Thread(target=lambda: results.append(ask(ca.db, "ca", "first", stream_id="s", runtime=ca(sleep=3))))
    first.start()
    try:
        deadline = time.monotonic() + 20
        while not launches(ca.log) and time.monotonic() < deadline:
            time.sleep(0.05)
        assert launches(ca.log), "the first ask never started the fake ant"
        with pytest.raises(RequestRejected, match="ca is stateless; use a new stream"):
            ask(ca.db, "ca", "second", stream_id="s", runtime=ca())
    finally:
        first.join(30)
    assert results[0]["status"] == "delivered" and len(launches(ca.log)) == 1


def test_the_caller_may_repeat_a_request_that_was_proven_not_started(ca):
    """PeerHub never retries; the same request-id after a not-started answer (for example after `ant auth login`) runs once more."""
    failed = ask(ca.db, "ca", "question", stream_id="s", request_id="one",
                 runtime=ca(stdout="", stderr=http_error(401, "Unauthorized"), code=1))
    assert failed["status"] == "failed_not_started"
    again = ask(ca.db, "ca", "question", stream_id="s", request_id="one", runtime=ca())
    assert again["status"] == "delivered" and again["response"] == "OK" and len(launches(ca.log)) == 2


def test_the_same_request_replays_its_stored_reply_without_a_second_call(ca):
    first = ask(ca.db, "ca", "question", stream_id="s", request_id="one", runtime=ca())
    again = ask(ca.db, "ca", "question", stream_id="s", request_id="one", runtime=ca())
    assert first["status"] == "delivered" and again["status"] == "recovered_terminal" and again["response"] == "OK"
    assert len(launches(ca.log)) == 1


# ---- wiring -------------------------------------------------------------------------------------------------------------
def test_ask_builds_the_ca_target_for_the_builtin_peer_and_a_registered_peer(tmp_path, monkeypatch):
    from peerhub.core.models import Peer
    from peerhub.core.store import CoreStore

    log = tmp_path / "ant.jsonl"
    monkeypatch.setitem(api.API_TARGETS, "ca", lambda workspace, **kw: CaRuntimeTarget(
        workspace, command=[sys.executable, FAKE_ANT], env_extra={"FAKE_ANT_LOG": str(log)}, **kw))
    db = tmp_path / "core.db"
    result = ask(db, "ca", "question", workspace=tmp_path, profile="standard")
    assert result["status"] == "delivered" and result["peer_id"] == "ca"
    CoreStore(db).register_peer(Peer(peer_id="voter", adapter_ref="ca"))
    assert ask(db, "voter", "question", workspace=tmp_path, profile="ca.effort")["status"] == "delivered"
    assert [launch["body"]["model"] for launch in launches(log)] == [resolve_profile("ca", name, tmp_path)[0]
                                                                    for name in ("ca.standard", "ca.effort")]


def test_cli_reports_request_rejected_and_truncated(tmp_path, monkeypatch, capsys):
    from peerhub.cli.app import main

    db = str(tmp_path / "core.db")
    monkeypatch.setitem(api.API_TARGETS, "ca", lambda workspace, **kw: CaRuntimeTarget(
        workspace, command=[sys.executable, FAKE_ANT], env_extra={"FAKE_ANT_STDOUT": message(stop_reason="max_tokens")}, **kw))
    base = ["--db", db, "ask", "ca", "question", "-w", str(tmp_path), "--profile", "ca.standard", "--json"]
    assert main([*base, "--writable"]) == 2
    rejected = json.loads(capsys.readouterr().out)
    assert rejected["status"] == "error" and rejected["error"]["code"] == "request_rejected"
    assert main([*base, "--stream", "s1"]) == 1
    captured = capsys.readouterr()
    truncated = json.loads(captured.out)
    assert truncated["status"] == "truncated" and truncated["error"]["code"] == "truncated" and "guidance" not in truncated
    assert captured.err.startswith("[truncated] ")
    assert main([*base, "--stream", "s1", "--request-id", "other"]) == 2
    assert json.loads(capsys.readouterr().out)["error"]["message"].endswith("ca is stateless; use a new stream")
