from __future__ import annotations

from pathlib import Path

import pytest

from fakes import FakeClock, FakeIdSource
from peerhub.core.errors import InvalidMutationError
from peerhub.dispatch.duty_lease import (
    DutyLeaseCoordinator, DutyLeaseCreateRequest, DutyLeaseRenewRequest,
    DutyLeaseCloseRequest, DutyLeaseFenceCheckRequest,
    DutyOwnerIdentity,
)
from peerhub.persistence.sqlite import SqliteStateStore


def _coordinator(tmp_path: Path) -> DutyLeaseCoordinator:
    store = SqliteStateStore(tmp_path / "duty.sqlite3", workspace_home_id="duty-test")
    store.initialize()
    return DutyLeaseCoordinator(store, clock=FakeClock(range(100, 200)), ids=FakeIdSource([f"lease-{i}" for i in range(10)]))


def _request(owner: DutyOwnerIdentity | None = None, epoch: int = 1) -> DutyLeaseCreateRequest:
    return DutyLeaseCreateRequest("room-1", "terminal-duty", owner or DutyOwnerIdentity("i-1", "cx.standard"), "principal-1", 50, epoch)


def test_create_and_renew_require_full_fence(tmp_path: Path) -> None:
    coordinator = _coordinator(tmp_path)
    lease = coordinator.create_lease(_request())
    assert lease.state.value == "ACTIVE"
    renewed = coordinator.renew_lease(DutyLeaseRenewRequest(lease.lease_id, "room-1", "terminal-duty", lease.owner, lease.term, lease.authority_epoch), heartbeat_timeout_ms=100)
    assert renewed.heartbeat_expires_at > lease.heartbeat_expires_at
    with pytest.raises(InvalidMutationError, match="fence"):
        coordinator.renew_lease(DutyLeaseRenewRequest(lease.lease_id, "room-1", "terminal-duty", DutyOwnerIdentity("other", "cx.standard"), lease.term, lease.authority_epoch), heartbeat_timeout_ms=100)


def test_active_duplicate_and_ap20_monopoly_are_rejected(tmp_path: Path) -> None:
    coordinator = _coordinator(tmp_path)
    first = coordinator.create_lease(_request())
    with pytest.raises(InvalidMutationError, match="already active"):
        coordinator.create_lease(_request(epoch=2))
    # Expiry is observed on the next acquisition; the same owner may not hold
    # the third consecutive term without an intervening different holder.
    coordinator._clock = FakeClock([1000, 1000, 1000, 1000])
    coordinator.create_lease(_request(epoch=2))
    coordinator._clock = FakeClock([2000, 2000])
    with pytest.raises(InvalidMutationError, match="AP-20"):
        coordinator.create_lease(_request(epoch=3))


def test_close_recovery_and_fence_validation(tmp_path: Path) -> None:
    coordinator = _coordinator(tmp_path)
    lease = coordinator.create_lease(_request())
    closed = coordinator.close_lease(DutyLeaseCloseRequest(lease.lease_id, lease.room_id, lease.role, lease.owner, lease.term, lease.authority_epoch))
    assert closed.state.value == "RELEASED"
    ok, reasons = coordinator.validate_lease_fence(DutyLeaseFenceCheckRequest(lease.lease_id, lease.room_id, lease.role, lease.owner, lease.term, lease.authority_epoch))
    assert not ok and "state_not_active" in reasons

    coordinator._clock = FakeClock([1000])
    live = coordinator.create_lease(_request(owner=DutyOwnerIdentity("i-2", "cx.standard"), epoch=2))
    coordinator._clock = FakeClock([2000])
    recovered, receipt = coordinator.expire_and_recover_lease(live.lease_id, recovery_actor_principal_id="human:a", trigger="HEARTBEAT_TIMEOUT", evidence_digest="sha256:e", policy_id="p", policy_revision="1")
    assert recovered.state.value == "EXPIRED"
    assert receipt.lease_id == live.lease_id


def test_sweep_expires_only_timed_out_active_leases(tmp_path: Path) -> None:
    coordinator = _coordinator(tmp_path)
    expired = coordinator.create_lease(_request())
    active = coordinator.create_lease(
        DutyLeaseCreateRequest(
            "room-2",
            "terminal-duty",
            DutyOwnerIdentity("i-2", "ag.standard"),
            "principal-2",
            5_000,
            1,
        )
    )
    coordinator._clock = FakeClock([200, 200])

    swept = coordinator.sweep_expired_leases(
        "terminal-duty",
        recovery_actor_principal_id="system:sweep",
        trigger="HEARTBEAT_TIMEOUT",
        evidence_digest="sha256:sweep",
        policy_id="terminal-duty-recovery",
        policy_revision="1",
    )

    assert tuple(lease.lease_id for lease in swept) == (expired.lease_id,)
    assert coordinator.get_lease(expired.lease_id).state.value == "EXPIRED"
    assert coordinator.get_lease(active.lease_id).state.value == "ACTIVE"
