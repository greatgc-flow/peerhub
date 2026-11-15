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
from tests.communication.harness.api_peers import (ANT_NO_CREDIT, ANT_NOT_SIGNED_IN, ANT_UNKNOWN_MODEL, ant_error,
                                                   evidence_kinds)

FAKE_ANT = str(Path(__file__).resolve().parents[1] / "harness" / "fake_ant.py")
SERVER_ERROR = ant_error(500, "Internal Server Error", "api_error", "Internal server error")


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
@pytest.mark.parametrize("status,phrase,kind", [
    (400, "Bad Request", "invalid_request_error"), (401, "Unauthorized", "permission_error"), (403, "Forbidden", "permission_error"),
    (404, "Not Found", "not_found_error"), (413, "Request Entity Too Large", "request_too_large"),
    (429, "Too Many Requests", "rate_limit_error")])
def test_an_error_object_with_a_listed_status_is_not_started(ca, status, phrase, kind):
    result = ask(ca.db, "ca", "question", runtime=ca(**ant_error(status, phrase, kind, "the request was refused")))
    assert (result["status"], result["certainty"]) == ("failed_not_started", "NOT_STARTED")
    assert result["detail"]["error"] == f"ca: HTTP {status}: {kind}: the request was refused"
    assert "prespawn_failure" in evidence_kinds(ca.db) and "started" not in evidence_kinds(ca.db)


def test_the_measured_outputs_of_the_real_ant_are_classified(ca):
    """Pinned from `ant` 2.9.0: the error object on stdout (several lines), the status line on stderr, exit code 1."""
    assert ANT_NOT_SIGNED_IN["stdout"].count("\n") > 3 and ANT_NOT_SIGNED_IN["code"] == 1
    assert ANT_NOT_SIGNED_IN["stderr"] == 'POST "https://api.anthropic.com/v1/messages": 401 Unauthorized\n'
    login = ask(ca.db, "ca", "question", runtime=ca(**ANT_NOT_SIGNED_IN))
    assert (login["status"], login["certainty"]) == ("failed_not_started", "NOT_STARTED")
    assert login["detail"]["error"] == ("ant is not signed in or its login expired (run: ant auth login): "
                                        "HTTP 401: authentication_error: x-api-key header is required")
    model = ask(ca.db, "ca", "question", runtime=ca(**ANT_UNKNOWN_MODEL))
    assert (model["status"], model["certainty"]) == ("failed_not_started", "NOT_STARTED")
    assert model["detail"]["error"] == "ca: HTTP 404: not_found_error: model: claude-nope-0"
    assert evidence_kinds(ca.db).count("prespawn_failure") == 2 and "started" not in evidence_kinds(ca.db)


@pytest.mark.parametrize("code", [1, 0])  # the exit code of this case was not recorded: the rule does not use it
def test_a_low_credit_balance_is_a_provider_limit_without_a_reset_time(ca, code):
    result = ask(ca.db, "ca", "question", runtime=ca(**{**ANT_NO_CREDIT, "code": code}))
    assert (result["status"], result["task_outcome"], result["reason"]) == ("not_executed", "NOT_EXECUTED", "quota_exhausted")
    assert result.get("reset_time") is None and result["error"]["code"] == "quota_exhausted"
    assert result["message"] == ("ca: HTTP 400: invalid_request_error: Your credit balance is too low to access the Anthropic API. "
                                 "Please go to Plans & Billing to upgrade or purchase credits.")


@pytest.mark.parametrize("output", [
    SERVER_ERROR,
    ant_error(529, "Overloaded", "overloaded_error", "Overloaded"),
    ant_error(402, "Payment Required", "invalid_request_error", "nope"),  # a status that is not listed
    {**ANT_NOT_SIGNED_IN, "stdout": ""},  # a status without an error object
    {**ANT_NOT_SIGNED_IN, "stderr": ""},  # an error object without a status
    {"stdout": "", "stderr": "Not logged in. Run `ant auth login`.\n", "code": 1},  # no guessed wording: only measured outputs count
    {"stdout": "", "stderr": "panic: something else\n", "code": 1},
])
def test_every_other_failure_is_uncertain_and_never_retried(ca, output):
    result = ask(ca.db, "ca", "question", runtime=ca(**output))
    assert result["status"] == "uncertain" and result["certainty"] in ("STARTED", "MAY_HAVE_STARTED")
    assert len(launches(ca.log)) == 1


def test_a_timeout_is_uncertain(ca):
    result = ask(ca.db, "ca", "question", runtime=ca(sleep=30, timeout_s=1.5))
    assert result["status"] == "uncertain" and "runtime_timeout" in evidence_kinds(ca.db)


def test_ant_missing_is_a_prespawn_failure(ca, tmp_path, monkeypatch):
    monkeypatch.setattr(api.shutil, "which", lambda name: None)
    missing = ask(ca.db, "ca", "question", runtime=CaRuntimeTarget(tmp_path, profile="ca.standard"))
    assert (missing["status"], missing["certainty"]) == ("failed_not_started", "NOT_STARTED")
    assert missing["detail"]["error"] == "ant executable not found"
    unstartable = ask(ca.db, "ca", "question",
                      runtime=CaRuntimeTarget(tmp_path, profile="ca.standard", command=[str(tmp_path / "no-such-ant")]))
    assert unstartable["status"] == "failed_not_started" and unstartable["detail"]["error"].startswith("cannot spawn ant")
    assert evidence_kinds(ca.db).count("prespawn_failure") == 2


