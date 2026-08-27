from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from fakes import FakeClock, FakeIdSource
from peerhub.core.errors import InvalidMutationError
from peerhub.dispatch.duty_lease import DutyOwnerIdentity
from peerhub.dispatch.room_session import (
    RoomParticipationCoordinator,
    RoomSessionEndRequest,
    RoomSessionHeartbeatRequest,
    RoomSessionOpenRequest,
    RoomSessionState,
)
from peerhub.persistence.sqlite import SqliteStateStore


def _coordinator(
    tmp_path: Path, clock_values: list[int]
) -> tuple[RoomParticipationCoordinator, Path]:
    database_path = tmp_path / "room-session.sqlite3"
    store = SqliteStateStore(
        database_path, workspace_home_id="room-session-test"
    )
    store.initialize()
    coordinator = RoomParticipationCoordinator(
        store,
        clock=FakeClock(clock_values),
        ids=FakeIdSource([f"room-session-id-{i}" for i in range(50)]),
    )
    return coordinator, database_path


def _open_request(
    *,
    actor_principal_id: str = "principal-1",
    owner: DutyOwnerIdentity | None = None,
    fingerprint: str = "fingerprint-1",
) -> RoomSessionOpenRequest:
    return RoomSessionOpenRequest(
        workspace_scope_id="workspace-1",
        room_id="room-1",
        actor_principal_id=actor_principal_id,
        owner=owner or DutyOwnerIdentity("instance-1", "cx.standard"),
        session_fingerprint=fingerprint,
        heartbeat_timeout_ms=50,
    )


def _heartbeat_request(
    session_id: str,
    session_generation: int,
    *,
    actor_principal_id: str = "principal-1",
    owner: DutyOwnerIdentity | None = None,
) -> RoomSessionHeartbeatRequest:
    return RoomSessionHeartbeatRequest(
        session_id=session_id,
        session_generation=session_generation,
        workspace_scope_id="workspace-1",
        room_id="room-1",
        actor_principal_id=actor_principal_id,
        owner=owner or DutyOwnerIdentity("instance-1", "cx.standard"),
    )


def _end_request(
    session_id: str,
    session_generation: int,
    *,
    actor_principal_id: str = "principal-1",
    owner: DutyOwnerIdentity | None = None,
) -> RoomSessionEndRequest:
    return RoomSessionEndRequest(
        session_id=session_id,
        session_generation=session_generation,
        workspace_scope_id="workspace-1",
        room_id="room-1",
        actor_principal_id=actor_principal_id,
        owner=owner or DutyOwnerIdentity("instance-1", "cx.standard"),
    )


def _event_types(database_path: Path) -> list[str]:
    with sqlite3.connect(database_path) as connection:
        rows = connection.execute(
            "SELECT event_type FROM room_session_events ORDER BY rowid"
        ).fetchall()
    return [str(row[0]) for row in rows]


def test_open_fresh_and_duplicate_is_idempotent(tmp_path: Path) -> None:
    coordinator, database_path = _coordinator(tmp_path, [100, 110])

    first = coordinator.open_session(_open_request())
    duplicate = coordinator.open_session(_open_request())

    assert first.state is RoomSessionState.ACTIVE
    assert first.session_generation == 1
    assert duplicate == first
    assert _event_types(database_path) == ["OPENED"]

    with sqlite3.connect(database_path) as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM room_participation_sessions"
        ).fetchone()
    assert count == (1,)


def test_fingerprint_drift_abandons_and_replaces_session(
    tmp_path: Path,
) -> None:
    coordinator, database_path = _coordinator(tmp_path, [100, 110])
    first = coordinator.open_session(_open_request())

    successor = coordinator.open_session(
        _open_request(fingerprint="fingerprint-2")
    )

    retired = coordinator.get_session(first.session_id)
    assert retired is not None
    assert retired.state is RoomSessionState.ABANDONED
    assert successor.session_id != first.session_id
    assert successor.session_generation == 2
    assert successor.resume_parent_session_id == first.session_id
    assert successor.state is RoomSessionState.ACTIVE
    assert _event_types(database_path) == [
        "OPENED",
        "ABANDONED",
        "OPENED",
    ]


