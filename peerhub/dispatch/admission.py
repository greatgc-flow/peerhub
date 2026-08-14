from __future__ import annotations

from peerhub.core.context import Clock, IdSource
from peerhub.core.errors import DuplicateClientRequestError, IdempotencyPayloadMismatchError
from peerhub.core.identity import (
    AuthenticatedSubject,
    require_authenticated_subject,
)
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
from .capability import (
    CapabilityLease,
    CapabilityLeaseViolation,
    CapabilityPolicy,
    CapabilityTier,
    PeerEnforcementEvidenceProvider,
    require_enforcement_floor,
    validate_capability_binding,
)
from .capability_policy import (
    default_capability_policy,
    default_enforcement_evidence_provider,
)
from .helpers import (
    dispatch_event,
    raise_request_cas,
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
        capability_policy: CapabilityPolicy | None = None,
        enforcement_evidence: PeerEnforcementEvidenceProvider | None = None,
    ) -> None:
        self._store = store
        self._clock = clock
        self._ids = ids
        self._faults = fault_injector or _NoFaultInjector()
        self._capability_policy: CapabilityPolicy = (
            capability_policy
            if capability_policy is not None
            else default_capability_policy()
        )
        self._enforcement_evidence: PeerEnforcementEvidenceProvider = (
            enforcement_evidence
            if enforcement_evidence is not None
            else default_enforcement_evidence_provider()
        )

    def _load_admission(
        self,
        unit: DispatchUnitOfWork,
        *,
        command_id: CommandID,
        admission_receipt_id: str,
    ) -> tuple[
        RequestSnapshot,
        AdmissionReceipt,
        LeaseSnapshot,
        CapabilityLease,
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

        # Errata 7.1/7.2: replay follows the request's current session lease.
        # It never mints or accepts a replacement, and static binding
        # corruption is fatal rather than repairable.
        capability_lease = unit.get_capability_lease_by_session_lease_id(
            lease.lease_id
        )
        if capability_lease is None:
            raise CapabilityLeaseViolation(
                "admitted request has no durable capability lease"
            )
        evidence = self._enforcement_evidence.resolve(
            peer_instance_id=request.selected_peer_instance_id,
            profile_id=request.selected_profile_id,
        )
        validate_capability_binding(
            request,
            receipt,
            lease,
            capability_lease,
            expected_peer_kind=evidence.peer_kind,
        )
        if capability_lease.admission_receipt_id != admission_receipt_id:
            raise CapabilityLeaseViolation(
                "capability lease is bound to a different admission receipt"
            )
        return (request, receipt, lease, capability_lease)

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
            CapabilityLease,
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
        authenticated_subject: AuthenticatedSubject,
        completion_contract: CompletionContract,
    ) -> tuple[
        RequestSnapshot,
        AdmissionReceipt,
        LeaseSnapshot,
        CapabilityLease,
    ] | None:
        authenticated_subject = require_authenticated_subject(
            authenticated_subject
        )
        submission = validate_submission(
            envelope,
            authenticated_principal=authenticated_subject.principal_id,
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
        authenticated_subject: AuthenticatedSubject,
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
        CapabilityLease,
    ]:
        authenticated_subject = require_authenticated_subject(
            authenticated_subject
        )
        submission = validate_submission(
            envelope,
            authenticated_principal=authenticated_subject.principal_id,
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

            capability_lease = self._issue_capability_lease(
                request,
                receipt,
                lease,
                issued_at=admitted_at,
            )

            unit.add_request(request)
            self._faults.hit(FaultPoint.AFTER_REQUEST_WRITE)

            unit.add_lease(lease)
            self._faults.hit(FaultPoint.AFTER_LEASE_WRITE)

            unit.add_admission_receipt(receipt)
            self._faults.hit(FaultPoint.AFTER_ADMISSION_RECEIPT_WRITE)

            # Written inside the existing admission transaction so a fault
            # before commit leaves none of the four records durable
            # (errata 7.1 point 7).  Ordered after its three foreign-key
            # parents in migration 0018.
            unit.add_capability_lease(capability_lease)
            self._faults.hit(FaultPoint.AFTER_CAPABILITY_LEASE_WRITE)

            unit.add_client_request_binding(client_binding)
            self._faults.hit(FaultPoint.AFTER_CLIENT_REQUEST_BINDING_WRITE)

            unit.add_command_idempotency_binding(idempotency_binding)
            self._faults.hit(FaultPoint.AFTER_IDEMPOTENCY_BINDING_WRITE)

            unit.add_outbox_event(event)
            self._faults.hit(FaultPoint.AFTER_OUTBOX_WRITE)

            self._faults.hit(FaultPoint.BEFORE_COMMIT)
            unit.commit()

        self._faults.hit(FaultPoint.AFTER_COMMIT)
        return (request, receipt, lease, capability_lease)

    def _issue_capability_lease(
        self,
        request: RequestSnapshot,
        receipt: AdmissionReceipt,
        session_lease: LeaseSnapshot,
        *,
        issued_at: int,
    ) -> CapabilityLease:
        """Authorize and mint the capability lease for a fresh admission.

        Implements errata Section 7.1 points 1-6 in order: machine-owned
        evidence resolution, the mandatory enforcement floor, the policy grant
        decision, then a least-privilege lease.  Raises
        ``CapabilityLeaseViolation`` before anything is written when the target
        cannot meet the floor or the policy denies the grant.
        """

        required_tier = request.required_capability_tier
        evidence = self._enforcement_evidence.resolve(
            peer_instance_id=request.selected_peer_instance_id,
            profile_id=request.selected_profile_id,
        )
        floor = require_enforcement_floor(
            evidence.peer_kind,
            required_tier,
            evidence,
        )
        decision = self._capability_policy.decide(
            subject_principal_id=request.authenticated_principal,
            selected_peer_kind=evidence.peer_kind,
            selected_peer_instance_id=request.selected_peer_instance_id,
            selected_profile_id=request.selected_profile_id,
            policy_revision=request.policy_revision,
            required_tier=required_tier,
            minimum_enforcement=floor,
        )
        if not decision.granted:
            raise CapabilityLeaseViolation(
                decision.denial_reason or "capability grant denied by policy"
            )
        minimum_enforcement = decision.minimum_enforcement
        if minimum_enforcement is None or minimum_enforcement < floor:
            # Policy may raise the code-owned floor but never lower it.
            raise CapabilityLeaseViolation(
                "policy minimum enforcement is below the mandatory floor"
            )

        capability_lease = CapabilityLease(
            capability_lease_id=self._ids.new_id("capability-lease"),
            command_id=request.command_id,
            admission_receipt_id=receipt.admission_receipt_id,
            session_lease_id=request.lease_id,
            subject_principal_id=request.authenticated_principal,
            selected_peer_kind=evidence.peer_kind,
            required_tier=required_tier,
            # Least privilege: the authorized tier is the required tier even
            # when policy would permit more (errata 7.1 point 6).
            authorized_tier=required_tier,
            minimum_enforcement=minimum_enforcement,
            selected_peer_instance_id=request.selected_peer_instance_id,
            selected_profile_id=request.selected_profile_id,
            route_decision_digest=request.route_decision_digest,
            policy_revision=request.policy_revision,
            issuer_id=decision.issuer_id,
            issued_at=issued_at,
            expires_at=self._capability_policy.expires_at(issued_at),
        )
        # Errata 7.2 point 1: validate the constructed four before the first
        # insert, using the same validator replay and dispatch reuse.
        validate_capability_binding(
            request,
            receipt,
            session_lease,
            capability_lease,
            expected_peer_kind=evidence.peer_kind,
        )
        return capability_lease

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
