"""One bounded external A2A binding (M3.2): JSON-RPC 2.0 over HTTP, `tasks/send`, `tasks/get`, `tasks/cancel`.

Binding profile `a2a-jsonrpc-subset`. It maps the remote protocol to PeerHub's small task states and keeps the remote protocol
state opaque in `ExternalExecutionRef` (an A2A Task is never a PeerHub Work, an A2A message never a Record). Properties:

- https only; plain http is accepted for loopback hosts only (explicit local testing/dev), never for a remote host
- no redirects, a bounded response size, and the timeout is a TOTAL deadline for the call (connect, headers and body)
- the JSON-RPC envelope is verified: version 2.0, the id we sent, exactly one of result/error, an integer error code, and the
  returned task id must be the one that was asked for (a response about someone else's task is rejected)
- it cannot PROVE that a task was never started (a slow send may still arrive after a "not found"), so `definitive_absence` is False:
  an uncertain task is resolved by finding it, or by an explicit `abandon_task` that burns the id
- the task id is the remote idempotency identity, so `lookup` (`tasks/get`) can reconcile an uncertain submission
- an unmapped remote state (input-required, auth-required, unknown, ...) fails closed instead of being guessed
- credentials are injected by the composition root as headers; they are never part of an Agent Card or a Record

Honest scope: exercised against an in-process JSON-RPC server that implements the same subset. Interoperability with third-party
A2A servers is NOT verified here.
"""

from __future__ import annotations

import ipaddress
import json
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from typing import Any, Mapping, cast

from peerhub.extensions.a2a import (A2AStateTransitionError, A2ATaskRequest, A2ATaskResponse, A2ATaskUnknownToRemoteError,
                                    A2ATransportUnavailableError, AgentCard, ExternalExecutionRef)

TASK_NOT_FOUND = -32001  # A2A "task not found" JSON-RPC error code
_STATES = {"submitted": "SUBMITTED", "working": "RUNNING", "completed": "COMPLETED", "failed": "FAILED",
           "rejected": "FAILED", "canceled": "CANCELLED"}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:  # a redirect could leave the declared endpoint
        return None


class RemoteRpcError(Exception):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(f"remote error {code}: {message}")
        self.code = code


def _is_loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


