from __future__ import annotations

from pathlib import Path

import pytest

from fakes import FakeClock, FakeIdSource
from peerhub.core.errors import InvalidMutationError
from peerhub.dispatch.duty_lease import (
    DutyLeaseCoordinator,
    DutyOwnerIdentity,
)
from peerhub.dispatch.terminal_duty import TerminalDutyService
from peerhub.persistence.sqlite import SqliteStateStore


def _service(tmp_path: Path) -> tuple[TerminalDutyService, DutyLeaseCoordinator]:
    store = SqliteStateStore(tmp_path / "terminal.sqlite3", workspace_home_id="terminal-test")
    store.initialize()
    coordinator = DutyLeaseCoordinator(
        store,
        clock=FakeClock(range(100, 200)),
        ids=FakeIdSource([f"lease-{i}" for i in range(10)]),
    )
    return TerminalDutyService(coordinator, default_heartbeat_timeout_ms=50), coordinator


def test_claim_and_heartbeat_use_terminal_duty_defaults(tmp_path: Path) -> None:
    service, _ = _service(tmp_path)
    owner = DutyOwnerIdentity("instance-1", "cx.standard")

    lease = service.claim_terminal_duty("room-1", owner, "principal-1", 1)
    renewed = service.send_heartbeat(
        lease.lease_id, "room-1", owner, lease.term, lease.authority_epoch
    )

    assert lease.role == "terminal-duty"
    assert renewed.heartbeat_expires_at > lease.heartbeat_expires_at


def test_handoff_validates_current_holder_and_claims_new_holder(tmp_path: Path) -> None:
    service, _ = _service(tmp_path)
    current = DutyOwnerIdentity("instance-1", "cx.standard")
    replacement = DutyOwnerIdentity("instance-2", "ag.standard")
    lease = service.claim_terminal_duty("room-1", current, "principal-1", 1)

    handed_off = service.handoff_terminal_duty(
        lease.lease_id,
        "room-1",
        current,
        lease.term,
        lease.authority_epoch,
        replacement,
        "principal-2",
        2,
    )

    assert handed_off.owner == replacement
    assert handed_off.role == "terminal-duty"
    assert service.active_terminal_holder("room-1") == replacement


def test_handoff_rejects_non_holder_without_releasing_lease(tmp_path: Path) -> None:
    service, _ = _service(tmp_path)
    current = DutyOwnerIdentity("instance-1", "cx.standard")
    lease = service.claim_terminal_duty("room-1", current, "principal-1", 1)

    with pytest.raises(InvalidMutationError, match="cannot hand off"):
        service.handoff_terminal_duty(
            lease.lease_id,
            "room-1",
            DutyOwnerIdentity("wrong", "cx.standard"),
            lease.term,
            lease.authority_epoch,
            DutyOwnerIdentity("instance-2", "ag.standard"),
            "principal-2",
            2,
        )

    assert service.active_terminal_holder("room-1") == current


def test_close_is_retry_safe_for_the_same_released_fence(
    tmp_path: Path,
) -> None:
    service, _ = _service(tmp_path)
    owner = DutyOwnerIdentity("instance-1", "cx.standard")
    lease = service.claim_terminal_duty(
        "room-1", owner, "principal-1", 1
    )

    closed = service.close_terminal_duty(
        lease.lease_id,
        lease.room_id,
        owner,
        lease.term,
        lease.authority_epoch,
    )
    retried = service.close_terminal_duty(
        lease.lease_id,
        lease.room_id,
        owner,
        lease.term,
        lease.authority_epoch,
    )

    assert closed.state.value == "RELEASED"
    assert retried == closed
