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

    def get_target(self, task_id: str) -> TargetState | None:
        return self._broker.get_target(task_id)

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

    def request_approval(
        self, task_id: str, *, requester_id: str, approval_id: str,
        approver_id: str,
    ) -> MutationSubmission:
        target, state = self._load(task_id, {"RUNNING", "CHECKPOINTED"})
        timestamp = self._clock.now()
        approval_state: dict[str, JsonValue] = {
            "schema": "peerhub.governance.approval-request-state/v1",
            "kind": "governance.approval.request",
            "scope": None,
            "approval_id": approval_id,
            "task_id": task_id,
            "status": "PENDING",
            "requested_by": {"actor_id": requester_id, "requested_at": timestamp},
            "approver_id": approver_id,
            "decision": None,
        }
        self._submit(
            f"approval:{approval_id}", 0, requester_id, "task.request_approval", approval_state
        )
        approval = dict(cast(dict[str, JsonValue], state["approval"]))
        approval.update({"active_request_target_id": f"approval:{approval_id}", "required": True})
        state["approval"] = approval
        state["state"] = "AWAITING_APPROVAL"
        return self._submit(task_id, target.revision, requester_id, "task.request_approval", state)

    def approval_granted(
        self, task_id: str, *, approval_id: str, decided_by: str,
        expected_revision: int | None = None,
    ) -> MutationSubmission:
        target, state, approval_target, approval = self._approval(task_id, approval_id)
        timestamp = self._clock.now()
        approval["status"] = "GRANTED"
        approval["decision"] = {"decided_by": decided_by, "decided_at": timestamp, "outcome": "GRANTED"}
        self._submit(f"approval:{approval_id}", approval_target.revision, decided_by, "task.approval_granted", approval)
        task_approval = dict(cast(dict[str, JsonValue], state["approval"]))
        task_approval.update({"active_request_target_id": None, "required": False})
        state["approval"] = task_approval
        state["state"] = "READY"
        return self._submit(task_id, target.revision if expected_revision is None else expected_revision, decided_by, "task.approval_granted", state)

    def approval_rejected(
        self, task_id: str, *, approval_id: str, decided_by: str, reason: str,
        expected_revision: int | None = None,
    ) -> MutationSubmission:
        target, state, approval_target, approval = self._approval(task_id, approval_id)
        timestamp = self._clock.now()
        approval["status"] = "REJECTED"
        approval["decision"] = {"decided_by": decided_by, "decided_at": timestamp, "outcome": "REJECTED", "reason": reason}
        self._submit(f"approval:{approval_id}", approval_target.revision, decided_by, "task.approval_rejected", approval)
        task_approval = dict(cast(dict[str, JsonValue], state["approval"]))
        task_approval.update({"active_request_target_id": None, "required": False})
        state["approval"] = task_approval
        state["state"] = "FAILED"
        timestamps = dict(cast(dict[str, JsonValue], state["timestamps"]))
        timestamps.update({"completed_at": timestamp, "updated_at": timestamp})
        state["timestamps"] = timestamps
        return self._submit(task_id, target.revision if expected_revision is None else expected_revision, decided_by, "task.approval_rejected", state)

    def request_failover(self, task_id: str, *, to_actor_id: str, reason: str, expected_revision: int | None = None) -> MutationSubmission:
        target, state = self._load(task_id, {"RUNNING", "CHECKPOINTED"})
        timestamp = self._clock.now()
        failover = dict(cast(dict[str, JsonValue], state["failover"]))
        count = failover.get("count", 0)
        if not isinstance(count, int) or isinstance(count, bool):
            raise InvalidMutationError("invalid failover.count")
        failover.update({"count": count + 1, "last_failover_id": self._ids.new_id("failover"), "last_failover_reason": reason, "last_failover_at": timestamp})
        executor = dict(cast(dict[str, JsonValue], state["executor"]))
        executor["binding_state"] = "UNBOUND"
        state.update({"failover": failover, "executor": executor, "state": "FAILOVER_PENDING"})
        return self._submit(task_id, target.revision if expected_revision is None else expected_revision, to_actor_id, "task.request_failover", state)

    def complete(self, task_id: str, *, actor_id: str, expected_revision: int | None = None) -> MutationSubmission:
        return self._terminal(task_id, actor_id, "SUCCEEDED", "task.complete", expected_revision)

    def fail(self, task_id: str, *, actor_id: str, failure_class: str, reason: str, expected_revision: int | None = None) -> MutationSubmission:
        target, state = self._load(task_id, {"CREATED", "READY", "RUNNING", "CHECKPOINTED", "FAILOVER_PENDING", "AWAITING_APPROVAL"})
        timestamp = self._clock.now()
        failure = dict(cast(dict[str, JsonValue], state["failure"]))
        count = failure.get("count", 0)
        if not isinstance(count, int) or isinstance(count, bool):
            raise InvalidMutationError("invalid failure.count")
        failure.update({"count": count + 1, "last_failure_id": self._ids.new_id("failure"), "last_failure_class": failure_class, "last_failure_reason": reason, "last_failure_at": timestamp})
        state["failure"] = failure
        return self._terminal_state(target, state, actor_id, "FAILED", "task.fail", expected_revision, timestamp)

    def cancel(self, task_id: str, *, actor_id: str, reason: str, expected_revision: int | None = None) -> MutationSubmission:
        target, state = self._load(task_id, {"CREATED", "READY", "RUNNING", "CHECKPOINTED", "FAILOVER_PENDING", "AWAITING_APPROVAL"})
        state["cancel_reason"] = reason
        return self._terminal_state(target, state, actor_id, "CANCELLED", "task.cancel", expected_revision, self._clock.now())

    def _terminal(self, task_id: str, actor_id: str, state_name: str, operation: str, expected_revision: int | None) -> MutationSubmission:
        target, state = self._load(task_id, {"RUNNING", "CHECKPOINTED"})
        return self._terminal_state(target, state, actor_id, state_name, operation, expected_revision, self._clock.now())

    def _terminal_state(self, target: TargetState, state: dict[str, JsonValue], actor_id: str, state_name: str, operation: str, expected_revision: int | None, timestamp: int) -> MutationSubmission:
        timestamps = dict(cast(dict[str, JsonValue], state["timestamps"]))
        timestamps.update({"completed_at": timestamp, "updated_at": timestamp})
        state.update({"state": state_name, "active_attempt_id": None, "timestamps": timestamps})
        return self._submit(target.target_id, target.revision if expected_revision is None else expected_revision, actor_id, operation, state)

    def _approval(self, task_id: str, approval_id: str) -> tuple[TargetState, dict[str, JsonValue], TargetState, dict[str, JsonValue]]:
        target, state = self._load(task_id, {"AWAITING_APPROVAL"})
        approval_target = self._broker.get_target(f"approval:{approval_id}")
        if approval_target is None:
            raise RecordNotFoundError("approval", approval_id)
        approval = dict(approval_target.state)
        if approval.get("status") != "PENDING":
            raise InvalidMutationError("approval is not pending")
        return target, state, approval_target, approval

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
