"""Dedicated room/role duty leases, separate from governed mutations."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol, cast

from peerhub.core.context import Clock, IdSource
from peerhub.core.errors import InvalidMutationError, RecordNotFoundError
from peerhub.state.contract import StateStore


class DutyLeaseState(StrEnum):
    ACTIVE = "ACTIVE"
    EXPIRED = "EXPIRED"
    RELEASED = "RELEASED"


@dataclass(frozen=True)
class DutyOwnerIdentity:
    instance_id: str
    profile_id: str


@dataclass(frozen=True)
class DutyLeaseCreateRequest:
    room_id: str
    role: str
    owner: DutyOwnerIdentity
    owner_principal_id: str
    heartbeat_timeout_ms: int
    authority_epoch: int
    term: int = 1
    challenge_until: int | None = None


@dataclass(frozen=True)
class DutyLeaseRenewRequest:
    lease_id: str
    room_id: str
    role: str
    owner: DutyOwnerIdentity
    term: int
    authority_epoch: int


@dataclass(frozen=True)
class DutyLeaseSnapshot:
    lease_id: str
    room_id: str
    role: str
    owner: DutyOwnerIdentity
    owner_principal_id: str
    authority_epoch: int
    term: int
    challenge_until: int | None
    state: DutyLeaseState
    heartbeat_expires_at: int
    created_at: int
    updated_at: int
    consecutive_terms_held: int


class DutyLeaseUnitOfWork(Protocol):
    def commit(self) -> None: ...
    def get_active_duty_lease(self, room_id: str, role: str) -> DutyLeaseSnapshot | None: ...
    def get_latest_duty_lease(self, room_id: str, role: str) -> DutyLeaseSnapshot | None: ...
    def mark_duty_lease_expired(self, lease_id: str, updated_at: int) -> None: ...
    def insert_duty_lease(self, snapshot: DutyLeaseSnapshot) -> None: ...
    def update_duty_lease_heartbeat(self, lease_id: str, heartbeat_expires_at: int, updated_at: int) -> None: ...
    def get_duty_lease(self, lease_id: str) -> DutyLeaseSnapshot | None: ...


class DutyLeaseCoordinator:
    """Persist and fence high-frequency room/role duty leases."""

    MONOPOLY_THRESHOLD = 3

    def __init__(self, store: StateStore[Any, Any], *, clock: Clock, ids: IdSource) -> None:
        self._store = store
        self._clock = clock
        self._ids = ids

    def create_lease(self, request: DutyLeaseCreateRequest) -> DutyLeaseSnapshot:
        now = self._clock.now()
        with self._store.unit_of_work() as unit:
            unit = cast(DutyLeaseUnitOfWork, unit)
            current = unit.get_active_duty_lease(request.room_id, request.role)
            if current is not None:
                if current.heartbeat_expires_at >= now:
                    raise InvalidMutationError("duty lease already active")
                unit.mark_duty_lease_expired(current.lease_id, now)
            prior = unit.get_latest_duty_lease(request.room_id, request.role)
            consecutive = 1
            if prior is not None and prior.owner == request.owner:
                consecutive = prior.consecutive_terms_held + 1
            if consecutive >= self.MONOPOLY_THRESHOLD:
                raise InvalidMutationError("AP-20 duty monopoly guard rejected claim")
            epoch = max(request.authority_epoch, (prior.authority_epoch + 1) if prior else 1)
            lease_id = self._ids.new_id("duty-lease")
            snapshot = DutyLeaseSnapshot(
                lease_id, request.room_id, request.role, request.owner,
                request.owner_principal_id, epoch, request.term,
                request.challenge_until, DutyLeaseState.ACTIVE,
                now + request.heartbeat_timeout_ms, now, now, consecutive,
            )
            unit.insert_duty_lease(snapshot)
            unit.commit()
        # Returned directly rather than re-read via unit.get_duty_lease():
        # the unit of work is finished after commit(), and its typed
        # get_duty_lease() (unlike the old raw _db() access it replaced)
        # correctly refuses to read from a finished unit -- the snapshot
        # just inserted already has every field the caller needs.
        return snapshot

    def renew_lease(self, request: DutyLeaseRenewRequest, *, heartbeat_timeout_ms: int) -> DutyLeaseSnapshot:
        now = self._clock.now()
        with self._store.unit_of_work() as unit:
            unit = cast(DutyLeaseUnitOfWork, unit)
            row = unit.get_duty_lease(request.lease_id)
            if row is None:
                raise RecordNotFoundError("duty_lease", request.lease_id)
            matches = (
                row.state == DutyLeaseState.ACTIVE and row.room_id == request.room_id and row.role == request.role
                and row.owner == request.owner and row.term == request.term and row.authority_epoch == request.authority_epoch
            )
            if not matches:
                raise InvalidMutationError("duty lease fence mismatch")
            unit.update_duty_lease_heartbeat(request.lease_id, now + heartbeat_timeout_ms, now)
            unit.commit()
        # See create_lease's comment: constructed directly, not re-read
        # after commit (the unit of work is finished by then).
        return DutyLeaseSnapshot(
            row.lease_id, row.room_id, row.role, row.owner, row.owner_principal_id,
            row.authority_epoch, row.term, row.challenge_until, row.state,
            now + heartbeat_timeout_ms, row.created_at, now, row.consecutive_terms_held,
        )
