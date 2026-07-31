"""Transactional request, attempt, session, lease, and outbox orchestration.

Slice 3 performs only deterministic persistence orchestration around pure
reducers. It does not spawn processes, contact providers, enforce live
deadlines or output limits, perform cancellation, or sweep orphaned
dispatch intents. Those behaviors remain outside this Phase 1 slice.
"""

from __future__ import annotations

from typing import Protocol

from peerhub.core.context import Clock, IdSource
from peerhub.core.errors import (
    ActorUnauthorizedError,
    DuplicateClientRequestError,
    IdempotencyPayloadMismatchError,
    InvalidMutationError,
    RecordNotFoundError,
    StaleRevisionError,
)
from peerhub.core.protocol import (
    PROTOCOL_MAJOR,
    PROTOCOL_MINOR,
    SCHEMA_VERSION,
    CommandEnvelope,
    CommandID,
    ErrorCode,
    RevisionValue,
)
from peerhub.governance.contract import OutboxEvent, OutboxState
from peerhub.state.contract import StateStore, UnitOfWork

from .contract import (
    AdmissionReceipt,
    AskResult,
    AttemptSnapshot,
    ClientRequestBinding,
    CommandIdempotencyBinding,
    CompletionContract,
    LeaseCloseRequest,
    LeaseCreateRequest,
    LeaseFenceCheckRequest,
    LeaseRenewRequest,
    LeaseReservationRequest,
    LeaseSnapshot,
    ProcessBirthIdentity,
    RecoveryReceipt,
    RecoveryTrigger,
    RequestSnapshot,
    SessionBindingKey,
    SessionBindingSnapshot,
    SessionResumeRequest,
    ValidatedSubmission,
)
from .model import (
    admit_request as reduce_admit_request,
)
from .model import (
    authorize_retry as reduce_authorize_retry,
)
from .model import (
    begin_assessment as reduce_begin_assessment,
)
from .model import (
    begin_cancellation as reduce_begin_cancellation,
)
from .model import (
    close_lease,
    complete_attempt as reduce_complete_attempt,
    create_attempt as reduce_create_attempt,
    create_lease,
    create_session_binding,
    expire_and_recover_lease,
    fail_pre_dispatch as reduce_fail_pre_dispatch,
    prepare_request as reduce_prepare_request,
    record_dispatch_intent as reduce_dispatch_intent,
    record_running as reduce_running,
    record_start_uncertain as reduce_start_uncertain,
    reject_request_policy as reduce_reject_policy,
    renew_lease,
    reserve_lease,
    resume_session_binding,
    validate_lease_fence,
    validate_submission,
)


class DispatchUnitOfWork(UnitOfWork, Protocol):
    """Persistence operations required by the dispatch service."""

    def allocate_fencing_token(self) -> int:
        """Allocate one database-monotonic fencing token."""

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

    def get_admission_receipt(
        self,
        admission_receipt_id: str,
    ) -> AdmissionReceipt | None:
        """Return an admission receipt by ID."""

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


class _NoFaultInjector:
    def hit(self, point: str) -> None:
        del point


