from __future__ import annotations

from pathlib import Path

from fakes import FakeClock, FakeIdSource
import pytest
from peerhub.core.errors import InvalidMutationError, StaleRevisionError
from peerhub.governance.broker import GovernanceBroker
from peerhub.governance.tasks import TaskService
from peerhub.persistence.sqlite import SqliteStateStore


def _service(tmp_path: Path) -> tuple[TaskService, GovernanceBroker]:
    store = SqliteStateStore(tmp_path / "tasks.sqlite3", workspace_home_id="tasks-test")
    store.initialize()
    broker = GovernanceBroker(
        store,
        clock=FakeClock(range(1, 100)),
        ids=FakeIdSource([f"id-{i}" for i in range(1, 200)]),
    )
    return (
        TaskService(
            broker,
            clock=FakeClock(range(1, 100)),
            ids=FakeIdSource([f"domain-{i}" for i in range(1, 200)]),
        ),
        broker,
    )


def test_create_task_builds_initial_snapshot(tmp_path: Path) -> None:
    service, broker = _service(tmp_path)
    service.create(
        task_id="task-01",
        summary="Build feature",
        spec="Implement it",
        creator_id="peer-a",
        room_id="room-01",
    )

    target = broker.get_target("task-01")
    assert target is not None
    assert target.revision == 1
    assert target.state["schema"] == "peerhub.task-state/v1"
    assert target.state["kind"] == "task"
    assert target.state["scope"] == "room-01"
    assert target.state["state"] == "CREATED"
    assert target.state["checkpoint"] is None
    assert target.state["child_request_ids"] == ()


def test_claim_start_records_executor_and_running_state(tmp_path: Path) -> None:
    service, broker = _service(tmp_path)
    service.create(
        task_id="task-01",
        summary="Build feature",
        spec="Implement it",
        creator_id="peer-a",
    )

    service.claim_start(
        "task-01",
        actor_id="peer-a",
        request_id="request-01",
        coordinator="peer-a",
        attempt_id="attempt-01",
    )

    state = broker.get_target("task-01").state
    assert state["state"] == "RUNNING"
    assert state["executor"]["active_request_id"] == "request-01"
    assert state["executor"]["coordinator"] == "peer-a"
    assert state["active_attempt_id"] == "attempt-01"
    assert state["timestamps"]["started_at"] is not None


def test_checkpoint_persists_digest_and_transitions_task(tmp_path: Path) -> None:
    service, broker = _service(tmp_path)
    service.create(
        task_id="task-01",
        summary="Build feature",
        spec="Implement it",
        creator_id="peer-a",
    )
    service.claim_start(
        "task-01",
        actor_id="peer-a",
        request_id="request-01",
        coordinator="peer-a",
        attempt_id="attempt-01",
    )

    service.checkpoint(
        "task-01",
        actor_id="peer-a",
        checkpoint_id="checkpoint-01",
        stage="implementation",
        request_id="request-01",
        attempt_id="attempt-01",
        resume_token_ref="artifact://resume-01",
        completed_units=("unit-1",),
        remaining_units=("unit-2",),
    )

    state = broker.get_target("task-01").state
    checkpoint = state["checkpoint"]
    assert state["state"] == "CHECKPOINTED"
    assert checkpoint["checkpoint_id"] == "checkpoint-01"
    assert checkpoint["state_digest"].startswith("sha256:")
    assert checkpoint["completed_units"] == ("unit-1",)
    assert checkpoint["remaining_units"] == ("unit-2",)


def _running(service: TaskService, task_id: str = "task-01") -> None:
    service.create(task_id=task_id, summary="S", spec="X", creator_id="peer-a")
    service.claim_start(task_id, actor_id="peer-a", request_id="request-01", coordinator="peer-a", attempt_id="attempt-01")


def test_request_and_grant_approval_use_separate_target(tmp_path: Path) -> None:
    service, broker = _service(tmp_path)
    _running(service)
    service.request_approval("task-01", requester_id="peer-a", approval_id="approval-01", approver_id="human:alice")
    assert broker.get_target("approval:approval-01").state["status"] == "PENDING"
    service.approval_granted("task-01", approval_id="approval-01", decided_by="human:alice")
    assert broker.get_target("approval:approval-01").state["status"] == "GRANTED"
    assert broker.get_target("task-01").state["state"] == "READY"


def test_rejected_approval_fails_task_and_failover_unbinds(tmp_path: Path) -> None:
    service, broker = _service(tmp_path)
    _running(service, "task-reject")
    service.request_approval("task-reject", requester_id="peer-a", approval_id="approval-r", approver_id="human:alice")
    service.approval_rejected("task-reject", approval_id="approval-r", decided_by="human:alice", reason="Denied")
    assert broker.get_target("task-reject").state["state"] == "FAILED"

    _running(service, "task-failover")
    service.request_failover("task-failover", to_actor_id="peer-b", reason="peer unavailable")
    state = broker.get_target("task-failover").state
    assert state["state"] == "FAILOVER_PENDING"
    assert state["failover"]["count"] == 1
    assert state["executor"]["binding_state"] == "UNBOUND"


def test_complete_cancel_fail_are_terminal_and_cas_checked(tmp_path: Path) -> None:
    service, broker = _service(tmp_path)
    _running(service, "task-complete")
    service.complete("task-complete", actor_id="peer-a")
    assert broker.get_target("task-complete").state["state"] == "SUCCEEDED"
    with pytest.raises(InvalidMutationError):
        service.cancel("task-complete", actor_id="peer-a", reason="late")

    _running(service, "task-fail")
    with pytest.raises(StaleRevisionError):
        service.fail("task-fail", actor_id="peer-a", failure_class="error", reason="bad", expected_revision=0)

    _running(service, "task-cancel")
    service.cancel("task-cancel", actor_id="peer-a", reason="withdrawn")
    assert broker.get_target("task-cancel").state["state"] == "CANCELLED"
