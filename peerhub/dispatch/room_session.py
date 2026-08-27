"""Dedicated liveness coordination for room-participation sessions."""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
from collections.abc import Sequence
from typing import Any, Protocol, cast

from peerhub.core.context import Clock, IdSource
from peerhub.core.errors import InvalidMutationError, RecordNotFoundError
from peerhub.state.contract import StateStore

from .duty_lease import DutyOwnerIdentity


class RoomSessionState(StrEnum):
    ACTIVE = "ACTIVE"
    ENDED = "ENDED"
    EXPIRED = "EXPIRED"
    ABANDONED = "ABANDONED"


class RoomSessionEventType(StrEnum):
    OPENED = "OPENED"
    RESUMED = "RESUMED"
    EXPIRED = "EXPIRED"
    ABANDONED = "ABANDONED"
    ENDED = "ENDED"


@dataclass(frozen=True)
class RoomSessionOpenRequest:
    workspace_scope_id: str
    room_id: str
    actor_principal_id: str
    owner: DutyOwnerIdentity
    session_fingerprint: str
    heartbeat_timeout_ms: int


@dataclass(frozen=True)
class RoomSessionHeartbeatRequest:
    session_id: str
    session_generation: int
    workspace_scope_id: str
    room_id: str
    actor_principal_id: str
    owner: DutyOwnerIdentity


@dataclass(frozen=True)
class RoomSessionEndRequest(RoomSessionHeartbeatRequest):
    pass


@dataclass(frozen=True)
class RoomSessionSnapshot:
    session_id: str
    workspace_scope_id: str
    room_id: str
    actor_principal_id: str
    owner: DutyOwnerIdentity
    session_fingerprint: str
    session_generation: int
    resume_parent_session_id: str | None
    state: RoomSessionState
    heartbeat_expires_at: int
    created_at: int
    updated_at: int


@dataclass(frozen=True)
class RoomSessionEvent:
    event_id: str
    session_id: str
    event_type: RoomSessionEventType
    at: int
    actor_principal_id: str


class RoomParticipationUnitOfWork(Protocol):
    def commit(self) -> None: ...

    def get_room_session(
        self, session_id: str
    ) -> RoomSessionSnapshot | None: ...

    def get_active_room_session(
        self,
        workspace_scope_id: str,
        room_id: str,
        actor_principal_id: str,
        instance_id: str,
        profile_id: str,
    ) -> RoomSessionSnapshot | None: ...

    def list_active_room_sessions(
        self, room_id: str
    ) -> Sequence[RoomSessionSnapshot]: ...

    def get_latest_room_session(
        self,
        workspace_scope_id: str,
        room_id: str,
        actor_principal_id: str,
        instance_id: str,
        profile_id: str,
    ) -> RoomSessionSnapshot | None: ...

    def insert_room_session(self, snapshot: RoomSessionSnapshot) -> None: ...

    def update_room_session_heartbeat(
        self,
        current: RoomSessionSnapshot,
        heartbeat_expires_at: int,
        updated_at: int,
    ) -> bool: ...

    def transition_room_session(
        self,
        current: RoomSessionSnapshot,
        state: RoomSessionState,
        updated_at: int,
        *,
        allow_expired: bool = False,
    ) -> bool: ...

    def insert_room_session_event(self, event: RoomSessionEvent) -> None: ...


