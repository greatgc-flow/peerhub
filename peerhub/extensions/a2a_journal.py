"""Durable A2A task evidence as ordered Records (M3.2): the Record stream is the authority, adapter memory is a cache.

Three kinds, written through the public Core port into a dedicated single-author Stream:

- `m3.a2a.submitting`  BEFORE any remote side effect (task id, request binding, attempt number)
- `m3.a2a.observed`    the validated remote response for a task (each status once)
- `m3.a2a.not_started` the remote definitively reported that it never saw the task (a retry may follow, as a NEW attempt)

Replay rule: a `submitting` record with no later `observed`/`not_started` for that attempt means the outcome is UNKNOWN. It is
reported as uncertain and is never replayed blindly; only reconciliation against the remote can resolve it.
"""

from __future__ import annotations

import dataclasses
import datetime
import hashlib
import json
from typing import Any, cast

from peerhub.core.store import IdempotencyConflictError
from peerhub.extensions.a2a import A2ATaskRequest, A2ATaskResponse, ExternalExecutionRef


class A2AJournalError(Exception):
    """The A2A evidence stream is not a trustworthy single-author stream."""


@dataclasses.dataclass
class A2ATaskState:
    request: A2ATaskRequest
    binding: str
    attempt: int
    response: A2ATaskResponse | None = None
    uncertain: bool = True


def request_binding(request: A2ATaskRequest) -> str:
    return json.dumps(dataclasses.asdict(request), sort_keys=True, allow_nan=False)


class A2ATaskJournal:
    def __init__(self, store: Any, stream_id: str, author_peer_id: str) -> None:
        self.store = store
        self.stream_id = stream_id
        self.author_peer_id = author_peer_id

    def _check_owner(self) -> None:
        if self.store.get_stream(self.stream_id).members != [self.author_peer_id]:
            raise A2AJournalError("A2A evidence requires a dedicated single-author stream.")

    def _append(self, kind: str, key: str, body: dict[str, Any]) -> bool:
        """Append one Record; returns False when the identical-key record already exists (idempotent no-op)."""
        self._check_owner()
        try:
            self.store.append_record(
                stream_id=self.stream_id, author_peer_id=self.author_peer_id, kind=f"m3.a2a.{kind}", body=body,
                idempotency_key="a2a:" + hashlib.sha256(key.encode("utf-8")).hexdigest(),
                created_at=datetime.datetime.now(datetime.timezone.utc).isoformat())
            return True
        except IdempotencyConflictError:
            return False

    def load(self) -> dict[str, A2ATaskState]:
        self._check_owner()
        tasks: dict[str, A2ATaskState] = {}
        position = 0
        while page := self.store.read_records(self.stream_id, after_position=position, limit=256):
            for record in page:
                position = record.position
                if not record.kind.startswith("m3.a2a.") or not isinstance(record.body, dict):
                    continue
                if record.author_peer_id != self.author_peer_id:
                    raise A2AJournalError("A2A evidence has a foreign authority.")
                body = cast(dict[str, Any], record.body)
                task_id = str(body.get("task_id", ""))
                if record.kind == "m3.a2a.submitting":
                    request = A2ATaskRequest(**json.loads(body["binding"]))
                    tasks[task_id] = A2ATaskState(request, body["binding"], int(body["attempt"]))
                elif record.kind == "m3.a2a.observed" and task_id in tasks:
                    evidence = body["response"]
                    tasks[task_id].response = A2ATaskResponse(
                        evidence["task_id"], evidence["status"], ExternalExecutionRef.from_dict(evidence["external_ref"]),
                        evidence.get("output"), evidence.get("error"), evidence["updated_at"])
                    tasks[task_id].uncertain = False
                elif record.kind == "m3.a2a.not_started" and task_id in tasks and tasks[task_id].response is None:
                    del tasks[task_id]  # the remote never saw it: no binding remains, a new attempt may be made
        return tasks

    def attempts(self, task_id: str) -> int:
        """How many submission attempts were ever recorded for this task (each retry after not_started is a new attempt)."""
        self._check_owner()
        n, position = 0, 0
        while page := self.store.read_records(self.stream_id, after_position=position, limit=256):
            for record in page:
                position = record.position
                body = record.body
                if record.kind == "m3.a2a.submitting" and isinstance(body, dict) and cast(dict[str, Any], body).get("task_id") == task_id:
                    n += 1
        return n

    def record_submitting(self, request: A2ATaskRequest) -> int:
        attempt = self.attempts(request.task_id) + 1
        self._append("submitting", f"{request.task_id}:submitting:{attempt}",
                     {"task_id": request.task_id, "binding": request_binding(request), "attempt": attempt})
        return attempt

    def record_observed(self, response: A2ATaskResponse) -> None:
        self._append("observed", f"{response.task_id}:status:{response.status}",
                     {"task_id": response.task_id, "response": response.to_record_evidence()})

    def record_not_started(self, task_id: str, attempt: int) -> None:
        self._append("not_started", f"{task_id}:not_started:{attempt}", {"task_id": task_id, "attempt": attempt})