def test_expired_compatible_session_is_resumed_as_successor(
    tmp_path: Path,
) -> None:
    coordinator, database_path = _coordinator(tmp_path, [100, 200])
    first = coordinator.open_session(_open_request())

    successor = coordinator.open_session(_open_request())

    retired = coordinator.get_session(first.session_id)
    assert retired is not None
    assert retired.state is RoomSessionState.EXPIRED
    assert successor.session_generation == 2
    assert successor.resume_parent_session_id == first.session_id
    assert _event_types(database_path) == [
        "OPENED",
        "EXPIRED",
        "RESUMED",
    ]


def test_heartbeat_renews_and_rejects_stale_fences(tmp_path: Path) -> None:
    coordinator, database_path = _coordinator(
        tmp_path, [100, 120, 130, 140]
    )
    session = coordinator.open_session(_open_request())

    renewed = coordinator.heartbeat(
        _heartbeat_request(
            session.session_id, session.session_generation
        ),
        heartbeat_timeout_ms=100,
    )
    assert renewed.heartbeat_expires_at == 220

    with pytest.raises(InvalidMutationError, match="fence"):
        coordinator.heartbeat(
            _heartbeat_request(
                session.session_id, session.session_generation + 1
            ),
            heartbeat_timeout_ms=100,
        )
    with pytest.raises(InvalidMutationError, match="fence"):
        coordinator.heartbeat(
            _heartbeat_request(
                session.session_id,
                session.session_generation,
                owner=DutyOwnerIdentity("wrong-instance", "cx.standard"),
            ),
            heartbeat_timeout_ms=100,
        )

    assert _event_types(database_path) == ["OPENED"]


def test_heartbeat_rejects_an_already_expired_session(
    tmp_path: Path,
) -> None:
    coordinator, _ = _coordinator(tmp_path, [100, 200])
    session = coordinator.open_session(_open_request())

    with pytest.raises(InvalidMutationError, match="fence"):
        coordinator.heartbeat(
            _heartbeat_request(
                session.session_id, session.session_generation
            ),
            heartbeat_timeout_ms=100,
        )


def test_end_session_validates_fence_and_transitions_to_ended(
    tmp_path: Path,
) -> None:
    coordinator, database_path = _coordinator(
        tmp_path, [100, 110, 120, 130, 140]
    )
    session = coordinator.open_session(_open_request())

    with pytest.raises(InvalidMutationError, match="fence"):
        coordinator.end_session(
            _end_request(
                session.session_id, session.session_generation + 1
            )
        )
    with pytest.raises(InvalidMutationError, match="fence"):
        coordinator.end_session(
            _end_request(
                session.session_id,
                session.session_generation,
                owner=DutyOwnerIdentity(
                    "wrong-instance", "cx.standard"
                ),
            )
        )

    ended = coordinator.end_session(
        _end_request(
            session.session_id, session.session_generation
        )
    )
    assert ended.state is RoomSessionState.ENDED
    assert coordinator.get_session(session.session_id) == ended
    assert _event_types(database_path) == ["OPENED", "ENDED"]

    with pytest.raises(InvalidMutationError, match="fence"):
        coordinator.heartbeat(
            _heartbeat_request(
                session.session_id, session.session_generation
            ),
            heartbeat_timeout_ms=100,
        )


def test_different_participants_can_be_active_in_the_same_room(
    tmp_path: Path,
) -> None:
    coordinator, _ = _coordinator(tmp_path, [100, 101, 102, 103])

    first = coordinator.open_session(_open_request())
    second = coordinator.open_session(
        _open_request(
            actor_principal_id="principal-2",
            owner=DutyOwnerIdentity("instance-2", "cc.standard"),
        )
    )

    assert first.state is RoomSessionState.ACTIVE
    assert second.state is RoomSessionState.ACTIVE
    assert first.session_id != second.session_id
    assert coordinator.get_active_session(
        "workspace-1",
        "room-1",
        "principal-1",
        "instance-1",
        "cx.standard",
    ) == first
    assert coordinator.get_active_session(
        "workspace-1",
        "room-1",
        "principal-2",
        "instance-2",
        "cc.standard",
    ) == second
