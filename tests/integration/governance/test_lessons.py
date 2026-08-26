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


def _active(service: LessonService, lesson_id: str) -> None:
    service.propose(lesson_id=lesson_id, title="T", rule="R", category="C", severity="LOW", proposer_id="cx", affected_peers=())
    service.approve(lesson_id, approved_by_actor_id="human:alice")
    service.activate(lesson_id, actor_id="cx")


def test_retire_and_supersede_record_lifecycle_metadata(tmp_path: Path) -> None:
    service, broker = _service(tmp_path)
    _active(service, "retire-me")
    service.retire("retire-me", actor_id="cx", reason="STALE")
    state = broker.get_target("lesson:retire-me").state
    assert state["lifecycle"] == "RETIRED"
    assert state["validity"]["retirement_reason"] == "STALE"

    _active(service, "supersede-me")
    service.supersede("supersede-me", actor_id="cx", replacement_lesson_id="replacement")
    assert broker.get_target("lesson:supersede-me").state["lifecycle"] == "SUPERSEDED"
    assert broker.get_target("lesson:supersede-me").state["validity"]["superseded_by"] == "replacement"


def test_quarantine_is_terminal(tmp_path: Path) -> None:
    service, broker = _service(tmp_path)
    service.propose(lesson_id="quarantine-me", title="T", rule="R", category="C", severity="LOW", proposer_id="cx", affected_peers=())
    service.quarantine("quarantine-me", actor_id="cx", reason="bad evidence", evidence="EV-1")
    assert broker.get_target("lesson:quarantine-me").state["lifecycle"] == "QUARANTINED"
    with pytest.raises(InvalidMutationError):
        service.activate("quarantine-me", actor_id="cx")


def test_delivery_target_is_independent_from_lesson_revision(tmp_path: Path) -> None:
    service, broker = _service(tmp_path)
    _active(service, "deliver-me")
    before = broker.get_target("lesson:deliver-me").revision
    service.record_delivery_pending("deliver-me", "cx")
    service.record_delivery_complete("deliver-me", "cx", command_id="cmd-1", correlation_id="corr-1")
    delivery = broker.get_target("lesson-delivery:deliver-me:cx")
    assert delivery is not None
    assert delivery.state["status"] == "DELIVERED"
    assert delivery.state["delivery_revision"] == 1
    assert delivery.state["delivery_evidence"]["result_sha256"].startswith("sha256:")
    assert broker.get_target("lesson:deliver-me").revision == before