class RoomParticipationCoordinator:
    """Persist concurrent, heartbeat-driven participation in rooms."""

    def __init__(
        self,
        store: StateStore[Any, Any],
        *,
        clock: Clock,
        ids: IdSource,
    ) -> None:
        self._store = store
        self._clock = clock
        self._ids = ids

    def open_session(
        self, request: RoomSessionOpenRequest
    ) -> RoomSessionSnapshot:
        if request.heartbeat_timeout_ms < 1:
            raise InvalidMutationError(
                "heartbeat_timeout_ms must be positive"
            )

        now = self._clock.now()
        with self._store.unit_of_work() as unit:
            unit = cast(RoomParticipationUnitOfWork, unit)
            current = unit.get_active_room_session(
                request.workspace_scope_id,
                request.room_id,
                request.actor_principal_id,
                request.owner.instance_id,
                request.owner.profile_id,
            )

            if current is not None:
                expired = current.heartbeat_expires_at < now
                fingerprint_drift = (
                    current.session_fingerprint
                    != request.session_fingerprint
                )
                if not expired and not fingerprint_drift:
                    return current

                retired_state = (
                    RoomSessionState.EXPIRED
                    if expired
                    else RoomSessionState.ABANDONED
                )
                if not unit.transition_room_session(
                    current,
                    retired_state,
                    now,
                    allow_expired=True,
                ):
                    raise InvalidMutationError(
                        "room session changed while being retired"
                    )
                retired = replace(
                    current,
                    state=retired_state,
                    updated_at=now,
                )
                self._record_event(
                    unit,
                    retired,
                    RoomSessionEventType(retired_state.value),
                    now,
                )
                prior = retired
            else:
                prior = unit.get_latest_room_session(
                    request.workspace_scope_id,
                    request.room_id,
                    request.actor_principal_id,
                    request.owner.instance_id,
                    request.owner.profile_id,
                )

            generation = (
                prior.session_generation + 1 if prior is not None else 1
            )
            resumes_expired = (
                prior is not None
                and prior.state is RoomSessionState.EXPIRED
                and prior.session_fingerprint
                == request.session_fingerprint
            )
            session = RoomSessionSnapshot(
                session_id=self._ids.new_id("room-session"),
                workspace_scope_id=request.workspace_scope_id,
                room_id=request.room_id,
                actor_principal_id=request.actor_principal_id,
                owner=request.owner,
                session_fingerprint=request.session_fingerprint,
                session_generation=generation,
                resume_parent_session_id=(
                    prior.session_id if prior is not None else None
                ),
                state=RoomSessionState.ACTIVE,
                heartbeat_expires_at=(
                    now + request.heartbeat_timeout_ms
                ),
                created_at=now,
                updated_at=now,
            )
            unit.insert_room_session(session)
            self._record_event(
                unit,
                session,
                (
                    RoomSessionEventType.RESUMED
                    if resumes_expired
                    else RoomSessionEventType.OPENED
                ),
                now,
            )
            # room TargetState.session_bindings is a rebuildable projection
            # and is deliberately not updated by this coordinator yet.
            unit.commit()
        return session

    def heartbeat(
        self,
        request: RoomSessionHeartbeatRequest,
        *,
        heartbeat_timeout_ms: int,
    ) -> RoomSessionSnapshot:
        if heartbeat_timeout_ms < 1:
            raise InvalidMutationError(
                "heartbeat_timeout_ms must be positive"
            )

        now = self._clock.now()
        with self._store.unit_of_work() as unit:
            unit = cast(RoomParticipationUnitOfWork, unit)
            current = unit.get_room_session(request.session_id)
            self._require_fence(current, request, now)
            assert current is not None
            heartbeat_expires_at = now + heartbeat_timeout_ms
            if not unit.update_room_session_heartbeat(
                current, heartbeat_expires_at, now
            ):
                raise InvalidMutationError("room session fence mismatch")
            unit.commit()
        return replace(
            current,
            heartbeat_expires_at=heartbeat_expires_at,
            updated_at=now,
        )

    def end_session(
        self, request: RoomSessionEndRequest
    ) -> RoomSessionSnapshot:
        now = self._clock.now()
        with self._store.unit_of_work() as unit:
            unit = cast(RoomParticipationUnitOfWork, unit)
            current = unit.get_room_session(request.session_id)
            self._require_fence(current, request, now)
            assert current is not None
            if not unit.transition_room_session(
                current, RoomSessionState.ENDED, now
            ):
                raise InvalidMutationError("room session fence mismatch")
            ended = replace(
                current,
                state=RoomSessionState.ENDED,
                updated_at=now,
            )
            self._record_event(
                unit, ended, RoomSessionEventType.ENDED, now
            )
            unit.commit()
        return ended

    def get_session(self, session_id: str) -> RoomSessionSnapshot | None:
        with self._store.read_unit_of_work() as unit:
            return cast(
                RoomParticipationUnitOfWork, unit
            ).get_room_session(session_id)

    def get_active_session(
        self,
        workspace_scope_id: str,
        room_id: str,
        actor_principal_id: str,
        instance_id: str,
        profile_id: str,
    ) -> RoomSessionSnapshot | None:
        now = self._clock.now()
        with self._store.read_unit_of_work() as unit:
            session = cast(
                RoomParticipationUnitOfWork, unit
            ).get_active_room_session(
                workspace_scope_id,
                room_id,
                actor_principal_id,
                instance_id,
                profile_id,
            )
        if session is None or session.heartbeat_expires_at < now:
            return None
        return session

    def list_active_sessions(self, room_id: str) -> Sequence[RoomSessionSnapshot]:
        """Return the currently live sessions for one room.

        The persistence state is authoritative, while callers may rebuild
        secondary projections from this short-lived read snapshot.
        """
        now = self._clock.now()
        with self._store.read_unit_of_work() as unit:
            sessions = cast(
                RoomParticipationUnitOfWork, unit
            ).list_active_room_sessions(room_id)
        return tuple(
            session
            for session in sessions
            if session.heartbeat_expires_at >= now
        )

    def _record_event(
        self,
        unit: RoomParticipationUnitOfWork,
        session: RoomSessionSnapshot,
        event_type: RoomSessionEventType,
        at: int,
    ) -> None:
        unit.insert_room_session_event(
            RoomSessionEvent(
                event_id=self._ids.new_id("room-session-event"),
                session_id=session.session_id,
                event_type=event_type,
                at=at,
                actor_principal_id=session.actor_principal_id,
            )
        )

    @staticmethod
    def _require_fence(
        current: RoomSessionSnapshot | None,
        request: RoomSessionHeartbeatRequest,
        now: int,
    ) -> None:
        if current is None:
            raise RecordNotFoundError("room_session", request.session_id)
        matches = (
            current.state is RoomSessionState.ACTIVE
            and current.session_generation == request.session_generation
            and current.workspace_scope_id == request.workspace_scope_id
            and current.room_id == request.room_id
            and current.actor_principal_id
            == request.actor_principal_id
            and current.owner == request.owner
            and current.heartbeat_expires_at >= now
        )
        if not matches:
            raise InvalidMutationError("room session fence mismatch")
