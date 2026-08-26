from __future__ import annotations

from pathlib import Path

from fakes import FakeClock, FakeIdSource
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

