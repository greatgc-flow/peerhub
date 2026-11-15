"""API peers behind the Session Bridge RuntimeTarget (#378): `ca` (Claude API through the `ant` CLI) and `oa` (OpenAI
Responses API over HTTPS). Extension side only.

One ask is one API call: no tools, no workspace access, no session, at most 16,000 output tokens, never retried.
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

from peerhub.extensions.adapters.base import EXCERPT_CHARS, MAX_PROMPT_BYTES, CliRuntimeTarget
from peerhub.extensions.adapters.process import (DEFAULT_MAX_BYTES, DEFAULT_TIMEOUT_S, BoundedProcess, SpawnFailure,
                                                 decode_stderr)
from peerhub.extensions.adapters.profiles import qualified_profile, resolve_profile
from peerhub.extensions.adapters.sanitize import Sanitizer, env_secrets
from peerhub.extensions.adapters.telemetry import unknown_usage
from peerhub.extensions.runtime_contract import PrespawnError, RuntimeEvent, RuntimeTargetError
from peerhub.extensions.usage_normalization import REPORTED_FIELDS, normalize_provider_usage

MAX_OUTPUT_TOKENS = 16_000
CA_HEADER = "PeerHub ask (text only, no tools, no files)."  # the content never starts with "@": `ant` inlines no file
CA_MODEL = re.compile(r"claude-[a-z0-9.-]{1,64}")
OA_MODEL = re.compile(r"gpt-[a-z0-9.-]{1,64}")
OA_URL = "https://api.openai.com/v1/responses"
OA_KEY_ENV = "OPENAI_API_KEY"
OA_LIMIT_CODES = ("credit_balance_exhausted", "insufficient_quota")
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


def _joined_text(blocks: object, block_type: str) -> str:
    """The joined `text` of the blocks of one type in a JSON list (other blocks, for example thinking, are not text)."""
    parts: list[str] = []
    for block in cast("list[Any]", blocks) if isinstance(blocks, list) else []:
        if isinstance(block, dict):
            item = cast("dict[str, Any]", block)
            if item.get("type") == block_type and isinstance(item.get("text"), str):
                parts.append(item["text"])
    return "".join(parts)


class ApiRuntimeTarget:
    """What ca and oa share: the refusal rules, the binding, the outcome table. A subclass implements `_call`."""

    runtime_kind: str
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
                 on_output: Callable[[str], None] | None = None) -> None:
        del on_output  # one reply arrives at once; the caller prints the stored response
        kind = self.runtime_kind
        if writable:
            raise RequestRejected(f"{kind} has no workspace access; --writable is refused")
        if profile is not None:
            profile = qualified_profile(kind, profile)
            profile_model, profile_effort = resolve_profile(kind, profile, workspace)
            model = model if model is not None else profile_model
            effort = effort if effort is not None else profile_effort
        if effort is not None:
            raise RequestRejected(f"{kind} takes no effort")
        if model is None:
            raise RequestRejected(f"{kind} needs --profile ({kind}.standard, {kind}.effort, {kind}.deepthink) or --model")
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
            return Outcome("truncated", text, f"{self.runtime_kind} reply cut at {MAX_OUTPUT_TOKENS} output tokens ({why})", usage)
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
    _usage_source = "ca.message.usage"

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
            raise PrespawnError("ant executable not found")
        # Keys, tokens, base URL and profile overrides are removed: the call uses the `ant` login and the official endpoint.
        # The batch ids describe the ask process only (#374).
        env = {name: value for name, value in merged.items()
               if not name.upper().startswith("ANTHROPIC_") and name.upper() not in ("PEERHUB_BATCH_ID", "PEERHUB_BATCH_TASK")}
        body = json.dumps({"model": self._model_id, "max_tokens": MAX_OUTPUT_TOKENS,
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
            if isinstance(message.get("content"), list) and isinstance(stop, str):
                return self._reply(_joined_text(message["content"], "text"), message.get("usage"), complete=stop in ("end_turn", "stop_sequence"),
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


def oa_opener() -> urllib.request.OpenerDirector:
    """No proxy from the environment, no followed redirect, TLS verified by the default context (a certificate for the host name)."""
    return urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect(),
                                       urllib.request.HTTPSHandler(context=ssl.create_default_context()))


class OaRuntimeTarget(ApiRuntimeTarget):
    """One POST to the Responses API with the standard library. The key is read from OPENAI_API_KEY at the call.

    `url` exists for tests only: `ask` passes none, and no variable or option changes the host (OPENAI_BASE_URL is ignored).
    The timeout of the ask is a total over the whole call; the silence timeout, when given, limits one socket operation.
    """

    runtime_kind = "oa"
    _model = OA_MODEL
    _usage_source = "oa.response.usage"

    def __init__(self, workspace: str | Path, *, url: str = OA_URL, **options: Any) -> None:
        super().__init__(workspace, **options)
        self.url = url

    def _call(self, prompt: str) -> Outcome:
        key = os.environ.get(OA_KEY_ENV)
        if not key:
            raise PrespawnError(f"set {OA_KEY_ENV}")
        if not key.isascii() or not key.isprintable() or " " in key:  # http.client would quote a refused header value in its error
            raise PrespawnError(f"{OA_KEY_ENV} is not usable in a header (printable ASCII without spaces)")
        san = Sanitizer([*env_secrets(), key]).with_prompt(prompt)
        body = json.dumps({"model": self._model_id, "input": prompt, "max_output_tokens": MAX_OUTPUT_TOKENS,
                           "store": False}).encode("utf-8")
        request = urllib.request.Request(self.url, data=body, method="POST", headers={
            "Authorization": f"Bearer {key}", "Content-Type": "application/json"})
        per_operation = min(self._timeout_s, self._silence_timeout_s or self._timeout_s)
        answers: list[tuple[int, bytes] | Exception] = []

        def exchange() -> None:
            try:
                try:
                    with oa_opener().open(request, timeout=per_operation) as response:
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
            return Outcome("uncertain", detail=san.clean(f"oa request failed: {type(answer).__name__}: {answer}", EXCERPT_CHARS))
        status, raw = answer
        doc = _json_object(raw.decode("utf-8", "replace")) if len(raw) <= self._max_bytes else None
        error: object = doc.get("error") if doc is not None else None
        if isinstance(error, dict):
            detail = cast("dict[str, Any]", error)
            text = san.clean(f"oa: HTTP {status}: {detail.get('code')}: {detail.get('message')}", EXCERPT_CHARS)
            if detail.get("code") in OA_LIMIT_CODES or status != 200:  # the code is checked before the status
                return self._error(status, detail.get("code") in OA_LIMIT_CODES, text)
        state: object = doc.get("status") if doc is not None else None
        if status != 200 or doc is None or not isinstance(state, str):
            excerpt = san.clean(" ".join(raw[:EXCERPT_CHARS * 4].decode("utf-8", "replace").split()), EXCERPT_CHARS)
            return Outcome("uncertain", detail=f"oa: HTTP {status}: unexpected answer: {excerpt}")
        items: object = doc.get("output")
        reply = "".join(_joined_text(cast("dict[str, Any]", item).get("content"), "output_text")
                        for item in (cast("list[Any]", items) if isinstance(items, list) else []) if isinstance(item, dict))
        details: object = doc.get("incomplete_details")
        reason: object = cast("dict[str, Any]", details).get("reason") if isinstance(details, dict) else None
        return self._reply(reply, doc.get("usage"), complete=state == "completed",
                           truncated=state == "incomplete" and reason == "max_output_tokens",
                           why=san.clean(f"status {state}, reason {reason}", 80))


API_TARGETS: dict[str, Callable[..., ApiRuntimeTarget]] = {"ca": CaRuntimeTarget, "oa": OaRuntimeTarget}
