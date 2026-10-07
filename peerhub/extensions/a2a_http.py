"""One bounded external A2A binding (M3.2): JSON-RPC 2.0 over HTTP, `tasks/send`, `tasks/get`, `tasks/cancel`.

Binding profile `a2a-jsonrpc-subset`. It maps the remote protocol to PeerHub's small task states and keeps the remote protocol
state opaque in `ExternalExecutionRef` (an A2A Task is never a PeerHub Work, an A2A message never a Record). Properties:

- https only; plain http is accepted for loopback hosts only (explicit local testing/dev), never for a remote host
- no redirects, a bounded response size, every call honours the adapter's timeout
- the task id is the remote idempotency identity, so `lookup` (`tasks/get`) can reconcile an uncertain submission
- an unmapped remote state (input-required, auth-required, unknown, ...) fails closed instead of being guessed
- credentials are injected by the composition root as headers; they are never part of an Agent Card or a Record

Honest scope: exercised against an in-process JSON-RPC server that implements the same subset. Interoperability with third-party
A2A servers is NOT verified here.
"""

from __future__ import annotations

import ipaddress
import json
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
        body = json.dumps({"jsonrpc": "2.0", "id": uuid.uuid4().hex, "method": method, "params": params}, allow_nan=False).encode("utf-8")
        request = urllib.request.Request(self._endpoint(card), data=body, method="POST",
                                         headers={"Content-Type": "application/json", **self._headers})
        opener = urllib.request.build_opener(_NoRedirect)
        try:
            with opener.open(request, timeout=timeout_s) as response:
                raw = response.read(self._max + 1)
        except urllib.error.HTTPError as exc:  # JSON-RPC errors normally arrive with HTTP 200; any other status is a failure
            raise A2ATransportUnavailableError(f"remote answered HTTP {exc.code}") from exc
        if len(raw) > self._max:
            raise A2AStateTransitionError("Remote response exceeds max_response_bytes")
        try:
            document = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise A2AStateTransitionError("Remote response is not valid JSON") from exc
        if not isinstance(document, dict):
            raise A2AStateTransitionError("Remote response is not a JSON-RPC object")
        doc = cast(dict[str, Any], document)
        if "error" in doc and isinstance(doc["error"], dict):
            err = cast(dict[str, Any], doc["error"])
            raise RemoteRpcError(int(err.get("code", 0)), str(err.get("message", "")))
        result = doc.get("result")
        if not isinstance(result, dict):
            raise A2AStateTransitionError("Remote response has no result object")
        return cast(dict[str, Any], result)

    @staticmethod
    def _to_response(card: AgentCard, task_id: str, result: dict[str, Any]) -> A2ATaskResponse:
        remote_id = result.get("id")
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
        return self._to_response(card, request.task_id, self._rpc(card, "tasks/send", params, timeout_s))

    def poll(self, card: AgentCard, current: A2ATaskResponse, timeout_s: float) -> A2ATaskResponse | None:
        result = self._rpc(card, "tasks/get", {"id": current.external_ref.remote_task_id}, timeout_s)
        return self._to_response(card, current.task_id, result)

    def cancel(self, card: AgentCard, current: A2ATaskResponse, timeout_s: float) -> A2ATaskResponse:
        result = self._rpc(card, "tasks/cancel", {"id": current.external_ref.remote_task_id}, timeout_s)
        return self._to_response(card, current.task_id, result)

    def lookup(self, card: AgentCard, request: A2ATaskRequest, timeout_s: float) -> A2ATaskResponse:
        try:
            return self._to_response(card, request.task_id, self._rpc(card, "tasks/get", {"id": request.task_id}, timeout_s))
        except RemoteRpcError as exc:
            if exc.code == TASK_NOT_FOUND:
                raise A2ATaskUnknownToRemoteError(f"remote has no task {request.task_id!r}") from exc
            raise

