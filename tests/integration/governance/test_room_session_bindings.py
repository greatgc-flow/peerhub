from __future__ import annotations

from pathlib import Path

from fakes import DeterministicClock, SequentialIdSource
from peerhub.dispatch.duty_lease import DutyOwnerIdentity
from peerhub.dispatch.room_session import (
    RoomParticipationCoordinator,
    RoomSessionEndRequest,
    RoomSessionOpenRequest,
)
from peerhub.governance.activity import rebuild_room_session_bindings
from peerhub.governance.broker import GovernanceBroker
from peerhub.governance.rooms import RoomsService
from peerhub.persistence.sqlite import SqliteStateStore


def test_rebuild_room_session_bindings_tracks_active_sessions_only(
    tmp_path: Path,
) -> None:
    store = SqliteStateStore(
        tmp_path / "room-session-bindings.sqlite3",
        workspace_home_id="room-session-bindings-test",
    )
    store.initialize()
    broker = GovernanceBroker(
        store, clock=DeterministicClock(1_000), ids=SequentialIdSource()
    )
    rooms = RoomsService(
        broker, clock=DeterministicClock(2_000), ids=SequentialIdSource()
    )
    sessions = RoomParticipationCoordinator(
        store, clock=DeterministicClock(100), ids=SequentialIdSource()
    )
    rooms.create_room(
        room_id="room-1",
        topic_id="topic-1",
        title="Continuity",
        creator_id="cx",
        participants=("cx", "ag"),
    )
    before = broker.get_target("room-1")
    assert before is not None

    first = sessions.open_session(
        RoomSessionOpenRequest(
            workspace_scope_id="workspace-1",
            room_id="room-1",
            actor_principal_id="principal-cx",
            owner=DutyOwnerIdentity("instance-cx", "cx.standard"),
            session_fingerprint="cx-terminal",
            heartbeat_timeout_ms=1_000,
        )
    )
    second = sessions.open_session(
        RoomSessionOpenRequest(
            workspace_scope_id="workspace-1",
            room_id="room-1",
            actor_principal_id="principal-ag",
            owner=DutyOwnerIdentity("instance-ag", "ag.standard"),
            session_fingerprint="ag-terminal",
            heartbeat_timeout_ms=1_000,
        )
    )

    rebuild_room_session_bindings(
        broker, "room-1", sessions.list_active_sessions("room-1")
    )
    rebuilt = broker.get_target("room-1")
    assert rebuilt is not None
    assert rebuilt.state["session_bindings"] == (
        {
            "binding_id": first.session_id,
            "instance_id": "instance-cx",
            "profile_id": "cx.standard",
            "session_id": first.session_id,
            "role": "participant",
            "bound_at": first.created_at,
        },
        {
            "binding_id": second.session_id,
            "instance_id": "instance-ag",
            "profile_id": "ag.standard",
            "session_id": second.session_id,
            "role": "participant",
            "bound_at": second.created_at,
        },
    )
    assert {
        key: value for key, value in rebuilt.state.items()
        if key != "session_bindings"
    } == {
        key: value for key, value in before.state.items()
        if key != "session_bindings"
    }

    sessions.end_session(
        RoomSessionEndRequest(
            session_id=first.session_id,
            session_generation=first.session_generation,
            workspace_scope_id=first.workspace_scope_id,
            room_id=first.room_id,
            actor_principal_id=first.actor_principal_id,
            owner=first.owner,
        )
    )
    rebuild_room_session_bindings(
        broker, "room-1", sessions.list_active_sessions("room-1")
    )
    after_end = broker.get_target("room-1")
    assert after_end is not None
    assert after_end.state["session_bindings"] == (
        {
            "binding_id": second.session_id,
            "instance_id": "instance-ag",
            "profile_id": "ag.standard",
            "session_id": second.session_id,
            "role": "participant",
            "bound_at": second.created_at,
        },
    )
    assert {
        key: value for key, value in after_end.state.items()
        if key != "session_bindings"
    } == {
        key: value for key, value in rebuilt.state.items()
        if key != "session_bindings"
    }
