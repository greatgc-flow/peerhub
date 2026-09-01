from __future__ import annotations

from pathlib import Path

import pytest

from fakes import FakeClock, FakeIdSource
from peerhub.core.errors import InvalidMutationError
from peerhub.governance.broker import GovernanceBroker
from peerhub.governance.rooms import RoomsService, _OMITTED
from peerhub.persistence.sqlite import SqliteStateStore


def _service(tmp_path: Path) -> tuple[RoomsService, GovernanceBroker]:
    store = SqliteStateStore(
        tmp_path / "rooms.sqlite3",
        workspace_home_id="rooms-test",
    )
    store.initialize()
    broker = GovernanceBroker(
        store,
        clock=FakeClock(range(1, 100)),
        ids=FakeIdSource([f"id-{i}" for i in range(1, 200)]),
    )
    service = RoomsService(
        broker,
        clock=FakeClock(range(1, 100)),
        ids=FakeIdSource([f"domain-{i}" for i in range(1, 200)]),
    )
    return service, broker


def test_update_room_summary_creates_fresh_target(tmp_path: Path) -> None:
    service, _ = _service(tmp_path)
    
    room_id = "test-room"
    
    # Initially None
    assert service.get_room_summary(room_id) is None
    
    # Create with mission and phase
    submission = service.update_room_summary(
        room_id,
        mission="explore",
        phase="scout",
        actor_id="test-actor",
    )
    
    assert submission is not None
    summary = service.get_room_summary(room_id)
    assert summary is not None
    assert summary.state["room_id"] == room_id
    assert summary.state["mission"] == "explore"
    assert summary.state["phase"] == "scout"
    assert summary.state["blocked"] is None
    assert "updated_at" in summary.state
    assert summary.revision == 1


def test_update_room_summary_partial_update(tmp_path: Path) -> None:
    service, _ = _service(tmp_path)
    room_id = "test-room"
    
    service.update_room_summary(
        room_id,
        mission="explore",
        blocked="no route",
        phase="scout",
        actor_id="test-actor",
    )
    
    # Partial update: change mission and clear blocked, leave phase alone
    service.update_room_summary(
        room_id,
        mission="gather",
        blocked=None,
        actor_id="test-actor",
    )
    
    summary = service.get_room_summary(room_id)
    assert summary is not None
    assert summary.revision == 2
    assert summary.state["mission"] == "gather"
    assert summary.state["blocked"] is None
    assert summary.state["phase"] == "scout"


def test_update_room_summary_validation(tmp_path: Path) -> None:
    service, _ = _service(tmp_path)
    room_id = "test-room"
    
    with pytest.raises(ValueError, match="mission must be a non-empty string"):
        service.update_room_summary(room_id, mission="", actor_id="a")
        
    with pytest.raises(ValueError, match="phase must be a non-empty string"):
        service.update_room_summary(room_id, phase="", actor_id="a")
        
    with pytest.raises(ValueError, match="blocked must be a non-empty string"):
        service.update_room_summary(room_id, blocked="", actor_id="a")

    # Creating with None is allowed
    service.update_room_summary(room_id, mission=None, blocked=None, phase=None, actor_id="a")
    summary = service.get_room_summary(room_id)
    assert summary is not None
    assert summary.state["mission"] is None
    assert summary.state["blocked"] is None
    assert summary.state["phase"] is None