class DispatchService:
    """Orchestrate Phase 1 dispatch state through one state store."""

    def __init__(
        self,
        store: StateStore[DispatchUnitOfWork],
        *,
        clock: Clock,
        ids: IdSource,
        fault_injector: FaultInjector | None = None,
    ) -> None:
        self._store = store
        self._clock = clock
        self._ids = ids
        self._faults = fault_injector or _NoFaultInjector()

    @staticmethod
    def _dispatch_event(
        request: RequestSnapshot,
        *,
        event_id: str,
        occurred_at: int,
    ) -> OutboxEvent:
        return OutboxEvent(
            event_id=event_id,
            protocol_major=PROTOCOL_MAJOR,
            protocol_minor=PROTOCOL_MINOR,
            schema_version=SCHEMA_VERSION,
            correlation_id=request.correlation_id,
            occurred_at=occurred_at,
            event_kind=request.state.value,
            payload={
                "command_id": str(request.command_id),
                "state": request.state.value,
                "request_revision": request.revision,
                "lease_id": request.lease_id,
                "terminal_error_code": (
                    request.terminal_error_code.value
                    if request.terminal_error_code is not None
                    else None
                ),
            },
            state=OutboxState.PENDING,
            created_at=occurred_at,
        )

    @staticmethod
    def _load_admission(
        unit: DispatchUnitOfWork,
        *,
        command_id: CommandID,
        admission_receipt_id: str,
    ) -> tuple[
        RequestSnapshot,
        AdmissionReceipt,
        LeaseSnapshot,
    ]:
        request = unit.get_request(command_id)
        if request is None:
            raise RuntimeError(
                "idempotency binding references a missing request"
            )
        receipt = unit.get_admission_receipt(
            admission_receipt_id
        )
        if receipt is None:
            raise RuntimeError(
                "idempotency binding references a missing "
                "admission receipt"
            )
        lease = unit.get_lease(receipt.lease_id)
        if lease is None:
            raise RuntimeError(
                "admission receipt references a missing lease"
            )
        if (
            request.command_id != command_id
            or receipt.command_id != command_id
            or request.lease_id != receipt.lease_id
            or lease.fence.command_id != command_id
        ):
            raise RuntimeError(
                "stored admission records are internally inconsistent"
            )
        return (request, receipt, lease)

    def _find_idempotent_admission(
        self,
        unit: DispatchUnitOfWork,
        submission: ValidatedSubmission,
        *,
        created_at: int,
    ) -> tuple[
        tuple[
            RequestSnapshot,
            AdmissionReceipt,
            LeaseSnapshot,
        ] | None,
        ClientRequestBinding | None,
        CommandIdempotencyBinding | None,
    ]:
        envelope = submission.envelope
        client_binding = unit.get_client_request_binding(
            envelope.client_id,
            envelope.client_request_id,
        )
        if (
            client_binding is not None
            and client_binding.payload_digest
            != submission.payload_digest
        ):
            raise DuplicateClientRequestError(
                envelope.client_id,
                envelope.client_request_id,
            )

        key_binding: CommandIdempotencyBinding | None = None
        if envelope.idempotency_key is not None:
            key_binding = unit.get_command_idempotency_binding(
                envelope.client_id,
                envelope.method,
                envelope.idempotency_key,
            )
            if (
                key_binding is not None
                and key_binding.payload_digest
                != submission.payload_digest
            ):
                raise IdempotencyPayloadMismatchError(
                    envelope.client_id,
                    envelope.method,
                    envelope.idempotency_key,
                )

        if (
            client_binding is not None
            and key_binding is not None
            and (
                client_binding.command_id
                != key_binding.command_id
                or client_binding.admission_receipt_id
                != key_binding.admission_receipt_id
            )
        ):
            raise RuntimeError(
                "client-request and idempotency bindings disagree"
            )

        if client_binding is None and key_binding is None:
            return (None, None, None)

        if client_binding is not None:
            command_id = client_binding.command_id
            admission_receipt_id = (
                client_binding.admission_receipt_id
            )
        else:
            if key_binding is None:
                raise AssertionError(
                    "idempotency binding selection is unreachable"
                )
            command_id = key_binding.command_id
            admission_receipt_id = (
                key_binding.admission_receipt_id
            )

        admission = self._load_admission(
            unit,
            command_id=command_id,
            admission_receipt_id=admission_receipt_id,
        )

        missing_client_binding = None
        if client_binding is None:
            missing_client_binding = ClientRequestBinding(
                client_id=envelope.client_id,
                client_request_id=envelope.client_request_id,
                payload_digest=submission.payload_digest,
                command_id=command_id,
                admission_receipt_id=admission_receipt_id,
                created_at=created_at,
            )

        missing_key_binding = None
        if (
            key_binding is None
            and envelope.idempotency_key is not None
        ):
            missing_key_binding = CommandIdempotencyBinding(
                client_id=envelope.client_id,
                command_type=envelope.method,
                idempotency_key=envelope.idempotency_key,
                payload_digest=submission.payload_digest,
                command_id=command_id,
                admission_receipt_id=admission_receipt_id,
                created_at=created_at,
            )

        return (
            admission,
            missing_client_binding,
            missing_key_binding,
        )

    @staticmethod
    def _require_request(
        unit: DispatchUnitOfWork,
        command_id: CommandID | str,
    ) -> RequestSnapshot:
        request = unit.get_request(command_id)
        if request is None:
            raise RecordNotFoundError(
                "dispatch_request",
                str(command_id),
            )
        return request

    @staticmethod
    def _require_attempt(
        unit: DispatchUnitOfWork,
        attempt_id: str,
    ) -> AttemptSnapshot:
        attempt = unit.get_attempt(attempt_id)
        if attempt is None:
            raise RecordNotFoundError(
                "dispatch_attempt",
                attempt_id,
            )
        return attempt

    @staticmethod
    def _require_lease(
        unit: DispatchUnitOfWork,
        lease_id: str,
    ) -> LeaseSnapshot:
        lease = unit.get_lease(lease_id)
        if lease is None:
            raise RecordNotFoundError("lease", lease_id)
        return lease

    @staticmethod
    def _raise_request_cas(
        unit: DispatchUnitOfWork,
        current: RequestSnapshot,
    ) -> None:
        latest = unit.get_request(current.command_id)
        raise StaleRevisionError(
            str(current.command_id),
            current.revision,
            0 if latest is None else latest.revision,
        )

    @staticmethod
    def _raise_attempt_cas(
        unit: DispatchUnitOfWork,
        current: AttemptSnapshot,
    ) -> None:
        latest = unit.get_attempt(current.attempt_id)
        raise StaleRevisionError(
            current.attempt_id,
            current.revision,
            0 if latest is None else latest.revision,
        )

    def _cas_request_attempt(
        self,
        unit: DispatchUnitOfWork,
        current_request: RequestSnapshot,
        updated_request: RequestSnapshot,
        current_attempt: AttemptSnapshot,
        updated_attempt: AttemptSnapshot,
    ) -> None:
        if not unit.cas_update_request(
            current_request,
            updated_request,
        ):
            self._raise_request_cas(unit, current_request)
        self._faults.hit(FaultPoint.AFTER_REQUEST_CAS)

        if not unit.cas_update_attempt(
            current_attempt,
            updated_attempt,
        ):
            self._raise_attempt_cas(unit, current_attempt)
        self._faults.hit(FaultPoint.AFTER_ATTEMPT_CAS)

    def admit_request(
        self,
        envelope: CommandEnvelope,
        *,
        authenticated_principal: str,
        actor_authorized: bool,
        completion_contract: CompletionContract,
        policy_revision: RevisionValue,
        configuration_revision: RevisionValue,
        selected_peer_instance_id: str,
        selected_profile_id: str,
        route_decision_digest: str,
        session_id: str,
        owner_principal_id: str,
        owner_instance_id: str,
        authority_epoch: int,
        heartbeat_timeout_ms: int,
        owner_peer_id: str = "",
    ) -> tuple[
        RequestSnapshot,
        AdmissionReceipt,
        LeaseSnapshot,
    ]:
        """Atomically admit, reserve, bind identities, and emit outbox."""

        submission = validate_submission(
            envelope,
            authenticated_principal=authenticated_principal,
            completion_contract=completion_contract,
            state_changing=True,
        )
        if type(actor_authorized) is not bool:
            raise ValueError("actor_authorized must be a boolean")
        if not actor_authorized:
            raise ActorUnauthorizedError(
                authenticated_principal
            )

        admitted_at = self._clock.now()
        with self._store.unit_of_work() as unit:
            (
                existing,
                missing_client_binding,
                missing_key_binding,
            ) = self._find_idempotent_admission(
                unit,
                submission,
                created_at=admitted_at,
            )
            if existing is not None:
                aliases_added = False
                if missing_client_binding is not None:
                    unit.add_client_request_binding(
                        missing_client_binding
                    )
                    self._faults.hit(
                        FaultPoint
                        .AFTER_CLIENT_REQUEST_BINDING_WRITE
                    )
                    aliases_added = True
                if missing_key_binding is not None:
                    unit.add_command_idempotency_binding(
                        missing_key_binding
                    )
                    self._faults.hit(
                        FaultPoint
                        .AFTER_IDEMPOTENCY_BINDING_WRITE
                    )
                    aliases_added = True

                if aliases_added:
                    self._faults.hit(FaultPoint.BEFORE_COMMIT)
                    unit.commit()
                    self._faults.hit(FaultPoint.AFTER_COMMIT)
                return existing

            command_id = CommandID(
                self._ids.new_id("command")
            )
            admission_receipt_id = self._ids.new_id(
                "admission-receipt"
            )
            lease_id = self._ids.new_id("lease")
            event_id = self._ids.new_id("outbox-event")
            fencing_token = unit.allocate_fencing_token()

            (
                request,
                client_binding,
                idempotency_binding,
                receipt,
            ) = reduce_admit_request(
                submission,
                command_id=command_id,
                admission_receipt_id=admission_receipt_id,
                lease_id=lease_id,
                policy_revision=policy_revision,
                configuration_revision=configuration_revision,
                selected_peer_instance_id=(
                    selected_peer_instance_id
                ),
                selected_profile_id=selected_profile_id,
                route_decision_digest=route_decision_digest,
                admitted_at=admitted_at,
            )
            lease = reserve_lease(
                LeaseReservationRequest(
                    session_id=session_id,
                    owner_principal_id=owner_principal_id,
                    owner_instance_id=owner_instance_id,
                    heartbeat_timeout_ms=heartbeat_timeout_ms,
                    command_id=command_id,
                    authority_epoch=authority_epoch,
                    owner_peer_id=owner_peer_id,
                ),
                lease_id=lease_id,
                fencing_token=fencing_token,
                created_at=admitted_at,
            )
            event = self._dispatch_event(
                request,
                event_id=event_id,
                occurred_at=admitted_at,
            )

            unit.add_request(request)
            self._faults.hit(FaultPoint.AFTER_REQUEST_WRITE)

            unit.add_lease(lease)
            self._faults.hit(FaultPoint.AFTER_LEASE_WRITE)

            unit.add_admission_receipt(receipt)
            self._faults.hit(
                FaultPoint.AFTER_ADMISSION_RECEIPT_WRITE
            )

            unit.add_client_request_binding(client_binding)
            self._faults.hit(
                FaultPoint.AFTER_CLIENT_REQUEST_BINDING_WRITE
            )

            unit.add_command_idempotency_binding(
                idempotency_binding
            )
            self._faults.hit(
                FaultPoint.AFTER_IDEMPOTENCY_BINDING_WRITE
            )

            unit.add_outbox_event(event)
            self._faults.hit(FaultPoint.AFTER_OUTBOX_WRITE)

            self._faults.hit(FaultPoint.BEFORE_COMMIT)
            unit.commit()

        self._faults.hit(FaultPoint.AFTER_COMMIT)
        return (request, receipt, lease)

    def get_request(
        self,
        command_id: CommandID | str,
    ) -> RequestSnapshot | None:
        """Return one persisted request snapshot."""

        with self._store.unit_of_work() as unit:
            return unit.get_request(command_id)

    def reject_policy(
        self,
        command_id: CommandID | str,
        *,
        error_code: ErrorCode,
    ) -> RequestSnapshot:
        """Atomically reject an admitted request and emit terminal outbox."""

        with self._store.unit_of_work() as unit:
            current = self._require_request(unit, command_id)
            timestamp = self._clock.now()
            updated = reduce_reject_policy(
                current,
                error_code=error_code,
                updated_at=timestamp,
            )
            if not unit.cas_update_request(current, updated):
                self._raise_request_cas(unit, current)
            self._faults.hit(FaultPoint.AFTER_REQUEST_CAS)

            unit.add_outbox_event(
                self._dispatch_event(
                    updated,
                    event_id=self._ids.new_id("outbox-event"),
                    occurred_at=timestamp,
                )
            )
            self._faults.hit(FaultPoint.AFTER_OUTBOX_WRITE)
            self._faults.hit(FaultPoint.BEFORE_COMMIT)
            unit.commit()

        self._faults.hit(FaultPoint.AFTER_COMMIT)
        return updated

    def prepare_request(
        self,
        command_id: CommandID | str,
        *,
        session_key: SessionBindingKey | None = None,
    ) -> RequestSnapshot:
        """Validate persisted binding evidence and enter PREPARED."""

        with self._store.unit_of_work() as unit:
            current = self._require_request(unit, command_id)
            lease = self._require_lease(
                unit,
                current.lease_id,
            )
            binding = (
                unit.get_session_binding(session_key)
                if session_key is not None
                else None
            )
            updated = reduce_prepare_request(
                current,
                session_binding=binding,
                lease=lease,
                updated_at=self._clock.now(),
            )
            if not unit.cas_update_request(current, updated):
                self._raise_request_cas(unit, current)
            self._faults.hit(FaultPoint.AFTER_REQUEST_CAS)
            self._faults.hit(FaultPoint.BEFORE_COMMIT)
            unit.commit()

        self._faults.hit(FaultPoint.AFTER_COMMIT)
        return updated

    def create_attempt(
        self,
        command_id: CommandID | str,
    ) -> AttemptSnapshot:
        """Create the next monotonic attempt under PREPARED."""

        with self._store.unit_of_work() as unit:
            request = self._require_request(unit, command_id)
            lease = self._require_lease(
                unit,
                request.lease_id,
            )
            attempt = reduce_create_attempt(
                request,
                lease,
                attempt_id=self._ids.new_id("attempt"),
                attempt_number=unit.next_attempt_number(
                    request.command_id
                ),
                created_at=self._clock.now(),
            )
            unit.add_attempt(attempt)
            self._faults.hit(FaultPoint.AFTER_ATTEMPT_WRITE)
            self._faults.hit(FaultPoint.BEFORE_COMMIT)
            unit.commit()

        self._faults.hit(FaultPoint.AFTER_COMMIT)
        return attempt

    def fail_pre_dispatch(
        self,
        command_id: CommandID | str,
        attempt_id: str,
        *,
        error_code: ErrorCode,
    ) -> tuple[RequestSnapshot, AttemptSnapshot]:
        """Commit a proven pre-dispatch failure and terminal outbox."""

        with self._store.unit_of_work() as unit:
            request = self._require_request(unit, command_id)
            attempt = self._require_attempt(unit, attempt_id)
            timestamp = self._clock.now()
            updated_request, updated_attempt = (
                reduce_fail_pre_dispatch(
                    request,
                    attempt,
                    error_code=error_code,
                    updated_at=timestamp,
                )
            )
            self._cas_request_attempt(
                unit,
                request,
                updated_request,
                attempt,
                updated_attempt,
            )
            unit.add_outbox_event(
                self._dispatch_event(
                    updated_request,
                    event_id=self._ids.new_id("outbox-event"),
                    occurred_at=timestamp,
                )
            )
            self._faults.hit(FaultPoint.AFTER_OUTBOX_WRITE)
            self._faults.hit(FaultPoint.BEFORE_COMMIT)
            unit.commit()

        self._faults.hit(FaultPoint.AFTER_COMMIT)
        return (updated_request, updated_attempt)

    def record_dispatch_intent(
        self,
        command_id: CommandID | str,
        attempt_id: str,
    ) -> tuple[
        RequestSnapshot,
        AttemptSnapshot,
        LeaseSnapshot,
    ]:
        """Commit replay boundary and bind the lease attempt ID."""

        with self._store.unit_of_work() as unit:
            request = self._require_request(unit, command_id)
            attempt = self._require_attempt(unit, attempt_id)
            lease = self._require_lease(
                unit,
                request.lease_id,
            )
            (
                updated_request,
                updated_attempt,
                updated_lease,
            ) = reduce_dispatch_intent(
                request,
                attempt,
                lease,
                updated_at=self._clock.now(),
            )
            if not unit.cas_update_dispatch_bundle(
                request,
                updated_request,
                attempt,
                updated_attempt,
                lease,
                updated_lease,
            ):
                raise InvalidMutationError(
                    "dispatch-intent bundle CAS failed"
                )
            self._faults.hit(
                FaultPoint.AFTER_DISPATCH_BUNDLE_CAS
            )
            self._faults.hit(FaultPoint.BEFORE_COMMIT)
            unit.commit()

        self._faults.hit(FaultPoint.AFTER_COMMIT)
        return (
            updated_request,
            updated_attempt,
            updated_lease,
        )

    def record_start_uncertain(
        self,
        command_id: CommandID | str,
        attempt_id: str,
    ) -> tuple[RequestSnapshot, AttemptSnapshot]:
        """Commit START_UNCERTAIN without claiming process identity."""

        with self._store.unit_of_work() as unit:
            request = self._require_request(unit, command_id)
            attempt = self._require_attempt(unit, attempt_id)
            updated_request, updated_attempt = (
                reduce_start_uncertain(
                    request,
                    attempt,
                    updated_at=self._clock.now(),
                )
            )
            self._cas_request_attempt(
                unit,
                request,
                updated_request,
                attempt,
                updated_attempt,
            )
            self._faults.hit(FaultPoint.BEFORE_COMMIT)
            unit.commit()

        self._faults.hit(FaultPoint.AFTER_COMMIT)
        return (updated_request, updated_attempt)

    def record_running(
        self,
        command_id: CommandID | str,
        attempt_id: str,
        *,
        process_identity: ProcessBirthIdentity,
    ) -> tuple[
        RequestSnapshot,
        AttemptSnapshot,
        LeaseSnapshot,
    ]:
        """Bind process-birth identity and atomically enter RUNNING."""

        with self._store.unit_of_work() as unit:
            request = self._require_request(unit, command_id)
            attempt = self._require_attempt(unit, attempt_id)
            lease = self._require_lease(
                unit,
                request.lease_id,
            )
            (
                updated_request,
                updated_attempt,
                updated_lease,
            ) = reduce_running(
                request,
                attempt,
                lease,
                process_identity=process_identity,
                updated_at=self._clock.now(),
            )
            if not unit.cas_update_dispatch_bundle(
                request,
                updated_request,
                attempt,
                updated_attempt,
                lease,
                updated_lease,
            ):
                raise InvalidMutationError(
                    "RUNNING bundle CAS failed"
                )
            self._faults.hit(
                FaultPoint.AFTER_DISPATCH_BUNDLE_CAS
            )
            self._faults.hit(FaultPoint.BEFORE_COMMIT)
            unit.commit()

        self._faults.hit(FaultPoint.AFTER_COMMIT)
        return (
            updated_request,
            updated_attempt,
            updated_lease,
        )

    def begin_cancellation(
        self,
        command_id: CommandID | str,
        attempt_id: str,
    ) -> tuple[RequestSnapshot, AttemptSnapshot]:
        """Persist CANCELLING without performing process cancellation."""

        with self._store.unit_of_work() as unit:
            request = self._require_request(unit, command_id)
            attempt = self._require_attempt(unit, attempt_id)
            updated_request, updated_attempt = (
                reduce_begin_cancellation(
                    request,
                    attempt,
                    updated_at=self._clock.now(),
                )
            )
            self._cas_request_attempt(
                unit,
                request,
                updated_request,
                attempt,
                updated_attempt,
            )
            self._faults.hit(FaultPoint.BEFORE_COMMIT)
            unit.commit()

        self._faults.hit(FaultPoint.AFTER_COMMIT)
        return (updated_request, updated_attempt)

    def begin_assessment(
        self,
        command_id: CommandID | str,
        attempt_id: str,
    ) -> tuple[RequestSnapshot, AttemptSnapshot]:
        """Persist ASSESSING from injected terminal process evidence."""

        with self._store.unit_of_work() as unit:
            request = self._require_request(unit, command_id)
            attempt = self._require_attempt(unit, attempt_id)
            updated_request, updated_attempt = (
                reduce_begin_assessment(
                    request,
                    attempt,
                    updated_at=self._clock.now(),
                )
            )
            self._cas_request_attempt(
                unit,
                request,
                updated_request,
                attempt,
                updated_attempt,
            )
            self._faults.hit(FaultPoint.BEFORE_COMMIT)
            unit.commit()

        self._faults.hit(FaultPoint.AFTER_COMMIT)
        return (updated_request, updated_attempt)

    def complete_attempt(
        self,
        command_id: CommandID | str,
        attempt_id: str,
        *,
        result: AskResult,
    ) -> tuple[RequestSnapshot, AttemptSnapshot]:
        """Commit the derived terminal state and canonical outbox event."""

        with self._store.unit_of_work() as unit:
            request = self._require_request(unit, command_id)
            attempt = self._require_attempt(unit, attempt_id)
            timestamp = self._clock.now()
            updated_request, updated_attempt = (
                reduce_complete_attempt(
                    request,
                    attempt,
                    result=result,
                    updated_at=timestamp,
                )
            )
            self._cas_request_attempt(
                unit,
                request,
                updated_request,
                attempt,
                updated_attempt,
            )
            unit.add_outbox_event(
                self._dispatch_event(
                    updated_request,
                    event_id=self._ids.new_id("outbox-event"),
                    occurred_at=timestamp,
                )
            )
            self._faults.hit(FaultPoint.AFTER_OUTBOX_WRITE)
            self._faults.hit(FaultPoint.BEFORE_COMMIT)
            unit.commit()

        self._faults.hit(FaultPoint.AFTER_COMMIT)
        return (updated_request, updated_attempt)

    def authorize_retry(
        self,
        command_id: CommandID | str,
        previous_attempt_id: str,
        *,
        reconciliation_complete: bool,
        heartbeat_timeout_ms: int,
    ) -> tuple[
        RequestSnapshot,
        AttemptSnapshot,
        LeaseSnapshot,
    ]:
        """Atomically rotate to a fresh RESERVED lease for a retry."""

        with self._store.unit_of_work() as unit:
            request = self._require_request(unit, command_id)
            previous_attempt = self._require_attempt(
                unit,
                previous_attempt_id,
            )
            current_lease = self._require_lease(
                unit,
                request.lease_id,
            )
            timestamp = self._clock.now()

            new_lease = reserve_lease(
                LeaseReservationRequest(
                    session_id=current_lease.session_id,
                    owner_principal_id=(
                        current_lease.fence.owner_principal_id
                    ),
                    owner_instance_id=(
                        current_lease.fence.owner_instance_id
                    ),
                    heartbeat_timeout_ms=heartbeat_timeout_ms,
                    command_id=request.command_id,
                    authority_epoch=(
                        current_lease.fence.authority_epoch
                    ),
                    owner_peer_id=(
                        current_lease.fence.owner_peer_id
                    ),
                ),
                lease_id=self._ids.new_id("lease"),
                fencing_token=unit.allocate_fencing_token(),
                created_at=timestamp,
            )
            updated_request, updated_attempt = (
                reduce_authorize_retry(
                    request,
                    previous_attempt,
                    new_lease,
                    reconciliation_complete=(
                        reconciliation_complete
                    ),
                    updated_at=timestamp,
                )
            )

            unit.add_lease(new_lease)
            self._faults.hit(FaultPoint.AFTER_LEASE_WRITE)

            if not unit.cas_update_request(
                request,
                updated_request,
            ):
                self._raise_request_cas(unit, request)
            self._faults.hit(FaultPoint.AFTER_REQUEST_CAS)

            if updated_attempt != previous_attempt:
                if not unit.cas_update_attempt(
                    previous_attempt,
                    updated_attempt,
                ):
                    self._raise_attempt_cas(
                        unit,
                        previous_attempt,
                    )
                self._faults.hit(FaultPoint.AFTER_ATTEMPT_CAS)

            self._faults.hit(FaultPoint.BEFORE_COMMIT)
            unit.commit()

        self._faults.hit(FaultPoint.AFTER_COMMIT)
        return (
            updated_request,
            updated_attempt,
            new_lease,
        )

    def create_session_and_lease(
        self,
        key: SessionBindingKey,
        lease_request: LeaseCreateRequest,
        adapter_fingerprint: str,
        readiness_binding: str,
        session_generation: int = 1,
    ) -> tuple[SessionBindingSnapshot, LeaseSnapshot]:
        """Atomically create a session binding and active lease."""

        timestamp = self._clock.now()
        lease_id = self._ids.new_id("lease")

        with self._store.unit_of_work() as unit:
            existing = unit.get_session_binding(key)
            if existing is not None:
                raise InvalidMutationError(
                    "session binding already exists for key"
                )

            lease = create_lease(
                lease_request,
                lease_id=lease_id,
                created_at=timestamp,
                fencing_token=unit.allocate_fencing_token(),
            )
            binding = create_session_binding(
                key,
                session_id=lease_request.session_id,
                current_lease_id=lease_id,
                adapter_fingerprint=adapter_fingerprint,
                readiness_binding=readiness_binding,
                session_generation=session_generation,
                created_at=timestamp,
            )

            unit.add_lease(lease)
            self._faults.hit(FaultPoint.AFTER_LEASE_WRITE)

            unit.add_session_binding(binding)
            self._faults.hit(
                FaultPoint.AFTER_SESSION_BINDING_WRITE
            )

            self._faults.hit(FaultPoint.BEFORE_COMMIT)
            unit.commit()

        self._faults.hit(FaultPoint.AFTER_COMMIT)
        return (binding, lease)

    def resume_session(
        self,
        request: SessionResumeRequest,
    ) -> tuple[bool, SessionBindingSnapshot]:
        """Validate compatibility with the current session binding."""

        timestamp = self._clock.now()
        with self._store.unit_of_work() as unit:
            binding = unit.get_session_binding(request.key)
            if binding is None:
                raise RecordNotFoundError(
                    "session_binding",
                    f"{request.key.instance_id}/"
                    f"{request.key.profile_id}",
                )

            is_compatible, updated = resume_session_binding(
                binding,
                request,
                updated_at=timestamp,
            )

            if not is_compatible:
                if not unit.cas_update_session_binding(
                    binding,
                    updated,
                ):
                    latest = unit.get_session_binding(request.key)
                    if latest is None:
                        raise RecordNotFoundError(
                            "session_binding",
                            f"{request.key.instance_id}/"
                            f"{request.key.profile_id}",
                        )
                    target_id = "/".join(
                        (
                            request.key.workspace_scope_id,
                            request.key.instance_id,
                            request.key.profile_id,
                            request.key.conversation_scope,
                        )
                    )
                    raise StaleRevisionError(
                        target_id,
                        binding.revision,
                        latest.revision,
                    )

            self._faults.hit(FaultPoint.BEFORE_COMMIT)
            unit.commit()

        self._faults.hit(FaultPoint.AFTER_COMMIT)
        return (is_compatible, updated)

    def renew_lease(
        self,
        request: LeaseRenewRequest,
        *,
        heartbeat_timeout_ms: int,
    ) -> LeaseSnapshot:
        """Renew an active lease in state storage."""

        timestamp = self._clock.now()
        with self._store.unit_of_work() as unit:
            current = unit.get_lease(request.lease_id)
            if current is None:
                raise RecordNotFoundError(
                    "lease",
                    request.lease_id,
                )

            updated = renew_lease(
                current,
                request,
                heartbeat_timeout_ms=heartbeat_timeout_ms,
                updated_at=timestamp,
            )

            if not unit.cas_update_lease(current, updated):
                latest = unit.get_lease(request.lease_id)
                raise InvalidMutationError(
                    f"CAS failure renewing lease "
                    f"{request.lease_id} "
                    f"(expected rev {current.fence.revision}, "
                    f"found "
                    f"{latest.fence.revision if latest else 'none'})"
                )
            self._faults.hit(FaultPoint.AFTER_LEASE_CAS)

            self._faults.hit(FaultPoint.BEFORE_COMMIT)
            unit.commit()

        self._faults.hit(FaultPoint.AFTER_COMMIT)
        return updated

    def close_lease(
        self,
        request: LeaseCloseRequest,
    ) -> LeaseSnapshot:
        """Close an active lease in state storage."""

        timestamp = self._clock.now()
        with self._store.unit_of_work() as unit:
            current = unit.get_lease(request.lease_id)
            if current is None:
                raise RecordNotFoundError(
                    "lease",
                    request.lease_id,
                )

            updated = close_lease(
                current,
                request,
                updated_at=timestamp,
            )

            if not unit.cas_update_lease(current, updated):
                raise InvalidMutationError(
                    f"CAS failure closing lease "
                    f"{request.lease_id}"
                )
            self._faults.hit(FaultPoint.AFTER_LEASE_CAS)

            self._faults.hit(FaultPoint.BEFORE_COMMIT)
            unit.commit()

        self._faults.hit(FaultPoint.AFTER_COMMIT)
        return updated

    def check_lease_fence(
        self,
        request: LeaseFenceCheckRequest,
    ) -> tuple[bool, tuple[str, ...]]:
        """Validate a requester fence against persisted lease state."""

        with self._store.unit_of_work() as unit:
            current = unit.get_lease(
                request.requester_fence.lease_id
            )
            if current is None:
                return (False, ("lease_id_not_found",))
            return validate_lease_fence(
                current.fence,
                request.requester_fence,
            )

    def recover_lease(
        self,
        lease_id: str,
        *,
        recovery_actor_principal_id: str,
        trigger: RecoveryTrigger,
        evidence_digest: str,
        policy_id: str,
        policy_revision: int,
        is_process_alive: bool,
        process_identity_matches: bool,
    ) -> tuple[LeaseSnapshot, RecoveryReceipt]:
        """Atomically fence a lease and record its recovery receipt."""

        timestamp = self._clock.now()
        receipt_id = self._ids.new_id("recovery-receipt")

        with self._store.unit_of_work() as unit:
            current = unit.get_lease(lease_id)
            if current is None:
                raise RecordNotFoundError("lease", lease_id)

            updated_lease, receipt = expire_and_recover_lease(
                current,
                recovery_receipt_id=receipt_id,
                recovery_actor_principal_id=(
                    recovery_actor_principal_id
                ),
                trigger=trigger,
                evidence_digest=evidence_digest,
                policy_id=policy_id,
                policy_revision=policy_revision,
                detected_at=timestamp,
                is_process_alive=is_process_alive,
                process_identity_matches=process_identity_matches,
            )

            if not unit.cas_update_lease(
                current,
                updated_lease,
            ):
                raise InvalidMutationError(
                    f"CAS failure recovering lease {lease_id}"
                )
            self._faults.hit(FaultPoint.AFTER_LEASE_CAS)

            unit.add_recovery_receipt(receipt)
            self._faults.hit(
                FaultPoint.AFTER_RECOVERY_RECEIPT_WRITE
            )

            self._faults.hit(FaultPoint.BEFORE_COMMIT)
            unit.commit()

        self._faults.hit(FaultPoint.AFTER_COMMIT)
        return (updated_lease, receipt)
