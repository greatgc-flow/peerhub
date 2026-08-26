"""Task lifecycle operations over governed TargetState records."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from typing import cast

from peerhub.core.context import Clock, IdSource
from peerhub.core.errors import InvalidMutationError, RecordNotFoundError
from peerhub.core.protocol import CommandID, JsonValue

from .broker import GovernanceBroker
from .contract import EffectIntent, MutationRequest, MutationSubmission, TargetState


class TaskService:
    """Create tasks and advance their initial execution lifecycle."""

    def __init__(self, broker: GovernanceBroker, *, clock: Clock, ids: IdSource) -> None:
        self._broker = broker
        self._clock = clock
        self._ids = ids

    def create(
        self,
        *,
        task_id: str,
        summary: str,
        spec: str,
        creator_id: str,
        room_id: str | None = None,
        current_stage: str = "initial",
    ) -> MutationSubmission:
        timestamp = self._clock.now()
        state: dict[str, JsonValue] = {
            "schema": "peerhub.task-state/v1",
            "kind": "task",
            "scope": room_id,
            "task_id": task_id,
            "objective": {"summary": summary, "spec": spec},
            "current_stage": current_stage,
            "state": "CREATED",
            "executor": {
                "binding_state": "UNBOUND",
                "coordinator": None,
                "session_lease_id": None,
                "capability_lease_id": None,
                "route_decision_id": None,
                "active_request_id": None,
            },
            "checkpoint": None,
            "child_request_ids": (),
            "active_attempt_id": None,
            "failure": {
                "count": 0,
                "last_failure_id": None,
                "last_failure_class": None,
                "last_failure_at": None,
            },
            "failover": {
                "count": 0,
                "last_failover_id": None,
                "last_failover_reason": None,
                "last_failover_at": None,
            },
            "approval": {"active_request_target_id": None, "required": False},
            "timestamps": {
                "created_at": timestamp,
                "started_at": None,
                "completed_at": None,
                "updated_at": timestamp,
            },
            "created_by": creator_id,
        }
        return self._submit(task_id, 0, creator_id, "task.create", state)

    def claim_start(
        self,
        task_id: str,
        *,
        actor_id: str,
        request_id: str,
        coordinator: str,
        attempt_id: str,
        expected_revision: int | None = None,
    ) -> MutationSubmission:
        target, state = self._load(task_id, {"CREATED", "READY"})
        timestamps = dict(cast(dict[str, JsonValue], state["timestamps"]))
        timestamp = self._clock.now()
        if timestamps.get("started_at") is None:
            timestamps["started_at"] = timestamp
        timestamps["updated_at"] = timestamp
        executor = dict(cast(dict[str, JsonValue], state["executor"]))
        executor.update(
            {
                "binding_state": "BOUND",
                "coordinator": coordinator,
                "active_request_id": request_id,
            }
        )
        state["executor"] = executor
        state["active_attempt_id"] = attempt_id
        state["state"] = "RUNNING"
        state["timestamps"] = timestamps
        return self._submit(
            task_id,
            target.revision if expected_revision is None else expected_revision,
            actor_id,
            "task.claim_start",
            state,
        )

    def checkpoint(
        self,
        task_id: str,
        *,
        actor_id: str,
        checkpoint_id: str,
        stage: str,
        request_id: str,
        attempt_id: str,
        resume_token_ref: str | None,
        completed_units: Sequence[str],
        remaining_units: Sequence[str],
        expected_revision: int | None = None,
    ) -> MutationSubmission:
        target, state = self._load(task_id, {"RUNNING"})
        timestamp = self._clock.now()
        payload = {
            "task_id": task_id,
            "stage": stage,
            "request_id": request_id,
            "attempt_id": attempt_id,
            "completed_units": tuple(completed_units),
            "remaining_units": tuple(remaining_units),
        }
        digest = "sha256:" + hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        state["checkpoint"] = {
            "checkpoint_id": checkpoint_id,
            "stage": stage,
            "request_id": request_id,
            "attempt_id": attempt_id,
            "captured_at": timestamp,
            "resume_token_ref": resume_token_ref,
            "state_digest": digest,
            "completed_units": tuple(completed_units),
            "remaining_units": tuple(remaining_units),
        }
        state["state"] = "CHECKPOINTED"
        timestamps = dict(cast(dict[str, JsonValue], state["timestamps"]))
        timestamps["updated_at"] = timestamp
        state["timestamps"] = timestamps
        return self._submit(
            task_id,
            target.revision if expected_revision is None else expected_revision,
            actor_id,
            "task.checkpoint",
            state,
        )

    def _load(
        self, task_id: str, allowed_states: set[str]
    ) -> tuple[TargetState, dict[str, JsonValue]]:
        target = self._broker.get_target(task_id)
        if target is None:
            raise RecordNotFoundError("task", task_id)
        state = dict(target.state)
        current = state.get("state")
        if current not in allowed_states:
            raise InvalidMutationError("operation is not valid in the current task state")
        return target, state

    def _submit(
        self,
        target_id: str,
        expected_revision: int,
        actor_id: str,
        operation: str,
        desired_state: dict[str, JsonValue],
    ) -> MutationSubmission:
        request_id = self._ids.new_id("tasks-request")
        return self._broker.submit(
            MutationRequest(
                request_id=request_id,
                command_id=CommandID(self._ids.new_id("tasks-command")),
                correlation_id=self._ids.new_id("tasks-correlation"),
                client_id="peerhub.tasks",
                command_type=operation,
                idempotency_key=request_id,
                actor_id=actor_id,
                policy_revision="protocol-v2",
                target_id=target_id,
                expected_revision=expected_revision,
                operation=operation,
                desired_state=desired_state,
                effect_intent=EffectIntent(kind="tasks.noop", payload={}),
            )
        )
