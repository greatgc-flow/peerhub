from __future__ import annotations

from pathlib import Path

from fakes import FakeClock, FakeIdSource
from peerhub.governance.activity import (
    list_active_consensus_rounds,
    list_active_lessons,
    list_active_tasks,
)
from peerhub.governance.broker import GovernanceBroker
from peerhub.governance.consensus import ConsensusService
from peerhub.governance.lessons import LessonService
from peerhub.governance.tasks import TaskService
from peerhub.persistence.sqlite import SqliteStateStore


def _services(tmp_path: Path) -> tuple[GovernanceBroker, ConsensusService, TaskService, LessonService]:
    store = SqliteStateStore(tmp_path / "activity.sqlite3", workspace_home_id="activity-test")
    store.initialize()
    broker = GovernanceBroker(
        store,
        clock=FakeClock(range(1, 300)),
        ids=FakeIdSource([f"id-{i}" for i in range(1, 500)]),
    )
    return (
        broker,
        ConsensusService(broker, clock=FakeClock(range(1, 300)), ids=FakeIdSource([f"c-{i}" for i in range(1, 500)])),
        TaskService(broker, clock=FakeClock(range(1, 300)), ids=FakeIdSource([f"t-{i}" for i in range(1, 500)])),
        LessonService(broker, clock=FakeClock(range(1, 300)), ids=FakeIdSource([f"l-{i}" for i in range(1, 500)])),
    )


def test_active_consensus_rounds_filter_open_and_scope(tmp_path: Path) -> None:
    broker, consensus, _, _ = _services(tmp_path)
    consensus.propose(
        round_id="round-open",
        title="T",
        question="Q",
        body="B",
        proposer_id="peer-a",
        required_participants=("peer-a",),
        eligible_participants=("peer-a",),
        risk="normal",
        source_hash="sha256:test",
    )
    consensus.propose(
        round_id="round-abandoned",
        title="T",
        question="Q",
        body="B",
        proposer_id="peer-a",
        required_participants=("peer-a",),
        eligible_participants=("peer-a",),
        risk="normal",
        source_hash="sha256:test",
    )
    consensus.abandon("round-abandoned", "obsolete", "No longer needed", "peer-a")

    assert tuple(t.target_id for t in list_active_consensus_rounds(broker)) == ("round-open",)


def test_active_tasks_returns_nonterminal_tasks(tmp_path: Path) -> None:
    broker, _, tasks, _ = _services(tmp_path)
    tasks.create(task_id="task-created", summary="S", spec="X", creator_id="peer-a", room_id="room-1")
    tasks.create(task_id="task-running", summary="S", spec="X", creator_id="peer-a", room_id="room-1")
    tasks.claim_start("task-running", actor_id="peer-a", request_id="request-1", coordinator="peer-a", attempt_id="attempt-1")

    active = list_active_tasks(broker, "room-1")
    assert tuple(t.target_id for t in active) == ("task-created", "task-running")


def test_active_lessons_filters_lifecycle_and_handles_object_scope(tmp_path: Path) -> None:
    broker, _, _, lessons = _services(tmp_path)
    lessons.propose(lesson_id="proposed", title="T", rule="R", category="C", severity="LOW", proposer_id="cx", affected_peers=())
    lessons.propose(lesson_id="active", title="T", rule="R", category="C", severity="LOW", proposer_id="cx", affected_peers=())
    lessons.approve("active", approved_by_actor_id="human:alice")
    lessons.activate("active", actor_id="cx")

    assert tuple(t.target_id for t in list_active_lessons(broker)) == ("lesson:active",)

