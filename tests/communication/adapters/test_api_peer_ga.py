"""Peer `ga` (#381): one HTTPS call to the Gemini API (generateContent). The loopback server of the `oa` tests stands in;
no provider is called.

The base URL is injected here only: production code has no override."""
import json
import socket
import sqlite3
from http.server import BaseHTTPRequestHandler

import pytest

from peerhub.extensions.adapters import api
from peerhub.extensions.adapters.api import GaRuntimeTarget, RequestRejected
from peerhub.extensions.adapters.profiles import resolve_profile
from peerhub.extensions.ask import ask
from tests.communication.adapters.test_api_peer_oa import Fake, Unending, store_bytes
from tests.communication.harness.api_peers import evidence_kinds

KEY = "gk-test-0123456789abcdefKEY"  # no real key has this form
QUOTA_MESSAGE = "You exceeded your current quota, please check your plan and billing details."  # measured 2026-11-16
# The answer of the Gemini API to every model on 2026-11-16 04:24Z (0.3-0.4 s), byte for byte (#384): HTTP 402.
PREPAY_BODY = (b'{"error": {"code": 402, "message": "Your prepayment credits are depleted. Please go to AI Studio at '
               b'https://ai.studio/projects to manage your project and billing. Learn more at '
               b'https://ai.google.dev/gemini-api/docs/billing#prepay. ", "status": "RESOURCE_EXHAUSTED"}}')