class HttpA2ATransport:
    definitive_absence = False  # "not found now" never proves an earlier send cannot still arrive

    def __init__(self, *, headers: Mapping[str, str] | None = None, max_response_bytes: int = 1_000_000) -> None:
        if type(max_response_bytes) is not int or max_response_bytes < 1:
            raise ValueError("max_response_bytes must be a positive integer")
        self._headers = dict(headers or {})
        self._max = max_response_bytes

    # ------------------------------------------------------------------ plumbing
    def _endpoint(self, card: AgentCard) -> str:
        url = urllib.parse.urlsplit(card.endpoint_url)
        host = url.hostname or ""
        if url.scheme == "https" and host:
            return card.endpoint_url
        if url.scheme == "http" and _is_loopback(host):
            return card.endpoint_url
        raise A2ATransportUnavailableError("The A2A endpoint must be https (http only for a loopback host)")

    def _rpc(self, card: AgentCard, method: str, params: dict[str, Any], timeout_s: float) -> dict[str, Any]:
        rpc_id = uuid.uuid4().hex
        body = json.dumps({"jsonrpc": "2.0", "id": rpc_id, "method": method, "params": params}, allow_nan=False).encode("utf-8")
        request = urllib.request.Request(self._endpoint(card), data=body, method="POST",
                                         headers={"Content-Type": "application/json", **self._headers})
        opener = urllib.request.build_opener(_NoRedirect)
        deadline = time.monotonic() + timeout_s
        try:
            with opener.open(request, timeout=timeout_s) as response:
                raw = self._read_bounded(response, deadline)
        except urllib.error.HTTPError as exc:  # JSON-RPC errors normally arrive with HTTP 200; any other status is a failure
            raise A2ATransportUnavailableError(f"remote answered HTTP {exc.code}") from exc
        try:
            document = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise A2AStateTransitionError("Remote response is not valid JSON") from exc
        if not isinstance(document, dict):
            raise A2AStateTransitionError("Remote response is not a JSON-RPC object")
        doc = cast(dict[str, Any], document)
        if doc.get("jsonrpc") != "2.0" or doc.get("id") != rpc_id or ("result" in doc) == ("error" in doc):
            raise A2AStateTransitionError("Remote response is not the JSON-RPC 2.0 answer to this request")
        if "error" in doc:
            err = doc["error"]
            code = cast(dict[str, Any], err).get("code") if isinstance(err, dict) else None
            if type(code) is not int:  # a float/str/bool code is a malformed envelope, never "task not found"
                raise A2AStateTransitionError("Remote error envelope has no integer code")
            raise RemoteRpcError(code, str(cast(dict[str, Any], err).get("message", "")))
        result = doc.get("result")
        if not isinstance(result, dict):
            raise A2AStateTransitionError("Remote response has no result object")
        return cast(dict[str, Any], result)

    def _read_bounded(self, response: Any, deadline: float) -> bytes:
        """Read the body in small chunks under ONE total deadline and the size cap (a slow drip cannot outlive the timeout)."""
        chunks: list[bytes] = []
        total = 0
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise A2ATransportUnavailableError("A2A call exceeded its total deadline")
            try:  # bound the next blocking read by what is left of the deadline
                response.fp.raw._sock.settimeout(remaining)
            except AttributeError:
                pass
            try:
                chunk = response.read1(8192)  # at most ONE raw read: read(n) would block until n bytes arrive
            except TimeoutError as exc:
                raise A2ATransportUnavailableError("A2A call exceeded its total deadline") from exc
            if not chunk:
                return b"".join(chunks)
            total += len(chunk)
            if total > self._max:
                raise A2AStateTransitionError("Remote response exceeds max_response_bytes")
            chunks.append(chunk)

    @staticmethod
    def _to_response(card: AgentCard, task_id: str, result: dict[str, Any], expected_remote_id: str) -> A2ATaskResponse:
        remote_id = result.get("id")
        if remote_id != expected_remote_id:  # the answer must be about the task we asked for
            raise A2AStateTransitionError("Remote answered about a different task than the one requested")
        status = result.get("status")
        raw_state = cast(dict[str, Any], status).get("state") if isinstance(status, dict) else None
        if not isinstance(remote_id, str) or not remote_id or not isinstance(raw_state, str):
            raise A2AStateTransitionError("Remote task lacks an id or a status state")
        if raw_state not in _STATES:
            raise A2AStateTransitionError(f"Remote task state {raw_state!r} has no PeerHub mapping (failing closed)")
        mapped = _STATES[raw_state]
        context = result.get("contextId")
        message = cast(dict[str, Any], status).get("message")
        error = None
        if mapped == "FAILED":
            error = json.dumps(message, sort_keys=True) if message is not None else raw_state
        artifacts = result.get("artifacts")
        output: dict[str, Any] | None = {"artifacts": cast(list[Any], artifacts)} if mapped == "COMPLETED" and isinstance(artifacts, list) else None
        return A2ATaskResponse(task_id, mapped, ExternalExecutionRef(
            card.agent_id, remote_id, str(context) if context else remote_id,
            {"transport": "a2a-jsonrpc-subset", "state": raw_state}), output=output, error=error)

    # --------------------------------------------------------------------- port
    def submit(self, card: AgentCard, request: A2ATaskRequest, timeout_s: float) -> A2ATaskResponse:
        params = {"id": request.task_id,
                  "message": {"role": "user", "parts": [{"kind": "data", "data": {"action": request.action,
                                                                                   "input": request.input_parameters}}]}}
        return self._to_response(card, request.task_id, self._rpc(card, "tasks/send", params, timeout_s), request.task_id)

    def poll(self, card: AgentCard, current: A2ATaskResponse, timeout_s: float) -> A2ATaskResponse | None:
        remote_id = current.external_ref.remote_task_id
        return self._to_response(card, current.task_id, self._rpc(card, "tasks/get", {"id": remote_id}, timeout_s), remote_id)

    def cancel(self, card: AgentCard, current: A2ATaskResponse, timeout_s: float) -> A2ATaskResponse:
        remote_id = current.external_ref.remote_task_id
        return self._to_response(card, current.task_id, self._rpc(card, "tasks/cancel", {"id": remote_id}, timeout_s), remote_id)

    def lookup(self, card: AgentCard, request: A2ATaskRequest, timeout_s: float) -> A2ATaskResponse:
        try:
            return self._to_response(card, request.task_id, self._rpc(card, "tasks/get", {"id": request.task_id}, timeout_s), request.task_id)
        except RemoteRpcError as exc:
            if exc.code == TASK_NOT_FOUND:
                raise A2ATaskUnknownToRemoteError(f"remote has no task {request.task_id!r}") from exc
            raise

