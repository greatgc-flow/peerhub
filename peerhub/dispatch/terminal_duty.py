"""Terminal-duty integration over the dedicated duty-lease coordinator."""

from __future__ import annotations

from peerhub.core.errors import InvalidMutationError
from peerhub.dispatch.duty_lease import (
    DutyLeaseCloseRequest,
    DutyLeaseCoordinator,
    DutyLeaseCreateRequest,
    DutyLeaseFenceCheckRequest,
    DutyLeaseRenewRequest,
    DutyLeaseSnapshot,
    DutyOwnerIdentity,
)


class TerminalDutyService:
    """Map terminal duty commands to the ``terminal-duty`` lease role."""

    def __init__(
        self,
        coordinator: DutyLeaseCoordinator,
        *,
        default_heartbeat_timeout_ms: int = 60_000,
    ) -> None:
        self._coordinator = coordinator
        self._default_heartbeat_timeout_ms = default_heartbeat_timeout_ms

    def claim_terminal_duty(
        self,
        room_id: str,
        owner: DutyOwnerIdentity,
        owner_principal_id: str,
        authority_epoch: int,
    ) -> DutyLeaseSnapshot:
        return self._coordinator.create_lease(
            DutyLeaseCreateRequest(
                room_id,
                "terminal-duty",
                owner,
                owner_principal_id,
                self._default_heartbeat_timeout_ms,
                authority_epoch,
            )
        )

    def send_heartbeat(
        self,
        lease_id: str,
        room_id: str,
        owner: DutyOwnerIdentity,
        term: int,
        authority_epoch: int,
    ) -> DutyLeaseSnapshot:
        return self._coordinator.renew_lease(
            DutyLeaseRenewRequest(
                lease_id,
                room_id,
                "terminal-duty",
                owner,
                term,
                authority_epoch,
            ),
            heartbeat_timeout_ms=self._default_heartbeat_timeout_ms,
        )

    def handoff_terminal_duty(
        self,
        current_lease_id: str,
        room_id: str,
        current_owner: DutyOwnerIdentity,
        term: int,
        authority_epoch: int,
        new_owner: DutyOwnerIdentity,
        new_owner_principal_id: str,
        new_authority_epoch: int,
    ) -> DutyLeaseSnapshot:
        fence_request = DutyLeaseFenceCheckRequest(
            current_lease_id,
            room_id,
            "terminal-duty",
            current_owner,
            term,
            authority_epoch,
        )
        valid, reasons = self._coordinator.validate_lease_fence(fence_request)
        if not valid:
            raise InvalidMutationError(
                f"cannot hand off terminal duty: {', '.join(reasons)}"
            )

        # This is intentionally two coordinator transactions. If the new claim
        # fails after close, duty is unheld; true cross-call atomicity is not
        # available from the current coordinator contract.
        self._coordinator.close_lease(
            DutyLeaseCloseRequest(
                current_lease_id,
                room_id,
                "terminal-duty",
                current_owner,
                term,
                authority_epoch,
            )
        )
        return self._coordinator.create_lease(
            DutyLeaseCreateRequest(
                room_id,
                "terminal-duty",
                new_owner,
                new_owner_principal_id,
                self._default_heartbeat_timeout_ms,
                new_authority_epoch,
            )
        )

    def active_terminal_holder(self, room_id: str) -> DutyOwnerIdentity | None:
        lease = self._coordinator.get_active_duty_lease(room_id, "terminal-duty")
        return lease.owner if lease is not None else None
