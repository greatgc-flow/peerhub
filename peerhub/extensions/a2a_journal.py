"""Durable A2A task evidence as ordered Records (M3.2): the Record stream is the authority, adapter memory is a cache.

Kinds, written through the public Core port into a dedicated single-author Stream:

- `m3.a2a.submitting`  BEFORE any remote side effect: task id, request binding, endpoint, attempt number and a unique CLAIM token
- `m3.a2a.observed`    a validated remote response for the current attempt (each status once; terminal evidence is immutable)
- `m3.a2a.not_started` the remote definitively reported that it never saw THIS attempt (a retry may follow as a NEW attempt)
- `m3.a2a.abandoned`   an operator decision to give up an unresolved id; the id is burned and never reused

Replay rule: a `submitting` record with no later `observed`/`not_started`/`abandoned` for the same attempt means the outcome is UNKNOWN.
It is reported as uncertain and is never replayed blindly; only reconciliation against the remote (or an explicit abandon) resolves it.
Submission ownership is claimed atomically: two adapters over the same journal cannot both send the same task.
"""

from __future__ import annotations

import dataclasses
import datetime
import hashlib
import json
import uuid
from typing import Any, cast

from peerhub.core.store import IdempotencyConflictError
from peerhub.extensions.a2a import A2AStateTransitionError, A2ATaskRequest, A2ATaskResponse, ExternalExecutionRef

_TERMINAL = ("COMPLETED", "FAILED", "CANCELLED")
_RANK = {"SUBMITTED": 0, "RUNNING": 1, "COMPLETED": 2, "FAILED": 2, "CANCELLED": 2}


class A2AJournalError(Exception):
    """The A2A evidence stream is not a trustworthy single-author stream, or evidence does not match the recorded attempt."""


class A2ASubmissionExistsError(A2AJournalError):
    """The task already has durable submission evidence: adopt it instead of sending again."""

    def __init__(self, state: "A2ATaskState") -> None:
        super().__init__("a submission for this task id already exists in the journal")
        self.state = state


class A2AClaimLostError(A2AJournalError):
    """Another adapter claimed this submission attempt first: this one must not send."""


