from __future__ import annotations

from typing import Protocol

from peerhub.core.protocol import CommandID
from peerhub.governance.contract import OutboxEvent
from peerhub.state.contract import ReadUnitOfWork, UnitOfWork

from .capability import CapabilityLease
from .contract import (
    AdmissionReceipt,
    AttemptSnapshot,
    ClientRequestBinding,
    CommandIdempotencyBinding,
    LeaseSnapshot,
    RecoveryReceipt,
    RequestSnapshot,
    SessionBindingKey,
    SessionBindingSnapshot,
)


class DispatchReadUnitOfWork(ReadUnitOfWork, Protocol):
    """Read-only persistence operations required by the dispatch service."""

    def count_active_leases(self) -> int:
        """Return the number of active leases."""

        ...

    def get_client_request_binding(
        self,
        client_id: str,
        client_request_id: str,
    ) -> ClientRequestBinding | None:
        """Return a caller-request identity binding."""

        ...

    def get_command_idempotency_binding(
        self,
        client_id: str,
        command_type: str,
        idempotency_key: str,
    ) -> CommandIdempotencyBinding | None:
        """Return a command-idempotency binding."""

        ...

    def get_lease(self, lease_id: str) -> LeaseSnapshot | None:
        """Return one lease snapshot by ID."""

        ...

    def get_request(
        self,
        command_id: CommandID | str,
    ) -> RequestSnapshot | None:
        """Return one request snapshot."""

        ...

    def get_attempt(
        self,
        attempt_id: str,
    ) -> AttemptSnapshot | None:
        """Return one attempt snapshot."""

        ...

    def get_session_binding(
        self,
        key: SessionBindingKey,
    ) -> SessionBindingSnapshot | None:
        """Return one session binding snapshot."""

        ...

    def get_admission_receipt(
        self,
        admission_receipt_id: str,
    ) -> AdmissionReceipt | None:
        """Return an admission receipt by ID."""

        ...

    def get_capability_lease(
        self,
        capability_lease_id: str,
    ) -> CapabilityLease | None:
        """Return one capability lease by ID."""

        ...

    def get_capability_lease_by_command_id(
        self,
        command_id: CommandID | str,
    ) -> CapabilityLease | None:
        """Return the capability lease uniquely bound to a command."""

        ...

    def get_capability_lease_by_admission_receipt_id(
        self,
        admission_receipt_id: str,
    ) -> CapabilityLease | None:
        """Return the lease uniquely bound to an admission receipt."""

        ...

    def get_capability_lease_by_session_lease_id(
        self,
        session_lease_id: str,
    ) -> CapabilityLease | None:
        """Return capability lease by session lease id."""

        ...

    def get_capability_lease_for_attempt(
        self,
        command_id: CommandID | str,
        authorized_attempt_number: int,
    ) -> CapabilityLease | None:
        """Return capability lease by attempt number."""

        ...

    def get_retry_policy_max_attempts(
        self,
        command_id: CommandID | str,
    ) -> int | None:
        """Return the maximum attempts for a command."""

        ...