PREPAY_MESSAGE = ("Your prepayment credits are depleted. Please go to AI Studio at https://ai.studio/projects to manage your "
                  "project and billing. Learn more at https://ai.google.dev/gemini-api/docs/billing#prepay. ")


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("PEERHUB_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("GEMINI_API_KEY", KEY)


def candidate(**over) -> dict:
    """The first candidate of the measured reply shape: a thought part, then the text in two parts."""
    doc = {"content": {"role": "model", "parts": [{"text": "weighing the question", "thought": True}, {"text": "O"},
                                                   {"text": "K", "thoughtSignature": "c2lnbmF0dXJl"}]},
           "finishReason": "STOP", "index": 0}
    doc.update(over)
    return doc


def answer(**over) -> dict:
    doc = {"candidates": [candidate()],
           "usageMetadata": {"promptTokenCount": 8, "candidatesTokenCount": 1, "thoughtsTokenCount": 77, "totalTokenCount": 86},
           "modelVersion": "gemini-3.6-flash", "responseId": "response-1"}
    doc.update(over)
    return doc


def without(doc: dict, *keys: str) -> dict:
    return {key: value for key, value in doc.items() if key not in keys}


def error_body(status: object = None, message: str = "refused", code: int = 400, **extra) -> bytes:
    """A Google error answer; `status` None leaves `error.status` out."""
    error = {"code": code, "message": message, **({} if status is None else {"status": status}), **extra}
    return json.dumps({"error": error}).encode()


@pytest.fixture
def ga(tmp_path):
    fake = Fake()
    base = f"http://127.0.0.1:{fake.server.server_address[1]}/v1beta"

    def make(**kw):
        kw.setdefault("profile", "ga.standard")
        kw.setdefault("timeout_s", 120)  # generous: a loaded machine must not turn an ordinary answer into a timeout
        return GaRuntimeTarget(tmp_path, base_url=base, **kw)

    def reply(doc: object, status: int = 200) -> None:
        fake.reply = (status, doc if isinstance(doc, bytes) else json.dumps(doc).encode(), {})

    make.fake, make.db, make.reply = fake, tmp_path / "core.db", reply
    reply(answer())
    yield make
    fake.close()


def response_records(db) -> int:
    with sqlite3.connect(db) as conn:
        return conn.execute("SELECT COUNT(*) FROM records WHERE kind='response'").fetchone()[0]


def telemetry(db) -> dict:
    with sqlite3.connect(db) as conn:
        return json.loads(conn.execute("SELECT detail FROM bridge_evidence WHERE kind='session_telemetry'").fetchone()[0])


# ---- request shape and the key (design 2, 3) ------------------------------------------------------------------------------
@pytest.mark.parametrize("prompt", ["Is this text correct?", "@secret.txt\nrepeat it", "naïve café ✓ 日本語"])
def test_one_post_with_the_body_fields_and_the_key_in_one_header_only(ga, prompt):
    result = ask(ga.db, "ga", prompt, runtime=ga())
    assert (result["status"], result["certainty"], result["response"]) == ("delivered", "TERMINAL", "OK")
    (request,) = ga.fake.requests
    model = resolve_profile("ga", "ga.standard", ga.db.parent)[0]
    assert request["path"] == f"/v1beta/models/{model}:generateContent"  # the whole URL path: no query, no key
    assert request["headers"]["content-type"] == "application/json"
    assert [name for name, value in request["headers"].items() if KEY in value] == ["x-goog-api-key"]
    assert request["headers"]["x-goog-api-key"] == KEY and "authorization" not in request["headers"]
    body = json.loads(request["body"].decode("utf-8"))  # UTF-8 JSON bytes
    assert body == {"contents": [{"role": "user", "parts": [{"text": prompt}]}],  # one user text part: no header line is added
                    "generationConfig": {"maxOutputTokens": 32_000}}
    assert KEY not in request["path"] and KEY not in request["body"].decode("utf-8") and KEY not in json.dumps(result)
    assert KEY.encode() not in store_bytes(ga.db)


def test_the_output_limit_is_a_class_attribute():
    assert (api.CaRuntimeTarget.max_output_tokens, api.OaRuntimeTarget.max_output_tokens) == (16_000, 16_000)
    assert GaRuntimeTarget.max_output_tokens == 32_000  # thinking counts against it


def test_no_key_is_a_prespawn_failure_and_sends_nothing(ga, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY")
    monkeypatch.setenv("GOOGLE_API_KEY", "another-variable-0123456789")  # only GEMINI_API_KEY is read
    result = ask(ga.db, "ga", "question", runtime=ga())
    assert (result["status"], result["certainty"]) == ("failed_not_started", "NOT_STARTED")
    assert result["detail"]["error"] == "set GEMINI_API_KEY (peerhub explain setup)"  # #385: the message names the next step
    assert "prespawn_failure" in evidence_kinds(ga.db)
    assert ga.fake.requests == []


@pytest.mark.parametrize("bad", ["two words-0123456789", "line\nbreak-0123456789", "naïve-0123456789"])
def test_a_key_that_cannot_be_a_header_is_a_prespawn_failure_and_is_never_shown(ga, monkeypatch, bad):
    monkeypatch.setenv("GEMINI_API_KEY", bad)
    result = ask(ga.db, "ga", "question", runtime=ga())
    assert (result["status"], result["certainty"]) == ("failed_not_started", "NOT_STARTED")
    assert result["detail"]["error"] == "GEMINI_API_KEY is not usable in a header (printable ASCII without spaces)"
    assert ga.fake.requests == [] and bad not in json.dumps(result, ensure_ascii=False)


def test_the_host_is_fixed_in_production(tmp_path):
    assert api.GA_BASE_URL == "https://generativelanguage.googleapis.com/v1beta"
    model = resolve_profile("ga", "ga.standard", tmp_path)[0]
    expected = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    assert GaRuntimeTarget(tmp_path, profile="ga.standard").url == expected
    built = api.API_TARGETS["ga"](tmp_path, profile="ga.standard")  # what ask builds: no URL argument exists there
    assert isinstance(built, GaRuntimeTarget) and built.url == expected


def test_proxy_variables_in_the_environment_are_ignored(ga, monkeypatch):
    for name in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy", "ALL_PROXY"):
        monkeypatch.setenv(name, "http://127.0.0.1:9")  # nothing listens there: a proxied request would fail
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.delenv("no_proxy", raising=False)
    assert ask(ga.db, "ga", "question", runtime=ga())["status"] == "delivered"
    assert len(ga.fake.requests) == 1


def test_oa_and_ga_share_the_exchange_and_keep_only_their_hooks():
    """Design 7: the key variable, the request builder and `_classify` are the hooks; `_call` is defined once."""
    shared = api.HttpRuntimeTarget
    for cls, variable in ((api.OaRuntimeTarget, "OPENAI_API_KEY"), (GaRuntimeTarget, "GEMINI_API_KEY")):
        assert issubclass(cls, shared) and cls.key_env == variable
        assert "_call" not in vars(cls) and "_call" in vars(shared)
        assert {"_request", "_classify"} <= set(vars(cls))
    assert not issubclass(api.CaRuntimeTarget, shared)  # ca runs `ant`


# ---- model, profile, request refusal (design 1) ---------------------------------------------------------------------------
def test_profiles_and_a_valid_model_override(ga, tmp_path):
    for profile in ("ga.standard", "ga.effort"):
        model = resolve_profile("ga", profile, tmp_path)[0]
        assert model and api.GA_MODEL.fullmatch(model)
        assert ga(profile=profile).binding() == f"{profile}/{model}/default"
    assert GaRuntimeTarget.profile_tiers == ("standard", "effort")
    with pytest.raises(ValueError, match="unknown model profile"):  # no deep profile is packaged: a stronger model goes by --model
        ga(profile="ga.deepthink")
    assert ga.fake.requests == []
    assert ask(ga.db, "ga", "question", runtime=ga(model="gemini-4-carbon"))["status"] == "delivered"
    assert ga.fake.requests[0]["path"] == "/v1beta/models/gemini-4-carbon:generateContent"
    assert "model" not in json.loads(ga.fake.requests[0]["body"])  # the model is in the URL path only
    # Dots inside the one path segment are harmless: the segment is sent as it is.
    assert ask(ga.db, "ga", "question", stream_id="dots", runtime=ga(model="gemini-a..b"))["status"] == "delivered"
    assert ga.fake.requests[1]["path"] == "/v1beta/models/gemini-a..b:generateContent"


@pytest.mark.parametrize("model", [
    "@secret.txt", "gpt-6-luna", "Gemini-4-carbon", "gemini-4-Carbon", "gemini-", "gemini-" + "a" * 65, "gemini-a_b",
    "gemini-a b", "gemini-a\n", "gemini-a\tb", "gemini-a/b", "gemini-a:generateContent", "gemini-a?alt=sse", "gemini-a#b",
    "gemini-a%2fb", "../x", "gemini-a/../x", "models/gemini-a"])
def test_an_invalid_model_writable_and_effort_are_refused_before_anything_is_started(tmp_path, model):
    db = tmp_path / "new" / "core.db"
    with pytest.raises(RequestRejected) as caught:
        ask(db, "ga", "question", profile="ga.standard", model=model)
    assert caught.value.code == "request_rejected"
    with pytest.raises(RequestRejected, match="--writable"):
        ask(db, "ga", "question", profile="ga.standard", writable=True)
    with pytest.raises(RequestRejected, match="effort"):
        ask(db, "ga", "question", profile="ga.standard", effort="high")
    with pytest.raises(RequestRejected) as missing:
        ask(db, "ga", "question")
    assert str(missing.value) == "ga needs --profile (ga.standard, ga.effort) or --model"  # only the packaged profiles are named
    assert not db.parent.exists()


def test_a_second_ask_on_a_stream_is_refused_before_anything_is_sent(ga):
    assert ask(ga.db, "ga", "first", stream_id="s", request_id="one", runtime=ga())["status"] == "delivered"
    with pytest.raises(RequestRejected, match="ga is stateless; use a new stream"):
        ask(ga.db, "ga", "second", stream_id="s", request_id="two", runtime=ga())
    assert len(ga.fake.requests) == 1
    assert ask(ga.db, "ga", "first", stream_id="s", request_id="one", runtime=ga())["status"] == "recovered_terminal"
    assert len(ga.fake.requests) == 1


def test_another_author_with_the_same_request_id_is_another_ask_and_is_refused(ga):
    assert ask(ga.db, "ga", "question", stream_id="s", request_id="one", runtime=ga())["status"] == "delivered"
    with pytest.raises(RequestRejected, match="ga is stateless; use a new stream"):
        ask(ga.db, "ga", "question", stream_id="s", request_id="one", author="someone-else", runtime=ga())
    assert ask(ga.db, "ga", "question", request_id="two", runtime=ga())["stream_id"] == "peer:ga:two"
    with pytest.raises(RequestRejected, match="ga is stateless; use a new stream"):
        ask(ga.db, "ga", "question", request_id="two", author="someone-else", runtime=ga())
    assert len(ga.fake.requests) == 2  # nothing was sent for the other author


# ---- completion rule (design 4), one row at a time ------------------------------------------------------------------------
def uncertain_without_a_reply(ga, payload: object) -> dict:
    ga.reply(payload)
    result = ask(ga.db, "ga", "question", runtime=ga())
    assert (result["status"], result["response"]) == ("uncertain", None) and "partial_response" not in result
    assert result.get("task_outcome") != "NOT_EXECUTED"
    assert response_records(ga.db) == 0 and len(ga.fake.requests) == 1  # never a reply, never retried
    return result


@pytest.mark.parametrize("payload", [b"this is not JSON", b"[1, 2]", b'"OK"', b"", json.dumps(answer())[:-4].encode()])
def test_4a_a_body_that_is_not_one_json_object_is_uncertain(ga, payload):
    uncertain_without_a_reply(ga, payload)


@pytest.mark.parametrize("error", [
    {"code": 500, "status": "INTERNAL", "message": "the model failed"},
    {"code": 429, "status": "RESOURCE_EXHAUSTED", "message": QUOTA_MESSAGE},  # a status name never decides at HTTP 200
    {}, "failed", 0, False, []])
def test_4a_any_error_that_is_not_null_is_uncertain_at_http_200(ga, error):
    uncertain_without_a_reply(ga, answer(error=error))  # with a complete reply beside it


def test_4a_an_error_that_is_null_is_no_error(ga):
    ga.reply(answer(error=None))
    assert ask(ga.db, "ga", "question", runtime=ga())["response"] == "OK"


@pytest.mark.parametrize("doc", [
    {"promptFeedback": {"blockReason": "SAFETY"}, "usageMetadata": {"promptTokenCount": 8, "totalTokenCount": 8}},
    {"candidates": [], "promptFeedback": {"blockReason": "SAFETY"}, "usageMetadata": {"promptTokenCount": 8}},
])
def test_4b_a_blocked_prompt_without_candidates_is_incomplete_with_the_reason(ga, doc):
    ga.reply(doc)
    result = ask(ga.db, "ga", "question", runtime=ga())
    assert (result["status"], result["certainty"], result["response"]) == ("incomplete", "STARTED", None)
    assert result["error"]["code"] == "incomplete" and "blockReason SAFETY" in result["error"]["message"]
    assert result["partial_response"] == "" and result["usage"] == {"input_tokens": 8, "output_tokens": 0}
    assert response_records(ga.db) == 0


def test_4b_a_block_reason_beside_a_candidate_does_not_decide(ga):
    ga.reply(answer(promptFeedback={"blockReason": "SAFETY"}))
    result = ask(ga.db, "ga", "question", runtime=ga())
    assert (result["status"], result["response"]) == ("delivered", "OK")  # the candidate is read by rows c to f


@pytest.mark.parametrize("doc", [
    without(answer(), "candidates"),  # no candidates and no prompt feedback
    answer(candidates=[]),
    {"promptFeedback": {}},
    {"promptFeedback": {"blockReason": 7}},  # a reason that is not a string
    {"promptFeedback": {"blockReason": None}},
    {"promptFeedback": "SAFETY"},
    {"candidates": [], "promptFeedback": ["SAFETY"]},
    answer(candidates=None),  # present and not a list
    answer(candidates="OK"),
    answer(candidates={"content": {"parts": [{"text": "OK"}]}, "finishReason": "STOP"}),  # an object, not a list
    {"candidates": None, "promptFeedback": {"blockReason": "SAFETY"}},  # not a list: the block reason is not read
    {"candidates": {}, "promptFeedback": {"blockReason": "SAFETY"}},
])
def test_4b_no_candidate_list_without_a_block_reason_is_uncertain(ga, doc):
    uncertain_without_a_reply(ga, doc)


@pytest.mark.parametrize("first", [
    "OK", None, 7, [candidate()],  # not an object
    without(candidate(), "finishReason"),
    candidate(finishReason=None), candidate(finishReason=7), candidate(finishReason=["STOP"]),
])
def test_4c_a_first_candidate_without_a_finish_reason_string_is_uncertain(ga, first):
    uncertain_without_a_reply(ga, answer(candidates=[first, candidate()]))


@pytest.mark.parametrize("content", [
    "OK", None, ["OK"], 7,  # present and not an object
    {"parts": "OK"}, {"parts": None}, {"parts": {"text": "OK"}},  # parts present and not a list
    {"parts": [{"text": "O"}, "K"]}, {"parts": [None]}, {"parts": [["OK"]]},  # a part that is not an object
    {"parts": [{"text": "O"}, {"text": 7}]}, {"parts": [{"text": None}]}, {"parts": [{"text": ["OK"]}]},  # a text that is not a string
    {"parts": [{"text": 7, "thought": True}, {"text": "OK"}]},  # also in a part that would not be joined
])
@pytest.mark.parametrize("finish", ["STOP", "MAX_TOKENS", "SAFETY"])
def test_4d_a_malformed_content_is_uncertain_and_never_a_complete_empty_or_partial_reply(ga, content, finish):
    uncertain_without_a_reply(ga, answer(candidates=[candidate(content=content, finishReason=finish)]))


def test_4e_thought_parts_and_parts_without_text_are_not_reply_text(ga):
    parts = [{"text": "plan: say OK", "thought": True}, {"text": "O"}, {"inlineData": {"mimeType": "image/png", "data": "AA=="}},
             {"thoughtSignature": "c2ln"}, {"text": "K", "thought": False}, {"text": "!", "thought": "true"}]
    ga.reply(answer(candidates=[candidate(content={"parts": parts})]))
    result = ask(ga.db, "ga", "question", runtime=ga())
    assert (result["status"], result["response"]) == ("delivered", "OK!")  # only `thought: true` (the JSON true) is left out
    assert "plan" not in json.dumps(result)


def test_4e_only_the_first_candidate_is_read(ga):
    ga.reply(answer(candidates=[candidate(), candidate(content={"parts": [{"text": " and more"}]})]))
    assert ask(ga.db, "ga", "question", runtime=ga())["response"] == "OK"


@pytest.mark.parametrize("first", [
    candidate(content={"parts": [{"text": "weighing the question", "thought": True}]}),  # a thought-only reply
    candidate(content={"parts": []}),
    candidate(content={"role": "model"}),  # no parts
    without(candidate(), "content"),  # no content
    candidate(content={"parts": [{"text": " \n"}]}),
])
def test_4e_4f_no_text_with_stop_is_an_empty_reply_as_for_other_peers(ga, first):
    ga.reply(answer(candidates=[first]))
    result = ask(ga.db, "ga", "question", runtime=ga())
    assert (result["status"], result["certainty"]) == ("delivered", "TERMINAL") and result["empty_response"] is True
    assert "weighing" not in json.dumps(result)


def test_4f_stop_is_complete(ga):
    result = ask(ga.db, "ga", "question", runtime=ga())
    assert (result["status"], result["certainty"], result["error"]) == ("delivered", "TERMINAL", None)
    assert result["response"] == "OK" and "empty_response" not in result and response_records(ga.db) == 1


def test_4f_max_tokens_is_truncated_and_names_the_limit_of_this_peer(ga):
    ga.reply(answer(candidates=[candidate(finishReason="MAX_TOKENS")]))
    result = ask(ga.db, "ga", "question", runtime=ga())
    assert (result["status"], result["certainty"], result["response"]) == ("truncated", "STARTED", None)
    assert result["error"]["code"] == "truncated"
    assert result["error"]["message"] == "ga reply cut at 32000 output tokens (finishReason MAX_TOKENS)"
    assert result["partial_response"] == "OK" and result["usage"] == {"input_tokens": 8, "output_tokens": 78}
    assert response_records(ga.db) == 0


def test_4f_max_tokens_without_text_is_truncated_too(ga):
    """Measured: with a small limit the thinking used every token, and the reply had no text."""
    ga.reply(answer(candidates=[{"content": {"role": "model"}, "finishReason": "MAX_TOKENS", "index": 0}],
                    usageMetadata={"promptTokenCount": 8, "thoughtsTokenCount": 17, "totalTokenCount": 25}))
    result = ask(ga.db, "ga", "question", runtime=ga())
    assert (result["status"], result["partial_response"]) == ("truncated", "")
    assert result["usage"] == {"input_tokens": 8, "output_tokens": 17}


@pytest.mark.parametrize("finish", ["SAFETY", "RECITATION", "PROHIBITED_CONTENT", "MALFORMED_FUNCTION_CALL", "OTHER",
                                    "FINISH_REASON_UNSPECIFIED", "stop", "Stop", "max_tokens", ""])
def test_4f_any_other_finish_reason_is_incomplete_and_keeps_the_text(ga, finish):
    ga.reply(answer(candidates=[candidate(finishReason=finish)]))
    result = ask(ga.db, "ga", "question", runtime=ga())
    assert (result["status"], result["certainty"], result["response"]) == ("incomplete", "STARTED", None)
    assert result["error"]["code"] == "incomplete" and f"finishReason {finish}" in result["error"]["message"]
    assert result["partial_response"] == "OK" and response_records(ga.db) == 0


# ---- usage (design 5) -------------------------------------------------------------------------------------------------------
def test_usage_goes_into_the_telemetry_fields_with_provider_ga(ga):
    result = ask(ga.db, "ga", "question", runtime=ga())
    assert result["usage"] == {"input_tokens": 8, "output_tokens": 78}  # 1 candidate token + 77 thinking tokens
    assert (result["usage_scope"], result["usage_attribution"]) == ("delivery", "delivery")
    detail = telemetry(ga.db)
    assert detail["provider_kind"] == "ga" and detail["adapter_version"] == "1"
    assert detail["usage"]["source"] == "ga.response.usage" and detail["usage"]["scope"] == "delivery"
    assert detail["usage"]["coverage"] == {"state": "MEASURED", "reason": None}
    assert detail["usage"]["reported"] == {"input_tokens": 8, "output_tokens": 78}
    assert detail["usage"]["normalized"] == {"provider_input_tokens": 8, "output_tokens": 78}


@pytest.mark.parametrize("meta,expected", [
    ({"promptTokenCount": 8, "candidatesTokenCount": 1}, (8, 1)),  # no thinking tokens (measured: gemini-3.5-flash-lite)
    ({"promptTokenCount": 8, "thoughtsTokenCount": 5}, (8, 5)),  # only thinking
    ({"promptTokenCount": 8}, (8, 0)),  # the two optional counters count as 0 when missing
    ({"promptTokenCount": 0, "candidatesTokenCount": 0, "thoughtsTokenCount": 0}, (0, 0)),  # a measured zero
    ({"promptTokenCount": 8, "candidatesTokenCount": 1, "totalTokenCount": "many", "cachedContentTokenCount": -1,
      "toolUsePromptTokenCount": True, "promptTokensDetails": [{"modality": "TEXT", "tokenCount": 8}]}, (8, 1)),  # other keys ignored
])
def test_measured_usage_with_and_without_thinking_tokens(ga, meta, expected):
    ga.reply(answer(usageMetadata=meta))
    result = ask(ga.db, "ga", "question", runtime=ga())
    assert result["status"] == "delivered"
    assert result["usage"] == {"input_tokens": expected[0], "output_tokens": expected[1]}
    assert telemetry(ga.db)["usage"]["coverage"]["state"] == "MEASURED"


@pytest.mark.parametrize("meta", [
    "missing", None, [], "8", 8, {},
    {"candidatesTokenCount": 1, "thoughtsTokenCount": 77},  # no prompt count
    {"promptTokenCount": None}, {"promptTokenCount": "8"}, {"promptTokenCount": 8.0}, {"promptTokenCount": -1},
    {"promptTokenCount": True},
    {"promptTokenCount": 8, "candidatesTokenCount": None}, {"promptTokenCount": 8, "candidatesTokenCount": "1"},
    {"promptTokenCount": 8, "candidatesTokenCount": -1}, {"promptTokenCount": 8, "candidatesTokenCount": True},
    {"promptTokenCount": 8, "thoughtsTokenCount": None}, {"promptTokenCount": 8, "thoughtsTokenCount": 1.5},
    {"promptTokenCount": 8, "thoughtsTokenCount": -1}, {"promptTokenCount": 8, "thoughtsTokenCount": False},
])
def test_usage_that_is_not_measured_is_absent_and_never_zero(ga, meta):
    ga.reply(without(answer(), "usageMetadata") if meta == "missing" else answer(usageMetadata=meta))
    result = ask(ga.db, "ga", "question", runtime=ga())
    assert (result["status"], result["response"]) == ("delivered", "OK")  # the reply does not depend on the usage
    assert result.get("usage") is None
    usage = telemetry(ga.db)["usage"]
    assert usage["coverage"] == {"state": "UNKNOWN", "reason": "usage_absent"} and usage["reported"] == {}


# ---- error answers (design 6): the pair (HTTP status, error.status) ---------------------------------------------------------
def test_429_resource_exhausted_is_a_provider_limit_without_a_reset_time(ga):
    """The measured body (gemini-4-argon, 2026-11-16). A rate limit and spent or missing quota look the same."""
    body = {"error": {"code": 429, "message": QUOTA_MESSAGE, "status": "RESOURCE_EXHAUSTED",
                      "details": [{"@type": "type.googleapis.com/google.rpc.Help", "links": [{"description": "Learn more"}]}]}}
    ga.reply(body, 429)
    result = ask(ga.db, "ga", "question", runtime=ga())
    assert (result["status"], result["task_outcome"], result["reason"]) == ("not_executed", "NOT_EXECUTED", "quota_exhausted")
    assert result.get("reset_time") is None
    assert result["message"] == f"ga: HTTP 429: RESOURCE_EXHAUSTED: {QUOTA_MESSAGE}"  # the message is kept
    assert len(ga.fake.requests) == 1


def test_402_resource_exhausted_is_a_provider_limit_without_a_reset_time(ga):
    """The measured body (every model, 2026-11-16 04:24Z; #384): a depleted prepay balance. 0.24.19 ended it uncertain."""
    assert json.loads(PREPAY_BODY)["error"] == {"code": 402, "message": PREPAY_MESSAGE, "status": "RESOURCE_EXHAUSTED"}
    ga.reply(PREPAY_BODY, 402)
    result = ask(ga.db, "ga", "question", runtime=ga())
    assert (result["status"], result["task_outcome"], result["reason"]) == ("not_executed", "NOT_EXECUTED", "quota_exhausted")
    assert result.get("reset_time") is None
    assert result["message"] == f"ga: HTTP 402: RESOURCE_EXHAUSTED: {PREPAY_MESSAGE}"  # the message is kept
    assert len(ga.fake.requests) == 1


def test_the_provider_limits_are_exactly_402_and_429_with_resource_exhausted():
    assert api.GA_LIMITS == ((402, "RESOURCE_EXHAUSTED"), (429, "RESOURCE_EXHAUSTED"))


@pytest.mark.parametrize("status,name", [
    (400, "INVALID_ARGUMENT"), (400, "FAILED_PRECONDITION"), (401, "UNAUTHENTICATED"), (403, "PERMISSION_DENIED"),
    (404, "NOT_FOUND"),
    (413, "INVALID_ARGUMENT"), (413, "RESOURCE_EXHAUSTED"), (413, "SOMETHING_NEW"), (413, 7), (413, None),  # 413: any status
    (400, None), (401, None), (403, None), (404, None), (429, None),  # error.status missing
])
def test_a_listed_pair_is_not_started(ga, status, name):
    ga.reply(error_body(name, "the request was refused", code=status), status)
    result = ask(ga.db, "ga", "question", runtime=ga())
    assert (result["status"], result["certainty"]) == ("failed_not_started", "NOT_STARTED")
    assert f"HTTP {status}" in result["detail"]["error"] and "the request was refused" in result["detail"]["error"]
    assert "prespawn_failure" in evidence_kinds(ga.db) and "started" not in evidence_kinds(ga.db)
    assert len(ga.fake.requests) == 1


@pytest.mark.parametrize("status,name,message", [  # measured 2026-11-16
    (404, "NOT_FOUND", "models/gemini-nope is not found for API version v1beta, or is not supported for generateContent."),
    (401, "UNAUTHENTICATED", "API key not valid. Please pass a valid API key."),
    (400, "INVALID_ARGUMENT", "API key not valid. Please pass a valid API key."),  # reported for other key forms
])
def test_the_measured_error_answers_are_not_started_and_keep_their_text(ga, status, name, message):
    ga.reply(error_body(name, message, code=status), status)
    result = ask(ga.db, "ga", "question", runtime=ga())
    assert result["status"] == "failed_not_started"
    assert result["detail"]["error"] == f"ga: HTTP {status}: {name}: {message}"


@pytest.mark.parametrize("status,name", [
    (400, "RESOURCE_EXHAUSTED"), (400, "NOT_FOUND"), (400, "UNAUTHENTICATED"), (401, "INVALID_ARGUMENT"),  # contradicting pairs
    (401, "PERMISSION_DENIED"), (403, "UNAUTHENTICATED"), (403, "RESOURCE_EXHAUSTED"), (404, "INVALID_ARGUMENT"),
    (429, "UNAVAILABLE"), (429, "INVALID_ARGUMENT"), (500, "RESOURCE_EXHAUSTED"), (503, "RESOURCE_EXHAUSTED"),
    (402, "INVALID_ARGUMENT"), (402, "FAILED_PRECONDITION"), (402, "resource_exhausted"), (402, 7),  # 402: one pair is listed
    (500, "INVALID_ARGUMENT"), (500, "NOT_FOUND"),
    (400, "SOMETHING_NEW"), (429, "SOMETHING_NEW"), (404, "not_found"), (429, "resource_exhausted"),  # unknown status strings
    (400, 7), (429, 7), (404, ["NOT_FOUND"]), (401, True),  # a status that is present and not a string
    (500, "INTERNAL"), (502, "UNAVAILABLE"), (503, "UNAVAILABLE"), (504, "DEADLINE_EXCEEDED"), (500, None), (503, None),  # any 5xx
    (402, None), (409, "ABORTED"), (409, None), (422, None), (499, "CANCELLED"),  # a status outside the table
])
def test_every_other_pair_is_uncertain(ga, status, name):
    ga.reply(error_body(name, QUOTA_MESSAGE, code=status), status)
    result = ask(ga.db, "ga", "question", runtime=ga())
    assert result["status"] == "uncertain" and result.get("task_outcome") != "NOT_EXECUTED"
    assert len(ga.fake.requests) == 1  # never retried


@pytest.mark.parametrize("status", [400, 402, 429])
def test_an_error_status_that_is_null_is_present_and_not_missing(ga, status):
    """`"status": null` is a status that is not a string, so it is no listed pair (only an absent key is missing)."""
    ga.reply(json.dumps({"error": {"code": status, "message": "refused", "status": None}}).encode(), status)
    assert ask(ga.db, "ga", "question", runtime=ga())["status"] == "uncertain"


@pytest.mark.parametrize("payload", [b"<html>bad gateway</html>", json.dumps({"error": "RESOURCE_EXHAUSTED"}).encode(),
                                     json.dumps({"error": None}).encode(), json.dumps(answer()).encode(), b""])
@pytest.mark.parametrize("status", [400, 402, 429])
def test_a_listed_status_without_an_error_object_is_uncertain(ga, status, payload):
    ga.reply(payload, status)
    result = ask(ga.db, "ga", "question", runtime=ga())
    assert (result["status"], result["response"]) == ("uncertain", None)  # a reply shape outside HTTP 200 is no reply


# ---- transport: the shared exchange -----------------------------------------------------------------------------------------
@pytest.mark.parametrize("status", [301, 302, 307, 308])
def test_a_redirect_is_never_followed_and_is_uncertain(ga, status):
    target = Fake()
    try:
        ga.fake.reply = (status, b"", {"Location": target.url})
        result = ask(ga.db, "ga", "question", runtime=ga())
    finally:
        target.close()
    assert result["status"] == "uncertain" and len(ga.fake.requests) == 1
    assert target.requests == []  # the key never reached another URL


def timed_out_while_unending(ga, part: str, timeout_s: float) -> None:
    """The ask ends as a timeout while the server is still sending (or still silent), after exactly one request."""
    reply = Unending(part)
    ga.fake.reply = reply
    try:
        result = ask(ga.db, "ga", "question", runtime=ga(timeout_s=timeout_s))
        why_at_return = reply.why
    finally:
        reply.release.set()
        reply.ended.wait(Unending.SAFETY_S)
    assert result["status"] == "uncertain" and "runtime_timeout" in evidence_kinds(ga.db)
    # Still going when the ask returned (or cut by the client at its deadline): the ask never waited for the reply to end.
    assert why_at_return in (None, "client gone"), why_at_return
    assert len(ga.fake.requests) == 1


@pytest.mark.timeout(300)
def test_a_timeout_is_uncertain(ga):
    timed_out_while_unending(ga, "silence", 1)


@pytest.mark.timeout(300)
@pytest.mark.parametrize("part", ["headers", "body"])
def test_the_timeout_is_a_total_and_a_slow_drip_does_not_extend_it(ga, part):
    """Every socket operation gets a byte within 50 ms, so only a deadline over the whole call ends this ask."""
    timed_out_while_unending(ga, part, 1.5)


def test_a_dropped_connection_is_uncertain(ga):
    def drop(handler: BaseHTTPRequestHandler) -> None:
        handler.connection.shutdown(socket.SHUT_RDWR)

    ga.fake.reply = drop
    result = ask(ga.db, "ga", "question", runtime=ga())
    assert result["status"] == "uncertain" and "ga request failed" in json.dumps(result)


def test_an_answer_that_echoes_the_key_is_redacted(ga):
    ga.reply(error_body("UNAUTHENTICATED", f"API key not valid: {KEY}.", code=401), 401)
    result = ask(ga.db, "ga", "question", runtime=ga())
    assert result["status"] == "failed_not_started" and "[REDACTED]" in result["detail"]["error"]
    assert KEY not in json.dumps(result) and KEY.encode() not in store_bytes(ga.db)
    ga.reply(f"upstream said {KEY}".encode(), 500)
    uncertain = ask(ga.db, "ga", "question", stream_id="s2", runtime=ga())
    assert uncertain["status"] == "uncertain" and KEY not in json.dumps(uncertain)
    ga.reply(error_body("RESOURCE_EXHAUSTED", f"quota of key {KEY} is used up", code=429), 429)
    limited = ask(ga.db, "ga", "question", stream_id="s3", runtime=ga())
    assert limited["status"] == "not_executed" and "[REDACTED]" in limited["message"] and KEY not in json.dumps(limited)
    ga.reply(answer(candidates=[candidate(content={"parts": [{"text": f"your key is {KEY}"}]})]))
    delivered = ask(ga.db, "ga", "question", stream_id="s4", runtime=ga())
    assert delivered["response"] == "your key is [REDACTED]"
    assert KEY.encode() not in store_bytes(ga.db)