@dataclasses.dataclass
class A2ATaskState:
    request: A2ATaskRequest
    binding: str
    attempt: int
    endpoint: str
    response: A2ATaskResponse | None = None
    uncertain: bool = True
    abandoned: bool = False


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
        """Append one Record; False when the same idempotency key already holds a DIFFERENT or identical record."""
        self._check_owner()
        try:
            self.store.append_record(
                stream_id=self.stream_id, author_peer_id=self.author_peer_id, kind=f"m3.a2a.{kind}", body=body,
                idempotency_key="a2a:" + hashlib.sha256(key.encode("utf-8")).hexdigest(),
                created_at=datetime.datetime.now(datetime.timezone.utc).isoformat())
            return True
        except IdempotencyConflictError:
            return False

    # ------------------------------------------------------------------------------------------------ replay
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
                state = tasks.get(task_id)
                if record.kind == "m3.a2a.submitting":
                    if state is not None and state.abandoned:
                        continue  # a burned id never comes back
                    tasks[task_id] = A2ATaskState(A2ATaskRequest(**json.loads(body["binding"])), body["binding"],
                                                  int(body["attempt"]), str(body.get("endpoint", "")))
                elif state is None or int(body.get("attempt", -1)) != state.attempt:
                    continue  # evidence for any attempt other than the current one is stale and ignored
                elif record.kind == "m3.a2a.observed":
                    evidence = body["response"]
                    new = A2ATaskResponse(evidence["task_id"], evidence["status"], ExternalExecutionRef.from_dict(evidence["external_ref"]),
                                          evidence.get("output"), evidence.get("error"), evidence["updated_at"])
                    if state.response is None or (state.response.status not in _TERMINAL and _RANK[new.status] > _RANK[state.response.status]):
                        state.response, state.uncertain = new, False  # terminal evidence is immutable; status only moves forward
                elif record.kind == "m3.a2a.not_started" and state.response is None:
                    del tasks[task_id]  # the remote never saw this attempt: no binding remains, a new attempt may be made
                elif record.kind == "m3.a2a.abandoned" and state.response is None:
                    state.abandoned, state.uncertain = True, False
        return tasks

    def state_of(self, task_id: str) -> A2ATaskState | None:
        return self.load().get(task_id)

    def _attempts(self, task_id: str) -> int:
        self._check_owner()
        n, position = 0, 0
        while page := self.store.read_records(self.stream_id, after_position=position, limit=256):
            for record in page:
                position = record.position
                body = record.body
                if record.kind == "m3.a2a.submitting" and isinstance(body, dict) and cast(dict[str, Any], body).get("task_id") == task_id:
                    n += 1
        return n

    def attempts(self, task_id: str) -> int:
        """How many submission attempts were ever recorded for this task (each retry after not_started is a new attempt)."""
        return self._attempts(task_id)

    # ----------------------------------------------------------------------------------------------- writers
    def claim_submission(self, request: A2ATaskRequest, endpoint: str) -> int:
        """Atomically take ownership of the next submission attempt; returns the attempt number.

        Raises A2ASubmissionExistsError when the task already has evidence (adopt it, do not send) and A2AClaimLostError when
        another adapter claimed the same attempt between this check and the append."""
        existing = self.state_of(request.task_id)
        if existing is not None:
            raise A2ASubmissionExistsError(existing)
        attempt = self._attempts(request.task_id) + 1
        body = {"task_id": request.task_id, "binding": request_binding(request), "attempt": attempt, "endpoint": endpoint,
                "claim": uuid.uuid4().hex}  # a unique claim makes a same-attempt race a key CONFLICT, never a silent duplicate
        if not self._append("submitting", f"{request.task_id}:submitting:{attempt}", body):
            raise A2AClaimLostError(f"submission attempt {attempt} of {request.task_id!r} was claimed by another adapter")
        return attempt

    def record_submitting(self, request: A2ATaskRequest, endpoint: str = "") -> int:
        return self.claim_submission(request, endpoint)

    def record_observed(self, response: A2ATaskResponse, attempt: int | None = None) -> None:
        state = self.state_of(response.task_id)
        if state is None or state.abandoned or (attempt is not None and attempt != state.attempt):
            raise A2AJournalError(f"no current submission attempt for {response.task_id!r} to attach this evidence to")
        current = state.response
        if current is not None:
            if current.status in _TERMINAL:
                if (current.status, current.output, current.error) == (response.status, response.output, response.error):
                    return  # the same terminal evidence again: idempotent
                raise A2AStateTransitionError(f"Task {response.task_id!r} is already {current.status}; terminal evidence is immutable")
            if _RANK[response.status] < _RANK[current.status] or (response.status == current.status and (response.output, response.error)
                                                                    != (current.output, current.error)):
                raise A2AStateTransitionError(f"Task {response.task_id!r}: {current.status} -> {response.status} is not a forward move")
            if response.status == current.status:
                return
        if not self._append("observed", f"{response.task_id}:{state.attempt}:status:{response.status}",
                            {"task_id": response.task_id, "attempt": state.attempt, "response": response.to_record_evidence()}):
            again = self.state_of(response.task_id)
            if again is None or again.response is None or again.response.status != response.status:
                raise A2AStateTransitionError(f"Conflicting evidence for {response.task_id!r} status {response.status}")

    def record_not_started(self, task_id: str, attempt: int) -> None:
        self._append("not_started", f"{task_id}:not_started:{attempt}", {"task_id": task_id, "attempt": attempt})

    def record_abandoned(self, task_id: str, attempt: int, reason: str) -> None:
        self._append("abandoned", f"{task_id}:abandoned:{attempt}", {"task_id": task_id, "attempt": attempt, "reason": reason})