class DispatchUnitOfWork(DispatchReadUnitOfWork, UnitOfWork, Protocol):
    """Persistence operations required by the dispatch service."""

    def allocate_fencing_token(self) -> int:
        """Allocate one database-monotonic fencing token."""

        ...

    def count_active_leases(self) -> int:
        """Return the number of active leases."""
        
        ...

    def get_client_request_binding(
        self,
        client_id: str,
        client_request_id: str,
    ) -> ClientRequestBinding | None:
        """Return a caller-request identity binding."""

        ...

    def add_client_request_binding(
        self,
        binding: ClientRequestBinding,
    ) -> None:
        """Insert a caller-request identity binding."""

        ...

    def get_command_idempotency_binding(
        self,
        client_id: str,
        command_type: str,
        idempotency_key: str,
    ) -> CommandIdempotencyBinding | None:
        """Return a command-idempotency binding."""

        ...

    def add_command_idempotency_binding(
        self,
        binding: CommandIdempotencyBinding,
    ) -> None:
        """Insert a command-idempotency binding."""

        ...

    def add_admission_receipt(
        self,
        receipt: AdmissionReceipt,
    ) -> None:
        """Insert an immutable admission receipt."""

        ...

    def add_capability_lease(
        self,
        lease: CapabilityLease,
    ) -> None:
        """Insert an immutable capability lease."""

        ...

    def add_retry_policy(
        self,
        command_id: CommandID | str,
        max_attempts: int,
    ) -> None:
        """Insert a retry policy."""

        ...

    def add_request(self, request: RequestSnapshot) -> None:
        """Insert an admitted request snapshot."""

        ...

    def get_request(
        self,
        command_id: CommandID | str,
    ) -> RequestSnapshot | None:
        """Return a request by server command ID."""

        ...

    def cas_update_request(
        self,
        current: RequestSnapshot,
        updated: RequestSnapshot,
    ) -> bool:
        """CAS-update a request by revision."""

        ...

    def next_attempt_number(
        self,
        command_id: CommandID | str,
    ) -> int:
        """Return the next attempt number in this transaction."""

        ...

    def add_attempt(self, attempt: AttemptSnapshot) -> None:
        """Insert an immutable initial attempt snapshot."""

        ...

    def get_attempt(
        self,
        attempt_id: str,
    ) -> AttemptSnapshot | None:
        """Return an attempt by ID."""

        ...

    def list_attempts(
        self,
        command_id: CommandID | str,
    ) -> tuple[AttemptSnapshot, ...]:
        """Return attempts in monotonic attempt-number order."""

        ...

    def cas_update_attempt(
        self,
        current: AttemptSnapshot,
        updated: AttemptSnapshot,
    ) -> bool:
        """CAS-update an attempt by revision."""

        ...

    def cas_update_dispatch_bundle(
        self,
        current_request: RequestSnapshot,
        updated_request: RequestSnapshot,
        current_attempt: AttemptSnapshot,
        updated_attempt: AttemptSnapshot,
        current_lease: LeaseSnapshot,
        updated_lease: LeaseSnapshot,
    ) -> bool:
        """Atomically CAS request, attempt, and full lease fence."""

        ...

    def add_outbox_event(self, event: OutboxEvent) -> None:
        """Insert one canonical outbox event."""

        ...

    def get_lease(self, lease_id: str) -> LeaseSnapshot | None:
        """Return a lease by ID."""

        ...

    def add_lease(self, lease: LeaseSnapshot) -> None:
        """Persist a new lease."""

        ...

    def cas_update_lease(
        self,
        current: LeaseSnapshot,
        updated: LeaseSnapshot,
    ) -> bool:
        """CAS-update a lease if its complete fence is current."""

        ...

    def get_session_binding(
        self,
        key: SessionBindingKey,
    ) -> SessionBindingSnapshot | None:
        """Return a session binding by its canonical key."""

        ...

    def add_session_binding(
        self,
        binding: SessionBindingSnapshot,
    ) -> None:
        """Persist a new session binding."""

        ...

    def cas_update_session_binding(
        self,
        current: SessionBindingSnapshot,
        updated: SessionBindingSnapshot,
    ) -> bool:
        """CAS-update a binding if its revision is current."""

        ...

    def add_recovery_receipt(self, receipt: RecoveryReceipt) -> None:
        """Persist an immutable recovery receipt."""

        ...

    def get_recovery_receipt(
        self,
        receipt_id: str,
    ) -> RecoveryReceipt | None:
        """Return a recovery receipt by ID."""

        ...


class FaultPoint(str):
    """Named transaction fault boundaries for deterministic tests."""

    AFTER_REQUEST_WRITE = "AFTER_REQUEST_WRITE"
    AFTER_LEASE_WRITE = "AFTER_LEASE_WRITE"
    AFTER_SESSION_BINDING_WRITE = "AFTER_SESSION_BINDING_WRITE"
    AFTER_ADMISSION_RECEIPT_WRITE = (
        "AFTER_ADMISSION_RECEIPT_WRITE"
    )
    AFTER_CAPABILITY_LEASE_WRITE = (
        "AFTER_CAPABILITY_LEASE_WRITE"
    )
    AFTER_CLIENT_REQUEST_BINDING_WRITE = (
        "AFTER_CLIENT_REQUEST_BINDING_WRITE"
    )
    AFTER_IDEMPOTENCY_BINDING_WRITE = (
        "AFTER_IDEMPOTENCY_BINDING_WRITE"
    )
    AFTER_OUTBOX_WRITE = "AFTER_OUTBOX_WRITE"
    AFTER_ATTEMPT_WRITE = "AFTER_ATTEMPT_WRITE"
    AFTER_REQUEST_CAS = "AFTER_REQUEST_CAS"
    AFTER_ATTEMPT_CAS = "AFTER_ATTEMPT_CAS"
    AFTER_DISPATCH_BUNDLE_CAS = "AFTER_DISPATCH_BUNDLE_CAS"
    AFTER_LEASE_CAS = "AFTER_LEASE_CAS"
    AFTER_RECOVERY_RECEIPT_WRITE = (
        "AFTER_RECOVERY_RECEIPT_WRITE"
    )
    BEFORE_COMMIT = "BEFORE_COMMIT"
    AFTER_COMMIT = "AFTER_COMMIT"


class FaultInjector(Protocol):
    """Transaction-boundary fault injection hook."""

    def hit(self, point: str) -> None:
        """Raise a fault or return normally."""

        ...


class _NoFaultInjector:  # pyright: ignore[reportUnusedClass]
    def hit(self, point: str) -> None:
        del point
