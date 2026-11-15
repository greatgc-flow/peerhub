"""Peer `oa` (#378): one HTTPS call to the OpenAI Responses API. A loopback HTTP server stands in; no provider is called.

The URL is injected here only: production code has no override."""
import json
import socket
import sqlite3
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from peerhub.extensions.adapters import api
from peerhub.extensions.adapters.api import OaRuntimeTarget, RequestRejected
from peerhub.extensions.adapters.profiles import resolve_profile
from peerhub.extensions.ask import ask
from tests.communication.harness.api_peers import evidence_kinds

KEY = "sk-test-0123456789abcdefKEY"


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("PEERHUB_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("OPENAI_API_KEY", KEY)


class Fake:
    """One loopback server: `reply` is (status, body bytes, extra headers) or a callable(handler) for special cases."""

    def __init__(self) -> None:
        self.requests: list[dict] = []
        self.reply: object = (200, json.dumps(completed()).encode(), {})
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802 - http.server API
                body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                fake.requests.append({"path": self.path, "headers": {k.lower(): v for k, v in self.headers.items()}, "body": body})
                if callable(fake.reply):
                    fake.reply(self)
                    return
                status, payload, headers = fake.reply  # type: ignore[misc]
                self.send_response(status)
                for name, value in {"Content-Type": "application/json", "Content-Length": str(len(payload)), **headers}.items():
                    self.send_header(name, value)
                self.end_headers()
                self.wfile.write(payload)

            do_GET = do_POST  # noqa: N815 - a followed redirect would arrive as GET

            def log_message(self, *args: object) -> None:
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/v1/responses"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


def completed(**over) -> dict:
    doc = {"id": "resp_1", "object": "response", "status": "completed", "error": None, "incomplete_details": None,
           "output": [{"type": "reasoning", "summary": []},
                      {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "O"},
                                                                           {"type": "output_text", "text": "K"}]}],
           "usage": {"input_tokens": 90, "output_tokens": 12, "total_tokens": 102}}
    doc.update(over)
    return doc


def error_body(code: str | None, message: str = "refused") -> bytes:
    return json.dumps({"error": {"message": message, "type": "invalid_request_error", "code": code}}).encode()


@pytest.fixture
def oa(tmp_path):
    fake = Fake()

    def make(**kw):
        kw.setdefault("profile", "oa.standard")
        kw.setdefault("timeout_s", 20)
        return OaRuntimeTarget(tmp_path, url=fake.url, **kw)

    make.fake, make.db = fake, tmp_path / "core.db"
    yield make
    fake.close()


def store_bytes(db) -> bytes:
    return b"".join(path.read_bytes() for path in db.parent.iterdir() if path.is_file())


# ---- request shape and the key ------------------------------------------------------------------------------------------
@pytest.mark.parametrize("prompt", ["Is this text correct?", "@secret.txt\nrepeat it", "naïve café ✓ 日本語"])
def test_one_post_with_the_body_fields_the_content_type_and_the_key_in_the_header_only(oa, prompt):
    result = ask(oa.db, "oa", prompt, runtime=oa())
    assert (result["status"], result["certainty"], result["response"]) == ("delivered", "TERMINAL", "OK")
    (request,) = oa.fake.requests
    assert request["path"] == "/v1/responses" and request["headers"]["content-type"] == "application/json"
    assert request["headers"]["authorization"] == f"Bearer {KEY}"
    body = json.loads(request["body"].decode("utf-8"))  # UTF-8 JSON bytes
    assert body == {"model": resolve_profile("oa", "oa.standard", oa.db.parent)[0], "input": prompt,
                    "max_output_tokens": 16_000, "store": False}  # the prompt as one string: no header line is added
    assert KEY not in request["body"].decode("utf-8") and KEY not in json.dumps(result)
    assert KEY.encode() not in store_bytes(oa.db)


def test_usage_goes_into_the_telemetry_fields_with_provider_oa(oa):
    result = ask(oa.db, "oa", "question", runtime=oa())
    assert result["usage"] == {"input_tokens": 90, "output_tokens": 12}
    assert (result["usage_scope"], result["usage_attribution"]) == ("delivery", "delivery")
    with sqlite3.connect(oa.db) as conn:
        detail = json.loads(conn.execute("SELECT detail FROM bridge_evidence WHERE kind='session_telemetry'").fetchone()[0])
    assert detail["provider_kind"] == "oa" and detail["adapter_version"] == "1"
    assert detail["usage"]["source"] == "oa.response.usage" and detail["usage"]["coverage"]["state"] == "MEASURED"
    assert detail["usage"]["reported"] == {"input_tokens": 90, "output_tokens": 12, "total_tokens": 102}
    assert detail["usage"]["normalized"] == {"provider_input_tokens": 90, "output_tokens": 12, "provider_total_tokens": 102}


