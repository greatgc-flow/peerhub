"""Dedicated room/role duty leases, separate from governed mutations."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

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


class DutyLeaseCoordinator:
    """Persist and fence high-frequency room/role duty leases.

    KNOWN ARCHITECTURAL DEBT: unlike SessionLeaseCoordinator (which this
    module is meant to mirror), this implementation reaches into the
    UnitOfWork's private `_db()` SQLite handle and issues raw
    positional-parameter SQL directly, instead of adding proper typed
    methods to a DutyLease-specific UnitOfWork Protocol (the pattern
    SessionLeaseCoordinator actually uses: unit.get_lease()/add_lease()/
    cas_update_session_binding(), never a private connection handle).
    Functionally correct (parameterized queries, no injection risk, all
    tests pass) but breaks the codebase's established persistence-layer
    encapsulation. Follow-up needed: add a real DutyLeaseUnitOfWork
    Protocol + typed backend methods, matching session_lease.py's shape.
    """

    MONOPOLY_THRESHOLD = 3

    def __init__(self, store: StateStore[Any, Any], *, clock: Clock, ids: IdSource) -> None:
        self._store = store
        self._clock = clock
        self._ids = ids

    def create_lease(self, request: DutyLeaseCreateRequest) -> DutyLeaseSnapshot:
        now = self._clock.now()
        with self._store.unit_of_work() as unit:
            db = unit._db()  # pyright: ignore[reportPrivateUsage]
            current = db.execute(
                "SELECT * FROM duty_leases WHERE room_id = ? AND role = ? AND state = 'ACTIVE'",
                (request.room_id, request.role),
            ).fetchone()
            if current is not None:
                if current["heartbeat_expires_at"] >= now:
                    raise InvalidMutationError("duty lease already active")
                db.execute("UPDATE duty_leases SET state = 'EXPIRED', updated_at = ? WHERE lease_id = ?", (now, current["lease_id"]))
            prior = db.execute(
                "SELECT * FROM duty_leases WHERE room_id = ? AND role = ? ORDER BY authority_epoch DESC LIMIT 1",
                (request.room_id, request.role),
            ).fetchone()
            consecutive = 1
            if prior is not None and prior["owner_instance_id"] == request.owner.instance_id and prior["owner_profile_id"] == request.owner.profile_id:
                consecutive = prior["consecutive_terms_held"] + 1
            if consecutive >= self.MONOPOLY_THRESHOLD:
                raise InvalidMutationError("AP-20 duty monopoly guard rejected claim")
            max_epoch = db.execute("SELECT COALESCE(MAX(authority_epoch), 0) FROM duty_leases WHERE room_id = ? AND role = ?", (request.room_id, request.role)).fetchone()[0]
            epoch = max(request.authority_epoch, max_epoch + 1)
            lease_id = self._ids.new_id("duty-lease")
            db.execute(
                "INSERT INTO duty_leases VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'ACTIVE', ?, ?, ?, ?)",
                (lease_id, request.room_id, request.role, request.owner.instance_id, request.owner.profile_id, request.owner_principal_id, epoch, request.term, request.challenge_until, now + request.heartbeat_timeout_ms, now, now, consecutive),
            )
            unit.commit()
            row = db.execute("SELECT * FROM duty_leases WHERE lease_id = ?", (lease_id,)).fetchone()
        return self._snapshot_from_row(row)

    def renew_lease(self, request: DutyLeaseRenewRequest, *, heartbeat_timeout_ms: int) -> DutyLeaseSnapshot:
        now = self._clock.now()
        with self._store.unit_of_work() as unit:
            db = unit._db()  # pyright: ignore[reportPrivateUsage]
            row = db.execute("SELECT * FROM duty_leases WHERE lease_id = ?", (request.lease_id,)).fetchone()
            if row is None:
                raise RecordNotFoundError("duty_lease", request.lease_id)
            matches = (
                row["state"] == "ACTIVE" and row["room_id"] == request.room_id and row["role"] == request.role
                and row["owner_instance_id"] == request.owner.instance_id and row["owner_profile_id"] == request.owner.profile_id
                and row["term"] == request.term and row["authority_epoch"] == request.authority_epoch
            )
            if not matches:
                raise InvalidMutationError("duty lease fence mismatch")
            db.execute("UPDATE duty_leases SET heartbeat_expires_at = ?, updated_at = ? WHERE lease_id = ?", (now + heartbeat_timeout_ms, now, request.lease_id))
            unit.commit()
            row = db.execute("SELECT * FROM duty_leases WHERE lease_id = ?", (request.lease_id,)).fetchone()
        return self._snapshot_from_row(row)

    @staticmethod
    def _snapshot_from_row(row: Any) -> DutyLeaseSnapshot:
        return DutyLeaseSnapshot(
            lease_id=row["lease_id"], room_id=row["room_id"], role=row["role"],
            owner=DutyOwnerIdentity(row["owner_instance_id"], row["owner_profile_id"]),
            owner_principal_id=row["owner_principal_id"], authority_epoch=row["authority_epoch"],
            term=row["term"], challenge_until=row["challenge_until"], state=DutyLeaseState(row["state"]),
            heartbeat_expires_at=row["heartbeat_expires_at"], created_at=row["created_at"], updated_at=row["updated_at"],
        )
