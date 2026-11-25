"""API peers behind the Session Bridge RuntimeTarget (#378, #381). Extension side only.

`ca` is the Claude API through the `ant` CLI; `oa` (OpenAI Responses API) and `ga` (Gemini API) are one HTTPS request.

One ask is one API call: no tools, no workspace access, no session, never retried, and an output limit per class
(`max_output_tokens`: 16,000 for ca and oa; 32,000 for ga, where thinking counts against it).
Each target adds only its transport and field names; the base maps one `Outcome` onto the runtime events:

    complete        -> ("started", ...) then ("terminal", ...); empty text is an empty reply as for other peers
    truncated       -> ("started", ...) then ("incomplete", ...): the reply was cut at the token limit
    incomplete      -> ("started", ...) then ("incomplete", ...): the reply ended for another reason
    provider_limit  -> ("started", ...) then ("refusal", quota_exhausted, no reset time)
    not_started     -> PrespawnError before any event: the provider did no inference
    timeout         -> ("started", ...) then ("timeout", ...): uncertain
    uncertain       -> ("started", ...) then RuntimeTargetError

For these peers "started" means that inference may have started: the call runs to its end before the first event, so an
answer that proves no inference (a listed HTTP status with an error object) stays NOT_STARTED although `ant` ran or a request
was sent. PeerHub never reads, stores or prints a credential; error text passes the sanitizer.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import ssl
import threading
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence, cast

from peerhub.extensions.adapters.base import EXCERPT_CHARS, MAX_PROMPT_BYTES, SETUP_HINT, CliRuntimeTarget, executable_not_found
from peerhub.extensions.adapters.process import (DEFAULT_MAX_BYTES, DEFAULT_TIMEOUT_S, BoundedProcess, SpawnFailure,
                                                 decode_stderr)
from peerhub.extensions.adapters.profiles import qualified_profile, resolve_profile
from peerhub.extensions.adapters.sanitize import Sanitizer, env_secrets
from peerhub.extensions.runtime_contract import PrespawnError, RuntimeEvent, RuntimeTargetError
from peerhub.extensions.usage_normalization import (
    PROVIDER_USAGE_CONTRACT, REPORTED_FIELDS, normalize_provider_usage, unknown_usage,
)

CA_HEADER = "PeerHub ask (text only, no tools, no files)."  # the content never starts with "@": `ant` inlines no file
CA_MODEL = re.compile(r"claude-[a-z0-9.-]{1,64}")
OA_MODEL = re.compile(r"gpt-[a-z0-9.-]{1,64}")
OA_URL = "https://api.openai.com/v1/responses"
OA_KEY_ENV = "OPENAI_API_KEY"
OA_LIMIT_CODES = ("credit_balance_exhausted", "insufficient_quota")
# The model is one URL path segment between `models/` and `:generateContent`: the pattern (with fullmatch) keeps `/`, `:`,
# `?`, `#`, `%`, whitespace and uppercase out of the URL; a `..` inside the segment is harmless there.
GA_MODEL = re.compile(r"gemini-[a-z0-9.-]{1,64}")
GA_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
GA_KEY_ENV = "GEMINI_API_KEY"
# The pairs (HTTP status, error.status) of a provider limit: no inference ran. 429 is a rate limit and spent or missing
# quota alike; 402 is a depleted prepay balance (measured 2026-11-16, #384).
GA_LIMITS = ((402, "RESOURCE_EXHAUSTED"), (429, "RESOURCE_EXHAUSTED"))
GA_NOT_STARTED = {400: ("INVALID_ARGUMENT", "FAILED_PRECONDITION"), 401: ("UNAUTHENTICATED",), 403: ("PERMISSION_DENIED",),
                  404: ("NOT_FOUND",)}  # the pairs (HTTP status, error.status) that prove no inference; 413: any status
NOT_STARTED_STATUS = (400, 401, 403, 404, 413, 429)  # with an error object: refused before inference
_CA_CREDIT_STATUS = (400, 401, 403, 404, 413)
_CA_CREDIT_TEXT = "credit balance is too low"
_ANT_STATUS = re.compile(r"\b(?:POST|GET) \S+: ([1-5][0-9]{2})\b")  # stderr: `POST "<url>": 400 Bad Request`
_ERROR_OBJECTS_SCANNED = 20


class RequestRejected(ValueError):
    """The request was refused before anything was started (nothing stored, nothing sent)."""

    code = "request_rejected"


@dataclass(frozen=True)
class Outcome:
    kind: str  # complete | truncated | incomplete | provider_limit | not_started | timeout | uncertain
    text: str = ""  # the reply text (complete, truncated, incomplete)
    detail: str = ""  # sanitized, bounded text for the error
    usage: object = None  # the provider's usage object, unvalidated
    termination_confirmed: bool | None = None


def _json_object(text: str) -> dict[str, Any] | None:
    """`text` as exactly one JSON object, else None."""
    try:
        value: object = json.loads(text)
    except (ValueError, RecursionError):
        return None
    return cast("dict[str, Any]", value) if isinstance(value, dict) else None


def _joined_text(blocks: object, block_type: str) -> str | None:
    """The joined `text` of the blocks of one type in a JSON list (other blocks, for example thinking, are not text).

    None for a wrong shape: not a list, a block that is not an object, a block of that type whose text is not a string."""
    if not isinstance(blocks, list):
        return None
    parts: list[str] = []
    for block in cast("list[Any]", blocks):
        if not isinstance(block, dict):
            return None
        item = cast("dict[str, Any]", block)
        if item.get("type") == block_type:
            if not isinstance(item.get("text"), str):
                return None
            parts.append(item["text"])
    return "".join(parts)


def _oa_text(output: object) -> str | None:
    """The joined `output_text` of the message items of a Responses `output`; None for a wrong shape (not a list, an
    item that is not an object with a type, a message whose content is not a list of well-formed blocks)."""
    if not isinstance(output, list):
        return None
    parts: list[str] = []
    for entry in cast("list[Any]", output):
        if not isinstance(entry, dict) or not isinstance(cast("dict[str, Any]", entry).get("type"), str):
            return None
        item = cast("dict[str, Any]", entry)
        if item["type"] == "message":  # other items (for example reasoning) carry no reply text
            text = _joined_text(item.get("content"), "output_text")
            if text is None:
                return None
            parts.append(text)
    return "".join(parts)


def _ga_text(candidate: dict[str, Any]) -> str | None:
    """The joined `text` of the parts of a Gemini candidate without `thought: true`; a missing `content` or missing
    `parts` is no text. None for a wrong shape: a content that is present and not an object, parts that are present and
    not a list, a part that is not an object, a `text` that is present and not a string (in a thought part too)."""
    content: object = candidate.get("content", {})
    parts: object = cast("dict[str, Any]", content).get("parts", []) if isinstance(content, dict) else None
    if not isinstance(parts, list):
        return None
    texts: list[str] = []
    for entry in cast("list[Any]", parts):
        if not isinstance(entry, dict):
            return None
        part = cast("dict[str, Any]", entry)
        if "text" in part:
            if not isinstance(part["text"], str):
                return None
            if part.get("thought") is not True:
                texts.append(part["text"])
    return "".join(texts)


def _ga_usage(meta: object) -> dict[str, int] | None:
    """`usageMetadata` as {"input_tokens", "output_tokens"}, or None when it is not measured (absent, never zero).

    Measured: an object whose `promptTokenCount` is an int >= 0 and whose `candidatesTokenCount` and `thoughtsTokenCount`,
    when present, are ints >= 0 (bool excluded). Only these two count as 0 when missing; every other key is ignored.
    The output is their sum: thinking is output, and the provider counts it against the limit."""
    if not isinstance(meta, dict):
        return None
    counts = cast("dict[str, Any]", meta)
    values: list[object] = [counts.get("promptTokenCount"), counts.get("candidatesTokenCount", 0), counts.get("thoughtsTokenCount", 0)]
    if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in values):
        return None
    prompt, candidates, thoughts = cast("list[int]", values)
    return {"input_tokens": prompt, "output_tokens": candidates + thoughts}


class ApiRuntimeTarget:
    """What ca, oa and ga share: the refusal rules, the binding, the outcome table. A subclass implements `_call`."""

    runtime_kind: str
    max_output_tokens = 16_000  # sent in the request and named by the truncation message; a class may set another limit
    profile_tiers: tuple[str, ...] = ("standard", "effort", "deepthink")  # the packaged profiles, for the refusal message
    stateless = True  # `ask` refuses a second ask on a stream: there is no session to continue
    resumable = False
    supports_interrupt = False
    supports_terminate = False
    supports_steer = False
    _model: re.Pattern[str]
    _usage_source: str

    def __init__(self, workspace: str | Path, *, model: str | None = None, effort: str | None = None,
                 profile: str | None = None, silence_timeout_s: float | None = None, writable: bool = False,
                 timeout_s: float = DEFAULT_TIMEOUT_S, max_bytes: int = DEFAULT_MAX_BYTES,
                 on_output: Callable[[str], None] | None = None, notice_alias: bool = False) -> None:
        del on_output  # one reply arrives at once; the caller prints the stored response
        kind = self.runtime_kind
        if writable:
            raise RequestRejected(f"{kind} has no workspace access; --writable is refused")
        if profile is not None:
            profile = qualified_profile(kind, profile)
            profile_model, profile_effort = resolve_profile(kind, profile, workspace, notice_alias=notice_alias)
            model = model if model is not None else profile_model
            effort = effort if effort is not None else profile_effort
        if effort is not None:
            raise RequestRejected(f"{kind} takes no effort")
        if model is None:
            raise RequestRejected(f"{kind} needs --profile ({', '.join(f'{kind}.{tier}' for tier in self.profile_tiers)}) or --model")
        if not self._model.fullmatch(model):
            raise RequestRejected(f"{kind} model must match ^{self._model.pattern}$")
        self._workspace = str(workspace)
        self._model_id, self._profile = model, profile
        self._timeout_s, self._silence_timeout_s, self._max_bytes = timeout_s, silence_timeout_s, max_bytes

    # ---- RuntimeTarget
    def fingerprint(self) -> str:
        return f"adapter:{self.runtime_kind}:v1"

    def binding(self) -> str:
        binding = f"{self._model_id}/default"
        return f"{self._profile}/{binding}" if self._profile is not None else binding

    def session_binding(self) -> dict[str, Any]:
        return dict(profile=self._profile, model=self._model_id, effort=None, writable=False)

    def create_session(self) -> str:
        return f"{self.runtime_kind}-local-{uuid.uuid4().hex}"  # a local handle: the provider keeps no session

    def resume_session(self, external_session_id: str, *, vendor_session_id: str | None = None) -> str:
        return "unsupported"

    def deliver(self, external_session_id: str, record: Any, catch_up: list[Any]) -> Iterable[RuntimeEvent]:
        kind = self.runtime_kind
        prompt = CliRuntimeTarget._prompt(record, catch_up)  # pyright: ignore[reportPrivateUsage]
        n_bytes = len(prompt.encode("utf-8", "replace"))
        if n_bytes > MAX_PROMPT_BYTES:
            raise PrespawnError(f"prompt is {n_bytes} bytes, exceeding the {kind} inline limit of {MAX_PROMPT_BYTES} bytes "
                                f"(catch-up history is never truncated silently; lower the catch-up budget)")
        if not Path(self._workspace).is_dir():
            raise PrespawnError("workspace is not a directory")
        outcome = self._call(prompt)
        if outcome.kind == "not_started":
            raise PrespawnError(outcome.detail)
        yield ("started", f"{kind}:{uuid.uuid4().hex[:8]}")
        if outcome.kind == "timeout":
            yield ("timeout", self._timeout_s, outcome.termination_confirmed)
            return
        if outcome.kind == "uncertain":
            raise RuntimeTargetError(outcome.detail, termination_confirmed=outcome.termination_confirmed)
        if outcome.kind == "provider_limit":
            yield ("refusal", {"outcome": "NOT_EXECUTED", "reason": "quota_exhausted", "message": outcome.detail,
                               "reset_time": None})
            return
        text = Sanitizer(env_secrets()).clean(outcome.text)  # secrets redacted; the prompt may legitimately be quoted
        usage: object = outcome.usage
        reported = {key: value for key, value in cast("dict[str, Any]", usage).items()
                    if key in REPORTED_FIELDS[kind] and isinstance(value, int) and not isinstance(value, bool) and value >= 0
                    } if isinstance(usage, dict) else {}
        telemetry = unknown_usage("usage_absent") if not reported else {
            "schema_version": 1, "scope": "delivery", "source": self._usage_source, "reported": reported,
            "normalized": normalize_provider_usage(kind, reported), "coverage": {"state": "MEASURED", "reason": None}}
        payload: dict[str, Any] = {"response": text, "provider_telemetry": telemetry}
        counters = {key: reported[key] for key in ("input_tokens", "output_tokens") if key in reported}
        if counters:
            payload["usage"] = counters
        if outcome.kind in ("truncated", "incomplete"):
            yield ("incomplete", {**payload, "code": outcome.kind, "message": outcome.detail})
            return
        if not text.strip():
            payload["empty_response"] = True
        yield ("terminal", payload)

    # ---- for subclasses
    def _call(self, prompt: str) -> Outcome:
        raise NotImplementedError

    def _reply(self, text: str, usage: object, *, complete: bool, truncated: bool, why: str) -> Outcome:
        if complete:
            return Outcome("complete", text, usage=usage)
        if truncated:
            return Outcome("truncated", text, f"{self.runtime_kind} reply cut at {self.max_output_tokens} output tokens ({why})", usage)
        return Outcome("incomplete", text, f"{self.runtime_kind} reply is not complete ({why})", usage)

    @staticmethod
    def _error(status: int | None, limit: bool, detail: str) -> Outcome:
        """The shared failure table: a limit first, then the listed statuses; anything else is uncertain."""
        if limit:
            return Outcome("provider_limit", detail=detail)
        return Outcome("not_started" if status in NOT_STARTED_STATUS else "uncertain", detail=detail)


class CaRuntimeTarget(ApiRuntimeTarget):
    """`ant messages create --format json` with the request body on stdin. `command` replaces `ant` (tests)."""

    runtime_kind = "ca"
    _model = CA_MODEL
    _usage_source = PROVIDER_USAGE_CONTRACT["ca"][1]

    def __init__(self, workspace: str | Path, *, command: Sequence[str] | None = None,
                 env_extra: Mapping[str, str] | None = None, **options: Any) -> None:
        super().__init__(workspace, **options)
        self._command = list(command) if command is not None else None
        self._env_extra = dict(env_extra or {})

    def _call(self, prompt: str) -> Outcome:
        merged = {**os.environ, **self._env_extra}
        san = Sanitizer(env_secrets(merged)).with_prompt(prompt)
        found = shutil.which("ant") if self._command is None else None
        base = self._command if self._command is not None else [found] if found else None
        if base is None:
            raise PrespawnError(executable_not_found("ant"))
        # Keys, tokens, base URL and profile overrides are removed: the call uses the `ant` login and the official endpoint.
        # The batch ids describe the ask process only (#374).
        env = {name: value for name, value in merged.items()
               if not name.upper().startswith("ANTHROPIC_") and name.upper() not in ("PEERHUB_BATCH_ID", "PEERHUB_BATCH_TASK")}
        body = json.dumps({"model": self._model_id, "max_tokens": self.max_output_tokens,
                           "messages": [{"role": "user", "content": f"{CA_HEADER}\n\n{prompt}"}]}).encode("utf-8")
        proc = BoundedProcess([*base, "messages", "create", "--format", "json"], stdin=body, cwd=self._workspace, env=env,
                              timeout_s=self._timeout_s, max_bytes=self._max_bytes, silence_timeout_s=self._silence_timeout_s)
        try:
            proc.start()
        except SpawnFailure as e:
            raise PrespawnError(san.clean(f"cannot spawn ant: {e}", EXCERPT_CHARS)) from e
        finished = False
        try:
            res = proc.wait()
            finished = True
        finally:
            if not finished:  # interrupted while waiting: never leave an orphan process
                proc.abort()
        if res.timed_out:
            return Outcome("timeout", termination_confirmed=res.termination_confirmed)
        for flag in ("silence_timed_out", "aborted", "drain_timed_out", "stdout_truncated"):
            if getattr(res, flag):
                return Outcome("uncertain", detail=f"ca call ended without a reply ({flag})",
                               termination_confirmed=res.termination_confirmed)
        out = res.stdout.decode("utf-8", "replace")
        message = _json_object(out)
        if res.returncode == 0 and message is not None and message.get("type") == "message":
            stop: object = message.get("stop_reason")
            reply = _joined_text(message.get("content"), "text")
            if reply is not None and isinstance(stop, str):  # a wrong shape is never a reply: it falls through to uncertain
                return self._reply(reply, message.get("usage"), complete=stop in ("end_turn", "stop_sequence"),
                                   truncated=stop == "max_tokens", why=san.clean(f"stop_reason {stop}", 80))
        output = out + "\n" + decode_stderr(res.stderr)
        status_match = _ANT_STATUS.search(output)
        status = int(status_match.group(1)) if status_match else None
        error = self._error_object(output)
        if status is not None and error is not None:
            kind, text = error.get("type"), str(error.get("message"))
            # Measured (ant 2.9.0): without a login the API answers 401 authentication_error "x-api-key header is required".
            who = ("ant is not signed in or its login expired (run: ant auth login)"
                   if status == 401 and kind == "authentication_error" else "ca")
            return self._error(status, status in _CA_CREDIT_STATUS and _CA_CREDIT_TEXT in text.lower(),
                               san.clean(f"{who}: HTTP {status}: {kind}: {text}", EXCERPT_CHARS))
        excerpt = san.clean(" ".join(output.split()), EXCERPT_CHARS)
        return Outcome("uncertain", detail=f"ca call ended without a complete reply (exit {res.returncode}): {excerpt}")

    @staticmethod
    def _error_object(output: str) -> dict[str, Any] | None:
        """The `error` of the first API error object ({"type": "error", "error": {...}}) printed by `ant`."""
        decoder = json.JSONDecoder()
        start = output.find("{")
        for _ in range(_ERROR_OBJECTS_SCANNED):
            if start < 0:
                break
            try:
                value, _end = decoder.raw_decode(output, start)
            except (ValueError, RecursionError):
                value = None
            if isinstance(value, dict):
                doc = cast("dict[str, Any]", value)
                error: object = doc.get("error")
                if doc.get("type") == "error" and isinstance(error, dict):
                    return cast("dict[str, Any]", error)
            start = output.find("{", start + 1)
        return None


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """A 3xx answer is never followed, so the key is never sent to another URL."""

    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> None:
        return None


def http_opener() -> urllib.request.OpenerDirector:
    """No proxy from the environment, no followed redirect, TLS verified by the default context (a certificate for the host name)."""
    return urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect(),
                                       urllib.request.HTTPSHandler(context=ssl.create_default_context()))


oa_opener = http_opener  # the name of 0.24.18, when oa was the only HTTP peer


class HttpRuntimeTarget(ApiRuntimeTarget):
    """What oa and ga share: one HTTPS POST with the standard library (`_call`). A subclass gives three hooks: the name of
    the key variable (`key_env`), the request (`_request`) and the outcome of one HTTP answer (`_classify`).

    The key is read from the environment at the call, sent in one header only and never stored or printed. `self.url` is
    fixed by the class: no variable or option changes the host. The timeout of the ask is a total over the whole call;
    the silence timeout, when given, limits one socket operation.
    """

    key_env: str
    url: str

    def _call(self, prompt: str) -> Outcome:
        key = os.environ.get(self.key_env)
        if not key:
            raise PrespawnError(f"set {self.key_env} {SETUP_HINT}")
        if not key.isascii() or not key.isprintable() or " " in key:  # http.client would quote a refused header value in its error
            raise PrespawnError(f"{self.key_env} is not usable in a header (printable ASCII without spaces)")
        san = Sanitizer([*env_secrets(), key]).with_prompt(prompt)
        request = self._request(prompt, key)
        per_operation = min(self._timeout_s, self._silence_timeout_s or self._timeout_s)
        answers: list[tuple[int, bytes] | Exception] = []

        def exchange() -> None:
            try:
                try:
                    with http_opener().open(request, timeout=per_operation) as response:
                        answers.append((int(response.status), cast(bytes, response.read(self._max_bytes + 1))))
                except urllib.error.HTTPError as e:
                    with e:
                        answers.append((int(e.code), e.read(self._max_bytes + 1)))
            except Exception as e:  # URLError, a timeout, a dropped connection: all uncertain
                answers.append(e)

        # A socket timeout limits one operation, so a slow drip would never end: the whole call waits on one deadline.
        # After the deadline the daemon thread and its socket live until the server stops sending or the process exits
        # (fine for the CLI: one ask per process).
        worker = threading.Thread(target=exchange, daemon=True)
        worker.start()
        worker.join(self._timeout_s)
        answer = answers[0] if answers else TimeoutError()
        if isinstance(answer, Exception):
            if isinstance(answer, TimeoutError) or isinstance(getattr(answer, "reason", None), TimeoutError):
                return Outcome("timeout")
            return Outcome("uncertain", detail=san.clean(f"{self.runtime_kind} request failed: {type(answer).__name__}: {answer}",
                                                         EXCERPT_CHARS))
        return self._classify(*answer, san)

    # ---- the hooks
    def _request(self, prompt: str, key: str) -> urllib.request.Request:
        raise NotImplementedError

    def _classify(self, status: int, raw: bytes, san: Sanitizer) -> Outcome:
        raise NotImplementedError


class OaRuntimeTarget(HttpRuntimeTarget):
    """One POST to the Responses API. The key is read from OPENAI_API_KEY at the call.

    `url` exists for tests only: `ask` passes none, and no variable or option changes the host (OPENAI_BASE_URL is ignored).
    """

    runtime_kind = "oa"
    key_env = OA_KEY_ENV
    _model = OA_MODEL
    _usage_source = PROVIDER_USAGE_CONTRACT["oa"][1]

    def __init__(self, workspace: str | Path, *, url: str = OA_URL, **options: Any) -> None:
        super().__init__(workspace, **options)
        self.url = url

    def _request(self, prompt: str, key: str) -> urllib.request.Request:
        body = json.dumps({"model": self._model_id, "input": prompt, "max_output_tokens": self.max_output_tokens,
                           "store": False}).encode("utf-8")
        return urllib.request.Request(self.url, data=body, method="POST", headers={
            "Authorization": f"Bearer {key}", "Content-Type": "application/json"})

    def _classify(self, status: int, raw: bytes, san: Sanitizer) -> Outcome:
        """The outcome of one HTTP answer. The order matters:

        1. HTTP 200: only the status and the shape of the response object decide. A wrong shape, or `completed`
           with an error object (it contradicts itself), is uncertain; else `completed` is complete, `incomplete` with
           the reason `max_output_tokens` is truncated, any other status is incomplete. An error code is never read
           here: at 200 it is no proof that nothing ran.
        2. Any other HTTP status with an error object (an error answer): the error code first (a quota code is a
           provider limit at any such status), then the status (a listed one is not started, any other is uncertain).
        3. Everything else is uncertain.
        """
        doc = _json_object(raw.decode("utf-8", "replace")) if len(raw) <= self._max_bytes else None
        error: object = doc.get("error") if doc is not None else None
        if status != 200 and isinstance(error, dict):
            detail = cast("dict[str, Any]", error)
            return self._error(status, detail.get("code") in OA_LIMIT_CODES,
                               san.clean(f"oa: HTTP {status}: {detail.get('code')}: {detail.get('message')}", EXCERPT_CHARS))
        state: object = doc.get("status") if doc is not None else None
        reply = _oa_text(doc.get("output")) if doc is not None else None
        if status != 200 or doc is None or not isinstance(state, str) or reply is None or (state == "completed" and error is not None):
            excerpt = san.clean(" ".join(raw[:EXCERPT_CHARS * 4].decode("utf-8", "replace").split()), EXCERPT_CHARS)
            return Outcome("uncertain", detail=f"oa: HTTP {status}: unexpected answer: {excerpt}")
        details: object = doc.get("incomplete_details")
        reason: object = cast("dict[str, Any]", details).get("reason") if isinstance(details, dict) else None
        return self._reply(reply, doc.get("usage"), complete=state == "completed",
                           truncated=state == "incomplete" and reason == "max_output_tokens",
                           why=san.clean(f"status {state}, reason {reason}", 80))


class GaRuntimeTarget(HttpRuntimeTarget):
    """One POST to the Gemini API (`generateContent`). The key is read from GEMINI_API_KEY at the call and sent in the
    header `x-goog-api-key` only, never in the URL.

    `base_url` exists for tests only: `ask` passes none, and no variable or option changes the host. The model is one
    path segment of the URL; `GA_MODEL` keeps every character that could change the path out of it.
    """

    runtime_kind = "ga"
    max_output_tokens = 32_000  # thinking counts against the limit (measured: 20 tokens gave MAX_TOKENS and no text)
    profile_tiers = ("standard", "effort")  # no deep profile is packaged (#381): a stronger model goes by --model
    key_env = GA_KEY_ENV
    _model = GA_MODEL
    _usage_source = PROVIDER_USAGE_CONTRACT["ga"][1]

    def __init__(self, workspace: str | Path, *, base_url: str = GA_BASE_URL, **options: Any) -> None:
        super().__init__(workspace, **options)
        self.url = f"{base_url}/models/{self._model_id}:generateContent"

    def _request(self, prompt: str, key: str) -> urllib.request.Request:
        body = json.dumps({"contents": [{"role": "user", "parts": [{"text": prompt}]}],
                           "generationConfig": {"maxOutputTokens": self.max_output_tokens}}).encode("utf-8")
        return urllib.request.Request(self.url, data=body, method="POST", headers={
            "x-goog-api-key": key, "Content-Type": "application/json"})

    def _classify(self, status: int, raw: bytes, san: Sanitizer) -> Outcome:
        """The outcome of one HTTP answer. The order matters:

        1. Any HTTP status other than 200 with an error object (an error answer): the pair (HTTP status, `error.status`)
           decides. 402 or 429 with RESOURCE_EXHAUSTED is a provider limit; a pair of `GA_NOT_STARTED`, 413 with any
           status, and 400, 401, 403, 404, 413 or 429 without an `error.status` are not started; every other pair is
           uncertain (an unknown status, a pair that contradicts itself, any 5xx, 402 with another status or without one).
        2. HTTP 200: only the shape and the finish reason decide, in this order. Not one JSON object, or an `error` that
           is not null: uncertain. No candidates (missing or an empty list) with a `promptFeedback.blockReason` string:
           incomplete with that reason; no candidate list otherwise: uncertain. A first candidate that is not an object
           or has no `finishReason` string, or a malformed `content`: uncertain. Then STOP is complete, MAX_TOKENS is
           truncated, any other finish reason is incomplete; the text is kept in both cases.
        3. Everything else is uncertain.
        """
        doc = _json_object(raw.decode("utf-8", "replace")) if len(raw) <= self._max_bytes else None
        error: object = doc.get("error") if doc is not None else None
        if status != 200 and isinstance(error, dict):
            detail = cast("dict[str, Any]", error)
            name: object = detail.get("status")
            text = san.clean(f"ga: HTTP {status}: {name}: {detail.get('message')}", EXCERPT_CHARS)
            if "status" not in detail:
                return self._error(status, False, text)
            if (status, name) in GA_LIMITS:
                return Outcome("provider_limit", detail=text)
            listed = status == 413 or (isinstance(name, str) and name in GA_NOT_STARTED.get(status, ()))
            return Outcome("not_started" if listed else "uncertain", detail=text)
        if status == 200 and doc is not None and error is None:
            usage = _ga_usage(doc.get("usageMetadata"))
            candidates: object = doc.get("candidates", [])
            if candidates == []:  # missing or an empty list: only a blocked prompt explains it
                feedback: object = doc.get("promptFeedback")
                block: object = cast("dict[str, Any]", feedback).get("blockReason") if isinstance(feedback, dict) else None
                if isinstance(block, str):
                    return self._reply("", usage, complete=False, truncated=False, why=san.clean(f"blockReason {block}", 80))
            elif isinstance(candidates, list) and isinstance(cast("list[Any]", candidates)[0], dict):
                first = cast("dict[str, Any]", cast("list[Any]", candidates)[0])
                finish: object = first.get("finishReason")
                reply = _ga_text(first)
                if isinstance(finish, str) and reply is not None:
                    return self._reply(reply, usage, complete=finish == "STOP", truncated=finish == "MAX_TOKENS",
                                       why=san.clean(f"finishReason {finish}", 80))
        excerpt = san.clean(" ".join(raw[:EXCERPT_CHARS * 4].decode("utf-8", "replace").split()), EXCERPT_CHARS)
        return Outcome("uncertain", detail=f"ga: HTTP {status}: unexpected answer: {excerpt}")


API_TARGETS: dict[str, Callable[..., ApiRuntimeTarget]] = {"ca": CaRuntimeTarget, "oa": OaRuntimeTarget, "ga": GaRuntimeTarget}
