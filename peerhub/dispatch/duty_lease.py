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
class DutyLeaseCloseRequest(DutyLeaseRenewRequest):
    pass

@dataclass(frozen=True)
class DutyLeaseFenceCheckRequest(DutyLeaseRenewRequest):
    pass

@dataclass(frozen=True)
class DutyRecoveryReceipt:
    lease_id: str
    recovered_at: int
    recovery_actor_principal_id: str
    trigger: str
    evidence_digest: str
    policy_id: str
    policy_revision: str


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
    def release_duty_lease(self, lease_id: str, updated_at: int) -> None: ...
    def insert_duty_recovery_receipt(self, receipt_id: str, receipt: DutyRecoveryReceipt) -> None: ...


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

    def close_lease(self, request: DutyLeaseCloseRequest) -> DutyLeaseSnapshot:
        now = self._clock.now()
        with self._store.unit_of_work() as unit:
            unit = cast(DutyLeaseUnitOfWork, unit)
            row = unit.get_duty_lease(request.lease_id)
            self._require_fence(row, request)
            assert row is not None
            unit.release_duty_lease(request.lease_id, now)
            unit.commit()
        return DutyLeaseSnapshot(row.lease_id, row.room_id, row.role, row.owner, row.owner_principal_id, row.authority_epoch, row.term, row.challenge_until, DutyLeaseState.RELEASED, row.heartbeat_expires_at, row.created_at, now, row.consecutive_terms_held)

    def expire_and_recover_lease(self, lease_id: str, *, recovery_actor_principal_id: str, trigger: str, evidence_digest: str, policy_id: str, policy_revision: str) -> tuple[DutyLeaseSnapshot, DutyRecoveryReceipt]:
        now = self._clock.now()
        with self._store.unit_of_work() as unit:
            unit = cast(DutyLeaseUnitOfWork, unit)
            row = unit.get_duty_lease(lease_id)
            if row is None:
                raise RecordNotFoundError("duty_lease", lease_id)
            if row.state != DutyLeaseState.ACTIVE or row.heartbeat_expires_at >= now:
                raise InvalidMutationError("duty lease is not expired")
            unit.mark_duty_lease_expired(lease_id, now)
            receipt = DutyRecoveryReceipt(lease_id, now, recovery_actor_principal_id, trigger, evidence_digest, policy_id, policy_revision)
            unit.insert_duty_recovery_receipt(self._ids.new_id("duty-recovery"), receipt)
            unit.commit()
        return DutyLeaseSnapshot(row.lease_id, row.room_id, row.role, row.owner, row.owner_principal_id, row.authority_epoch, row.term, row.challenge_until, DutyLeaseState.EXPIRED, row.heartbeat_expires_at, row.created_at, now, row.consecutive_terms_held), receipt

    def validate_lease_fence(self, request: DutyLeaseFenceCheckRequest) -> tuple[bool, tuple[str, ...]]:
        with self._store.read_unit_of_work() as unit:
            row = cast(DutyLeaseUnitOfWork, unit).get_duty_lease(request.lease_id)
        if row is None:
            return False, ("lease_not_found",)
        reasons: list[str] = []
        if row.state != DutyLeaseState.ACTIVE: reasons.append("state_not_active")
        if row.room_id != request.room_id or row.role != request.role: reasons.append("scope_mismatch")
        if row.owner != request.owner: reasons.append("owner_mismatch")
        if row.term != request.term: reasons.append("term_mismatch")
        if row.authority_epoch != request.authority_epoch: reasons.append("epoch_mismatch")
        return not reasons, tuple(reasons)

    def get_active_duty_lease(self, room_id: str, role: str) -> DutyLeaseSnapshot | None:
        """Read the currently active lease without exposing store internals."""
        with self._store.read_unit_of_work() as unit:
            return cast(DutyLeaseUnitOfWork, unit).get_active_duty_lease(room_id, role)

    def get_lease(self, lease_id: str) -> DutyLeaseSnapshot | None:
        with self._store.read_unit_of_work() as unit:
            return cast(DutyLeaseUnitOfWork, unit).get_duty_lease(lease_id)

    @staticmethod
    def _require_fence(row: DutyLeaseSnapshot | None, request: DutyLeaseRenewRequest) -> None:
        if row is None:
            raise RecordNotFoundError("duty_lease", request.lease_id)
        if row.state != DutyLeaseState.ACTIVE or row.room_id != request.room_id or row.role != request.role or row.owner != request.owner or row.term != request.term or row.authority_epoch != request.authority_epoch:
            raise InvalidMutationError("duty lease fence mismatch")