def test_a_secret_in_an_error_is_redacted(ca, monkeypatch):
    monkeypatch.setenv("PEERHUB_TEST_TOKEN", "hunter2-hunter2-value")
    output = ant_error(401, "Unauthorized", "authentication_error", "bad key sk-ant-abcdefgh12345678 and hunter2-hunter2-value")
    result = ask(ca.db, "ca", "the question itself", runtime=ca(**output))
    shown = json.dumps(result)
    assert "[REDACTED]" in result["detail"]["error"]
    assert "sk-ant-abcdefgh12345678" not in shown and "hunter2-hunter2-value" not in shown
    assert "sk-ant-abcdefgh12345678".encode() not in ca.db.read_bytes()


# ---- one ask per stream (design 4) --------------------------------------------------------------------------------------
@pytest.mark.parametrize("first,status", [
    ({}, "delivered"),
    (ANT_NOT_SIGNED_IN, "failed_not_started"),
    (SERVER_ERROR, "uncertain"),
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


def test_the_second_ask_check_runs_again_under_the_claim(ca, monkeypatch):
    """The check before the claim saw an empty stream; another ask stored its prompt before this one got the claim."""
    from peerhub.extensions.bridge_claims import ClaimStore

    real_acquire = ClaimStore.acquire
    raced: list[str] = []

    def acquire(self, peer_id, stream_id, owner_id, lease_sec):
        if not raced:  # the other ask runs to its end between the first check and this claim
            raced.append("running")
            raced.append(ask(ca.db, "ca", "first", stream_id="s", request_id="one", runtime=ca())["status"])
        return real_acquire(self, peer_id, stream_id, owner_id, lease_sec)

    monkeypatch.setattr(ClaimStore, "acquire", acquire)
    with pytest.raises(RequestRejected, match="ca is stateless; use a new stream"):
        ask(ca.db, "ca", "second", stream_id="s", request_id="two", runtime=ca())
    assert raced == ["running", "delivered"] and len(launches(ca.log)) == 1
    with sqlite3.connect(ca.db) as conn:
        assert [json.loads(row[0]) for row in conn.execute("SELECT body_json FROM records WHERE kind='prompt'")] == ["first"]


def test_without_a_stream_each_ask_gets_its_own_stream_named_after_the_request(ca):
    first = ask(ca.db, "ca", "one", request_id="r1", runtime=ca())
    second = ask(ca.db, "ca", "two", request_id="r2", runtime=ca())
    assert (first["status"], first["stream_id"]) == ("delivered", "peer:ca:r1")
    assert (second["status"], second["stream_id"]) == ("delivered", "peer:ca:r2")
    again = ask(ca.db, "ca", "one", request_id="r1", runtime=ca())  # the same request finds its stream and replays
    assert (again["status"], again["stream_id"]) == ("recovered_terminal", "peer:ca:r1") and len(launches(ca.log)) == 2
    unnamed = ask(ca.db, "ca", "three", runtime=ca())
    assert unnamed["status"] == "delivered" and unnamed["stream_id"] == f"peer:ca:{unnamed['request_id']}"
    assert ask(ca.db, "ca", "four", stream_id="mine", runtime=ca())["stream_id"] == "mine"


def test_the_caller_may_repeat_a_request_that_was_proven_not_started(ca):
    """PeerHub never retries; the same request-id after a not-started answer (for example after `ant auth login`) runs once more."""
    failed = ask(ca.db, "ca", "question", stream_id="s", request_id="one", runtime=ca(**ANT_NOT_SIGNED_IN))
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
    captured = capsys.readouterr()
    rejected = json.loads(captured.out)
    assert rejected["status"] == "error" and rejected["error"]["code"] == "request_rejected"
    assert rejected["guidance"]["code"] == "request_rejected"  # its own entry, not ask_unexpected_failure
    assert captured.err.startswith("[request_rejected] ") and "peerhub explain request_rejected" in captured.err
    assert main([*base, "--stream", "s1"]) == 1
    captured = capsys.readouterr()
    truncated = json.loads(captured.out)
    assert truncated["status"] == "truncated" and truncated["error"]["code"] == "truncated"
    assert truncated["guidance"]["code"] == "truncated"
    assert captured.err.startswith("[truncated] ") and "details: peerhub explain truncated" in captured.err
    assert main([*base, "--stream", "s1", "--request-id", "other"]) == 2
    assert json.loads(capsys.readouterr().out)["error"]["message"].endswith("ca is stateless; use a new stream")


def test_cli_prints_the_stream_it_chose_before_the_session_line(tmp_path, monkeypatch, capsys):
    from peerhub.cli.app import main

    monkeypatch.setitem(api.API_TARGETS, "ca", lambda workspace, **kw: CaRuntimeTarget(
        workspace, command=[sys.executable, FAKE_ANT], **kw))
    base = ["--db", str(tmp_path / "core.db"), "ask", "ca", "question", "-w", str(tmp_path), "--profile", "ca.standard"]
    assert main([*base, "--request-id", "r9"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == "OK" and lines[1] == "stream: peer:ca:r9" and lines[2].startswith("session: sess_")
    assert main([*base, "--stream", "named"]) == 0  # a stream the caller named is not repeated
    assert not any(line.startswith("stream:") for line in capsys.readouterr().out.splitlines())
