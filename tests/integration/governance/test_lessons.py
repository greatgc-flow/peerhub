from __future__ import annotations

from pathlib import Path

import pytest

from fakes import FakeClock, FakeIdSource
from peerhub.core.errors import InvalidMutationError
from peerhub.governance.broker import GovernanceBroker
from peerhub.governance.lessons import LessonService
from peerhub.persistence.sqlite import SqliteStateStore


def _service(tmp_path: Path) -> tuple[LessonService, GovernanceBroker]:
    store = SqliteStateStore(tmp_path / "lessons.sqlite3", workspace_home_id="lessons-test")
    store.initialize()
    broker = GovernanceBroker(
        store,
        clock=FakeClock(range(1, 100)),
        ids=FakeIdSource([f"id-{i}" for i in range(1, 200)]),
    )
    return (
        LessonService(
            broker,
            clock=FakeClock(range(1, 100)),
            ids=FakeIdSource([f"domain-{i}" for i in range(1, 200)]),
        ),
        broker,
    )


def test_propose_creates_lesson_envelope(tmp_path: Path) -> None:
    service, broker = _service(tmp_path)
    service.propose(
        lesson_id="LL-01",
        title="Use live evidence",
        rule="Measure before claiming",
        category="runtime-reality",
        severity="HIGH",
        proposer_id="cx",
        affected_peers=("cc", "cx", "ag"),
    )

    target = broker.get_target("lesson:LL-01")
    assert target is not None
    assert target.revision == 1
    assert target.state["schema"] == "peerhub.lesson.v1"
    assert target.state["kind"] == "lesson"
    assert target.state["lifecycle"] == "PROPOSED"
    assert target.state["approval"] is None
    assert target.state["affected_peers"] == ("cc", "cx", "ag")


def test_approve_populates_authority_and_hash(tmp_path: Path) -> None:
    service, broker = _service(tmp_path)
    service.propose(
        lesson_id="LL-01",
        title="Use live evidence",
        rule="Measure before claiming",
        category="runtime-reality",
        severity="HIGH",
        proposer_id="cx",
        affected_peers=(),
    )

    service.approve(
        "LL-01",
        approved_by_actor_id="human:alice",
        authority_target_id="consensus-round:01",
    )

    approval = broker.get_target("lesson:LL-01").state["approval"]
    assert approval["approved_by"][0]["actor_id"] == "human:alice"
    assert approval["authority"]["target_id"] == "consensus-round:01"
    assert approval["authority"]["resolution_sha256"].startswith("sha256:")


def test_activate_requires_approval_and_sets_active(tmp_path: Path) -> None:
    service, broker = _service(tmp_path)
    service.propose(
        lesson_id="LL-01",
        title="Use live evidence",
        rule="Measure before claiming",
        category="runtime-reality",
        severity="HIGH",
        proposer_id="cx",
        affected_peers=(),
    )
    with pytest.raises(InvalidMutationError, match="approval"):
        service.activate("LL-01", actor_id="cx")

    service.approve("LL-01", approved_by_actor_id="human:alice")
    service.activate("LL-01", actor_id="cx")
    assert broker.get_target("lesson:LL-01").state["lifecycle"] == "ACTIVE"

