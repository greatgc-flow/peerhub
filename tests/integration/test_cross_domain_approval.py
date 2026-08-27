"""Cross-domain governance approval integration tests."""

from typing import Any

import pytest

from peerhub.governance.tasks import TaskService
from peerhub.governance.consensus import ConsensusService


def _prepare_task(tasks: TaskService, task_id: str, approval_id: str) -> None:
    tasks.create(task_id=task_id, summary="Task", spec="Do the work", creator_id="owner")
    tasks.claim_start(
        task_id,
        actor_id="worker",
        request_id=f"request-{task_id}",
        coordinator="coordinator",
        attempt_id=f"attempt-{task_id}",
    )
    tasks.request_approval(
        task_id,
        requester_id="worker",
        approval_id=approval_id,
        approver_id="approver",
    )


def _resolve_round(consensus: ConsensusService, round_id: str, outcome: str) -> Any:
    consensus.propose(
        round_id=round_id,
        title="Task approval",
        question="Should this task be approved?",
        body="Use the consensus outcome as the approval decision.",
        proposer_id="worker",
        required_participants=("approver", "reviewer"),
        eligible_participants=("approver", "reviewer"),
        risk="normal",
        source_hash="task-approval-test",
    )
    consensus.cast_vote(round_id, actor_id="approver", choice="agree" if outcome == "GRANTED" else "disagree")
    consensus.cast_vote(round_id, actor_id="reviewer", choice="agree" if outcome == "GRANTED" else "disagree")
    consensus.resolve(round_id, outcome, "approver", "task-approval-test")
    target = consensus.get_target(round_id)
    assert target is not None
    return target.state["resolution"]


def _assert_task_approval(runtime_setup: Any, outcome: str, expected_task_state: str) -> None:
    runtime, _, _ = runtime_setup
    task_id = f"task-{outcome.lower()}"
    approval_id = f"approval-{outcome.lower()}"
    _prepare_task(runtime.task_service, task_id, approval_id)
    resolution = _resolve_round(runtime.consensus_service, f"round-{outcome.lower()}", outcome)

    assert resolution["outcome"] == outcome
    if resolution["outcome"] == "GRANTED":
        runtime.task_service.approval_granted(task_id, approval_id=approval_id, decided_by="approver")
    else:
        runtime.task_service.approval_rejected(task_id, approval_id=approval_id, decided_by="approver", reason="Consensus rejected task")

    task = runtime.task_service.get_target(task_id)
    approval = runtime.governance_broker.get_target(f"approval:{approval_id}")
    assert task is not None
    assert approval is not None
    assert task.state["state"] == expected_task_state
    assert approval.state["status"] == outcome
    assert approval.state["decision"]["outcome"] == outcome


def test_task_approval_granted_by_real_consensus(runtime_setup) -> None:
    _assert_task_approval(runtime_setup, "GRANTED", "READY")


def test_task_approval_rejected_by_real_consensus(runtime_setup) -> None:
    _assert_task_approval(runtime_setup, "REJECTED", "FAILED")