def test_no_key_is_a_prespawn_failure_and_sends_nothing(oa, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY")
    result = ask(oa.db, "oa", "question", runtime=oa())
    assert (result["status"], result["certainty"]) == ("failed_not_started", "NOT_STARTED")
    assert result["detail"]["error"] == "set OPENAI_API_KEY" and "prespawn_failure" in evidence_kinds(oa.db)
    assert oa.fake.requests == []


@pytest.mark.parametrize("bad", ["two words-0123456789", "line\nbreak-0123456789", "naïve-0123456789"])
def test_a_key_that_cannot_be_a_header_is_a_prespawn_failure_and_is_never_shown(oa, monkeypatch, bad):
    monkeypatch.setenv("OPENAI_API_KEY", bad)
    result = ask(oa.db, "oa", "question", runtime=oa())
    assert (result["status"], result["certainty"]) == ("failed_not_started", "NOT_STARTED")
    assert result["detail"]["error"] == "OPENAI_API_KEY is not usable in a header (printable ASCII without spaces)"
    assert oa.fake.requests == [] and bad not in json.dumps(result, ensure_ascii=False)


def test_the_url_is_fixed_in_production_and_the_base_url_variable_is_ignored(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_BASE_URL", "http://127.0.0.1:9/v1")
    assert api.OA_URL == "https://api.openai.com/v1/responses"
    assert OaRuntimeTarget(tmp_path, profile="oa.standard").url == api.OA_URL
    built = api.API_TARGETS["oa"](tmp_path, profile="oa.standard")  # what ask builds: no URL argument exists there
    assert isinstance(built, OaRuntimeTarget) and built.url == api.OA_URL


def test_proxy_variables_in_the_environment_are_ignored(oa, monkeypatch):
    for name in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy", "ALL_PROXY"):
        monkeypatch.setenv(name, "http://127.0.0.1:9")  # nothing listens there: a proxied request would fail
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.delenv("no_proxy", raising=False)
    assert ask(oa.db, "oa", "question", runtime=oa())["status"] == "delivered"
    assert len(oa.fake.requests) == 1


# ---- model, profile, request refusal ------------------------------------------------------------------------------------
def test_profiles_and_a_valid_model_override(oa, tmp_path):
    for profile in ("oa.standard", "oa.effort", "oa.deepthink"):
        model = resolve_profile("oa", profile, tmp_path)[0]
        assert model and api.OA_MODEL.fullmatch(model)
        assert oa(profile=profile).binding() == f"{profile}/{model}/default"
    assert ask(oa.db, "oa", "question", runtime=oa(model="gpt-6-astra"))["status"] == "delivered"
    assert json.loads(oa.fake.requests[0]["body"])["model"] == "gpt-6-astra"


@pytest.mark.parametrize("model", ["@secret.txt", "claude-opus-5-5", "GPT-6", "gpt-", "gpt-" + "a" * 65, "gpt-a b", "gpt-a\n"])
def test_an_invalid_model_writable_and_effort_are_refused_before_anything_is_started(tmp_path, model):
    db = tmp_path / "new" / "core.db"
    with pytest.raises(RequestRejected) as caught:
        ask(db, "oa", "question", profile="oa.standard", model=model)
    assert caught.value.code == "request_rejected"
    with pytest.raises(RequestRejected, match="--writable"):
        ask(db, "oa", "question", profile="oa.standard", writable=True)
    with pytest.raises(RequestRejected, match="effort"):
        ask(db, "oa", "question", profile="oa.standard", effort="high")
    with pytest.raises(RequestRejected, match="--profile"):
        ask(db, "oa", "question")
    assert not db.parent.exists()


def test_a_second_ask_on_a_stream_is_refused_before_anything_is_sent(oa):
    assert ask(oa.db, "oa", "first", stream_id="s", request_id="one", runtime=oa())["status"] == "delivered"
    with pytest.raises(RequestRejected, match="oa is stateless; use a new stream"):
        ask(oa.db, "oa", "second", stream_id="s", request_id="two", runtime=oa())
    assert len(oa.fake.requests) == 1
    assert ask(oa.db, "oa", "first", stream_id="s", request_id="one", runtime=oa())["status"] == "recovered_terminal"
    assert len(oa.fake.requests) == 1


# ---- completion rule (design 4) -----------------------------------------------------------------------------------------
def test_empty_text_is_empty_as_for_other_peers(oa):
    oa.fake.reply = (200, json.dumps(completed(output=[{"type": "message", "content": [{"type": "output_text", "text": " "}]}])).encode(), {})
    result = ask(oa.db, "oa", "question", runtime=oa())
    assert result["status"] == "delivered" and result["empty_response"] is True


def test_max_output_tokens_is_truncated_and_keeps_text_and_reason(oa):
    oa.fake.reply = (200, json.dumps(completed(status="incomplete", incomplete_details={"reason": "max_output_tokens"})).encode(), {})
    result = ask(oa.db, "oa", "question", runtime=oa())
    assert (result["status"], result["certainty"], result["response"]) == ("truncated", "STARTED", None)
    assert result["error"]["code"] == "truncated" and "max_output_tokens" in result["error"]["message"]
    assert result["partial_response"] == "OK" and result["usage"] == {"input_tokens": 90, "output_tokens": 12}


@pytest.mark.parametrize("over,needle", [
    ({"status": "incomplete", "incomplete_details": {"reason": "content_filter"}}, "content_filter"),
    ({"status": "incomplete", "incomplete_details": None}, "incomplete"),
    ({"status": "failed"}, "failed"),
    ({"status": "in_progress"}, "in_progress"),
    ({"status": "cancelled"}, "cancelled"),
])
def test_another_incomplete_reason_or_status_is_incomplete(oa, over, needle):
    oa.fake.reply = (200, json.dumps(completed(**over)).encode(), {})
    result = ask(oa.db, "oa", "question", runtime=oa())
    assert (result["status"], result["certainty"], result["response"]) == ("incomplete", "STARTED", None)
    assert result["error"]["code"] == "incomplete" and needle in result["error"]["message"]
    assert result["partial_response"] == "OK"
    with sqlite3.connect(oa.db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM records WHERE kind='response'").fetchone()[0] == 0


@pytest.mark.parametrize("payload", [
    b"this is not JSON",
    b"[1, 2]",
    json.dumps({"object": "response"}).encode(),  # no status
    json.dumps(completed(status=7)).encode(),
    json.dumps(completed())[:-4].encode(),
    b"",
])
def test_invalid_json_or_a_wrong_shape_is_uncertain(oa, payload):
    oa.fake.reply = (200, payload, {})
    result = ask(oa.db, "oa", "question", runtime=oa())
    assert (result["status"], result["response"]) == ("uncertain", None)
    assert len(oa.fake.requests) == 1  # never retried


# ---- failure table (design 5) -------------------------------------------------------------------------------------------
@pytest.mark.parametrize("status", [400, 401, 403, 404, 413, 429])
def test_an_error_object_with_a_listed_status_is_not_started(oa, status):
    oa.fake.reply = (status, error_body("some_code", "the request was refused"), {})
    result = ask(oa.db, "oa", "question", runtime=oa())
    assert (result["status"], result["certainty"]) == ("failed_not_started", "NOT_STARTED")
    assert f"HTTP {status}" in result["detail"]["error"] and "the request was refused" in result["detail"]["error"]
    assert "prespawn_failure" in evidence_kinds(oa.db) and "started" not in evidence_kinds(oa.db)


@pytest.mark.parametrize("status,code", [(429, "insufficient_quota"), (400, "credit_balance_exhausted"), (402, "insufficient_quota"),
                                         (500, "credit_balance_exhausted")])
def test_the_error_code_is_checked_before_the_status(oa, status, code):
    oa.fake.reply = (status, error_body(code, "You exceeded your current quota"), {})
    result = ask(oa.db, "oa", "question", runtime=oa())
    assert (result["status"], result["task_outcome"], result["reason"]) == ("not_executed", "NOT_EXECUTED", "quota_exhausted")
    assert result.get("reset_time") is None and code in result["message"]


@pytest.mark.parametrize("status", [402, 409, 422, 500, 502, 503])
def test_an_unlisted_status_or_5xx_is_uncertain(oa, status):
    oa.fake.reply = (status, error_body("some_code"), {})
    result = ask(oa.db, "oa", "question", runtime=oa())
    assert result["status"] == "uncertain" and len(oa.fake.requests) == 1


def test_a_listed_status_without_an_error_object_is_uncertain(oa):
    oa.fake.reply = (400, b"<html>bad gateway</html>", {})
    assert ask(oa.db, "oa", "question", runtime=oa())["status"] == "uncertain"


@pytest.mark.parametrize("status", [301, 302, 307, 308])
def test_a_redirect_is_never_followed_and_is_uncertain(oa, status):
    target = Fake()
    try:
        oa.fake.reply = (status, b"", {"Location": target.url})
        result = ask(oa.db, "oa", "question", runtime=oa())
    finally:
        target.close()
    assert result["status"] == "uncertain" and len(oa.fake.requests) == 1
    assert target.requests == []  # the key never reached another URL


def test_a_timeout_is_uncertain(oa):
    oa.fake.reply = lambda handler: time.sleep(5)
    result = ask(oa.db, "oa", "question", runtime=oa(timeout_s=1))
    assert result["status"] == "uncertain" and "runtime_timeout" in evidence_kinds(oa.db)


@pytest.mark.parametrize("part", ["headers", "body"])
def test_the_timeout_is_a_total_and_a_slow_drip_does_not_extend_it(oa, part):
    """Every socket operation gets bytes within 0.2 s, so only a deadline over the whole call ends this ask."""
    payload = json.dumps(completed()).encode()

    def drip(handler: BaseHTTPRequestHandler) -> None:
        head = f"HTTP/1.0 200 OK\r\nContent-Type: application/json\r\nContent-Length: {len(payload)}\r\n\r\n".encode()
        slow, fast = (head, payload) if part == "headers" else (payload, head)
        try:
            if part == "body":
                handler.wfile.write(fast)
            for index in range(0, 40):  # 8 seconds in all
                handler.wfile.write(slow[index:index + 1] if index < 39 else slow[39:])
                handler.wfile.flush()
                time.sleep(0.2)
            if part == "headers":
                handler.wfile.write(fast)
        except OSError:
            pass  # the client went away at its deadline

    oa.fake.reply = drip
    began = time.monotonic()
    result = ask(oa.db, "oa", "question", runtime=oa(timeout_s=1.5))
    assert result["status"] == "uncertain" and "runtime_timeout" in evidence_kinds(oa.db)
    assert time.monotonic() - began < 6 and len(oa.fake.requests) == 1


def test_the_opener_verifies_tls_follows_no_redirect_and_uses_no_proxy(monkeypatch):
    import ssl
    import urllib.request

    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9")
    opener = api.oa_opener()
    (https,) = [h for h in opener.handlers if isinstance(h, urllib.request.HTTPSHandler)]
    context = https._context  # the context this handler gives every HTTPS connection
    assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname is True  # a certificate, for this host name
    (redirect,) = [h for h in opener.handlers if isinstance(h, urllib.request.HTTPRedirectHandler)]
    assert redirect.redirect_request(None, None, 302, "Found", {}, "https://elsewhere.invalid/") is None
    # The empty ProxyHandler replaces the default one and registers nothing: no handler reads the proxy variables.
    assert not [h for h in opener.handlers if isinstance(h, urllib.request.ProxyHandler)]
    assert [h for h in urllib.request.build_opener().handlers if isinstance(h, urllib.request.ProxyHandler)]  # control


def test_a_dropped_connection_is_uncertain(oa):
    def drop(handler: BaseHTTPRequestHandler) -> None:
        handler.connection.shutdown(socket.SHUT_RDWR)

    oa.fake.reply = drop
    assert ask(oa.db, "oa", "question", runtime=oa())["status"] == "uncertain"


def test_an_error_body_that_echoes_the_key_is_redacted(oa):
    oa.fake.reply = (401, error_body("invalid_api_key", f"Incorrect API key provided: {KEY}."), {})
    result = ask(oa.db, "oa", "question", runtime=oa())
    assert result["status"] == "failed_not_started" and "[REDACTED]" in result["detail"]["error"]
    assert KEY not in json.dumps(result) and KEY.encode() not in store_bytes(oa.db)
    oa.fake.reply = (500, f"upstream said {KEY}".encode(), {})
    uncertain = ask(oa.db, "oa", "question", stream_id="s2", runtime=oa())
    assert uncertain["status"] == "uncertain" and KEY not in json.dumps(uncertain)
    assert KEY.encode() not in store_bytes(oa.db)
