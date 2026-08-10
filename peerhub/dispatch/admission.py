from __future__ import annotations

from peerhub.core.context import Clock, IdSource
from peerhub.core.errors import DuplicateClientRequestError, IdempotencyPayloadMismatchError
from peerhub.core.protocol import CommandEnvelope, CommandID, ErrorCode, RevisionValue
from peerhub.state.contract import StateStore

from .contract import (
    AdmissionReceipt,
    ClientRequestBinding,
    CommandIdempotencyBinding,
    CompletionContract,
    LeaseSnapshot,
    RequestSnapshot,
    SessionBindingKey,
    ValidatedSubmission,
)
from .capability import CapabilityTier
from .helpers import (
    dispatch_event,
    raise_request_cas,
    require_actor_authorized,
    require_lease,
    require_request,
)
from .model import (
    admit_request as reduce_admit_request,
    prepare_request as reduce_prepare_request,
    reject_request_policy as reduce_reject_policy,
    reserve_lease,
    validate_submission,
)
from .unit_of_work import DispatchUnitOfWork, FaultInjector, FaultPoint, _NoFaultInjector  # pyright: ignore[reportPrivateUsage]


class AdmissionCoordinator:
    """Orchestrate Phase 1 admission, validation, and idempotency."""

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
            raise RuntimeError("idempotency binding references a missing request")
        receipt = unit.get_admission_receipt(admission_receipt_id)
        if receipt is None:
            raise RuntimeError(
                "idempotency binding references a missing admission receipt"
            )
        lease = unit.get_lease(receipt.lease_id)
        if lease is None:
            raise RuntimeError("admission receipt references a missing lease")
        if (
            request.command_id != command_id
            or receipt.command_id != command_id
            or request.lease_id != receipt.lease_id
            or lease.fence.command_id != command_id
        ):
            raise RuntimeError("stored admission records are internally inconsistent")
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
            and client_binding.payload_digest != submission.payload_digest
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
                and key_binding.payload_digest != submission.payload_digest
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
                client_binding.command_id != key_binding.command_id
                or client_binding.admission_receipt_id != key_binding.admission_receipt_id
            )
        ):
            raise RuntimeError("client-request and idempotency bindings disagree")

        if client_binding is None and key_binding is None:
            return (None, None, None)

        if client_binding is not None:
            command_id = client_binding.command_id
            admission_receipt_id = client_binding.admission_receipt_id
        else:
            if key_binding is None:
                raise AssertionError("idempotency binding selection is unreachable")
            command_id = key_binding.command_id
            admission_receipt_id = key_binding.admission_receipt_id

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
        if key_binding is None and envelope.idempotency_key is not None:
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

    def peek_idempotent_admission(
        self,
        envelope: CommandEnvelope,
        *,
        authenticated_principal: str,
        actor_authorized: bool,
        completion_contract: CompletionContract,
    ) -> tuple[
        RequestSnapshot,
        AdmissionReceipt,
        LeaseSnapshot,
    ] | None:
        require_actor_authorized(actor_authorized, authenticated_principal)
        submission = validate_submission(
            envelope,
            authenticated_principal=authenticated_principal,
            completion_contract=completion_contract,
            state_changing=True,
        )

        with self._store.unit_of_work() as unit:
            (
                existing,
                missing_client_binding,
                missing_key_binding,
            ) = self._find_idempotent_admission(
                unit,
                submission,
                created_at=self._clock.now(),
            )
            if existing is None:
                return None

            aliases_added = False
            if missing_client_binding is not None:
                unit.add_client_request_binding(missing_client_binding)
                self._faults.hit(FaultPoint.AFTER_CLIENT_REQUEST_BINDING_WRITE)
                aliases_added = True
            if missing_key_binding is not None:
                unit.add_command_idempotency_binding(missing_key_binding)
                self._faults.hit(FaultPoint.AFTER_IDEMPOTENCY_BINDING_WRITE)
                aliases_added = True

            if aliases_added:
                self._faults.hit(FaultPoint.BEFORE_COMMIT)
                unit.commit()
                self._faults.hit(FaultPoint.AFTER_COMMIT)
            return existing

    def admit_request(
        self,
        envelope: CommandEnvelope,
        *,
        authenticated_principal: str,
        actor_authorized: bool,
        completion_contract: CompletionContract,
        policy_revision: RevisionValue,
        configuration_revision: RevisionValue,
        required_capability_tier: CapabilityTier,
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
        require_actor_authorized(actor_authorized, authenticated_principal)
        submission = validate_submission(
            envelope,
            authenticated_principal=authenticated_principal,
            completion_contract=completion_contract,
            state_changing=True,
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
                if (
                    existing[0].required_capability_tier
                    is not required_capability_tier
                ):
                    raise IdempotencyPayloadMismatchError(
                        envelope.client_id,
                        envelope.method,
                        envelope.idempotency_key or "",
                    )
                aliases_added = False
                if missing_client_binding is not None:
                    unit.add_client_request_binding(missing_client_binding)
                    self._faults.hit(FaultPoint.AFTER_CLIENT_REQUEST_BINDING_WRITE)
                    aliases_added = True
                if missing_key_binding is not None:
                    unit.add_command_idempotency_binding(missing_key_binding)
                    self._faults.hit(FaultPoint.AFTER_IDEMPOTENCY_BINDING_WRITE)
                    aliases_added = True

                if aliases_added:
                    self._faults.hit(FaultPoint.BEFORE_COMMIT)
                    unit.commit()
                    self._faults.hit(FaultPoint.AFTER_COMMIT)
                return existing

            command_id = CommandID(self._ids.new_id("command"))
            admission_receipt_id = self._ids.new_id("admission-receipt")
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
                required_capability_tier=required_capability_tier,
                selected_peer_instance_id=selected_peer_instance_id,
                selected_profile_id=selected_profile_id,
                route_decision_digest=route_decision_digest,
                admitted_at=admitted_at,
            )
            
            from .contract import LeaseReservationRequest
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
            event = dispatch_event(
                request,
                event_id=event_id,
                occurred_at=admitted_at,
            )

            unit.add_request(request)
            self._faults.hit(FaultPoint.AFTER_REQUEST_WRITE)

            unit.add_lease(lease)
            self._faults.hit(FaultPoint.AFTER_LEASE_WRITE)

            unit.add_admission_receipt(receipt)
            self._faults.hit(FaultPoint.AFTER_ADMISSION_RECEIPT_WRITE)

            unit.add_client_request_binding(client_binding)
            self._faults.hit(FaultPoint.AFTER_CLIENT_REQUEST_BINDING_WRITE)

            unit.add_command_idempotency_binding(idempotency_binding)
            self._faults.hit(FaultPoint.AFTER_IDEMPOTENCY_BINDING_WRITE)

            unit.add_outbox_event(event)
            self._faults.hit(FaultPoint.AFTER_OUTBOX_WRITE)

            self._faults.hit(FaultPoint.BEFORE_COMMIT)
            unit.commit()

        self._faults.hit(FaultPoint.AFTER_COMMIT)
        return (request, receipt, lease)

    def reject_policy(
        self,
        command_id: CommandID | str,
        *,
        error_code: ErrorCode,
    ) -> RequestSnapshot:
        with self._store.unit_of_work() as unit:
            current = require_request(unit, command_id)
            timestamp = self._clock.now()
            updated = reduce_reject_policy(
                current,
                error_code=error_code,
                updated_at=timestamp,
            )
            if not unit.cas_update_request(current, updated):
                raise_request_cas(unit, current)
            self._faults.hit(FaultPoint.AFTER_REQUEST_CAS)

            unit.add_outbox_event(
                dispatch_event(
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
        with self._store.unit_of_work() as unit:
            current = require_request(unit, command_id)
            lease = require_lease(unit, current.lease_id)
            binding = (
                unit.get_session_binding(session_key) if session_key is not None else None
            )
            updated = reduce_prepare_request(
                current,
                session_binding=binding,
                lease=lease,
                updated_at=self._clock.now(),
            )
            if not unit.cas_update_request(current, updated):
                raise_request_cas(unit, current)
            self._faults.hit(FaultPoint.AFTER_REQUEST_CAS)
            self._faults.hit(FaultPoint.BEFORE_COMMIT)
            unit.commit()

        self._faults.hit(FaultPoint.AFTER_COMMIT)
        return updated
