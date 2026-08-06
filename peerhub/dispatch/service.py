"""Transactional request, attempt, session, lease, and outbox orchestration.

Slice 3 performs only deterministic persistence orchestration around pure
reducers. It does not spawn processes, contact providers, enforce live
deadlines or output limits, perform cancellation, or sweep orphaned
dispatch intents. Those behaviors remain outside this Phase 1 slice.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from peerhub.core.context import Clock, IdSource
from peerhub.core.execution import ExecutionCertainty
from peerhub.core.errors import (
    ActorUnauthorizedError,
    DuplicateClientRequestError,
    IdempotencyPayloadMismatchError,
    InvalidMutationError,
    RecordNotFoundError,
    StaleRevisionError,
)
from peerhub.core.protocol import (
    ATTEMPT_TERMINAL_OBSERVED_EVENT_KIND,
    PROTOCOL_MAJOR,
    PROTOCOL_MINOR,
    SCHEMA_VERSION,
    AttemptTerminalObserved,
    CommandEnvelope,
    CommandID,
    ErrorCode,
    OperationalFailureCategory,
    RevisionValue,
)
from peerhub.governance.contract import OutboxEvent, OutboxState
from peerhub.state.contract import StateStore, UnitOfWork

from .contract import (
    AdmissionReceipt,
    ArtifactManifestRecord,
    ArtifactMetadata,
    AskResult,
    AttemptSnapshot,
    ClientRequestBinding,
    CommandIdempotencyBinding,
    CompletionContract,
    ExecutionOutcome,
    LeaseCloseRequest,
    LeaseCreateRequest,
    LeaseFenceCheckRequest,
    LeaseFenceTuple,
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
from .process import (
    InterruptedAttemptRecoveryOutcome,
    TerminalClassification,
)


def recover_interrupted_attempt(
    *,
    journal_entries: Sequence[str],
    journal_digest: str,
) -> InterruptedAttemptRecoveryOutcome:
    """Recover one interrupted attempt from its durable journal.

    Currently ratifies ONLY the post-intent crash recovery shape: journal_entries
    containing INTENT_PERSISTED with no later execution or terminal evidence.
    All other shapes fail closed by raising InvalidMutationError.
    """

    if not journal_entries:
        raise InvalidMutationError("journal_entries cannot be empty")

    for entry in journal_entries:
        if not isinstance(entry, str):
            raise InvalidMutationError("journal_entries must contain string entries")

    if "INTENT_PERSISTED" not in journal_entries:
        raise InvalidMutationError(
            "unsupported journal entries shape: missing INTENT_PERSISTED"
        )

    entries_list = list(journal_entries)
    intent_index = entries_list.index("INTENT_PERSISTED")
    later_entries = entries_list[intent_index + 1 :]

    if later_entries:
        raise InvalidMutationError(
            "unsupported journal entries shape: post-intent evidence present"
        )

    earlier_entries = entries_list[:intent_index]
    for entry in earlier_entries:
        if entry in ("SPAWNED", "EXIT"):
            raise InvalidMutationError(
                "unsupported journal entries shape: malformed pre-intent entries"
            )

    return InterruptedAttemptRecoveryOutcome(
        terminal_classification=TerminalClassification.START_UNCERTAIN,
        execution_outcome=ExecutionOutcome(
            started=False,
            exit_code=None,
            timed_out=False,
            cancelled=False,
            execution_certainty=ExecutionCertainty.MAY_HAVE_STARTED,
        ),
        automatic_replay_authorized=False,
        journal_digest=journal_digest,
    )


from .admission import AdmissionCoordinator
from .attempt_lifecycle import AttemptLifecycleCoordinator
from .helpers import (
    attempt_terminal_event as _attempt_terminal_event,
    cas_request_attempt as _cas_request_attempt,
    dispatch_event as _dispatch_event,
    raise_attempt_cas as _raise_attempt_cas,
    raise_request_cas as _raise_request_cas,
    require_actor_authorized as _require_actor_authorized,
    require_attempt as _require_attempt,
    require_lease as _require_lease,
    require_request as _require_request,
)
from .unit_of_work import DispatchUnitOfWork, FaultPoint, FaultInjector, _NoFaultInjector

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
        self._admission = AdmissionCoordinator(store=store, clock=clock, ids=ids, fault_injector=fault_injector)
        self._attempts = AttemptLifecycleCoordinator(store=store, clock=clock, ids=ids, fault_injector=fault_injector)
    def now(self) -> int:
        """Return the current timestamp from the configured clock."""
        return self._clock.now()

    def record_artifact_manifest(
        self,
        manifest_record: ArtifactManifestRecord,
        item_records: Sequence[ArtifactMetadata],
    ) -> None:
        """Persist an artifact manifest and item metadata records."""
        with self._store.unit_of_work() as unit:
            unit.add_artifact_manifest(
                manifest_record,
                tuple(item_records),
            )
            unit.commit()

    def mark_artifacts_orphaned_if_manifest_exists(
        self,
        attempt_id: str,
        *,
        failure_code: str,
    ) -> bool:
        """Mark artifacts orphaned for an attempt if an artifact manifest exists."""
        timestamp = self._clock.now()
        with self._store.unit_of_work() as unit:
            manifest_row = unit.get_artifact_manifest(attempt_id)
            if manifest_row is None:
                return False
            unit.mark_artifacts_orphaned(
                attempt_id=attempt_id,
                expected_manifest_revision=manifest_row.revision,
                orphaned_at=timestamp,
                failure_code=failure_code,
            )
            unit.commit()
            return True

    def get_lease(self, lease_id: str) -> LeaseSnapshot | None:
        """Retrieve a lease snapshot by ID, if found."""
        with self._store.unit_of_work() as unit:
            return unit.get_lease(lease_id)

    def get_request_and_attempt(
        self,
        command_id: CommandID | str,
        attempt_id: str,
    ) -> tuple[RequestSnapshot, AttemptSnapshot]:
        """Retrieve current request and attempt snapshots."""
        with self._store.unit_of_work() as unit:
            req = _require_request(unit, command_id)
            att = _require_attempt(unit, attempt_id)
            return req, att

    def peek_idempotent_admission(
        self,
        envelope: CommandEnvelope,
        *,
        authenticated_principal: str,
        actor_authorized: bool,
        completion_contract: CompletionContract,
    ) -> tuple[RequestSnapshot, AdmissionReceipt, LeaseSnapshot] | None:
        """Return an existing idempotent admission, if one exists."""
        return self._admission.peek_idempotent_admission(
            envelope,
            authenticated_principal=authenticated_principal,
            actor_authorized=actor_authorized,
            completion_contract=completion_contract,
        )

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
    ) -> tuple[RequestSnapshot, AdmissionReceipt, LeaseSnapshot]:
        """Atomically admit, reserve, bind identities, and emit outbox."""
        return self._admission.admit_request(
            envelope,
            authenticated_principal=authenticated_principal,
            actor_authorized=actor_authorized,
            completion_contract=completion_contract,
            policy_revision=policy_revision,
            configuration_revision=configuration_revision,
            selected_peer_instance_id=selected_peer_instance_id,
            selected_profile_id=selected_profile_id,
            route_decision_digest=route_decision_digest,
            session_id=session_id,
            owner_principal_id=owner_principal_id,
            owner_instance_id=owner_instance_id,
            authority_epoch=authority_epoch,
            heartbeat_timeout_ms=heartbeat_timeout_ms,
            owner_peer_id=owner_peer_id,
        )
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
        return self._admission.reject_policy(command_id, error_code=error_code)

    def prepare_request(
        self,
        command_id: CommandID | str,
        *,
        session_key: SessionBindingKey | None = None,
    ) -> RequestSnapshot:
        """Validate persisted binding evidence and enter PREPARED."""
        return self._admission.prepare_request(command_id, session_key=session_key)
    def create_attempt(
        self,
        command_id: CommandID | str,
    ) -> AttemptSnapshot:
        """Create the next monotonic attempt under PREPARED."""
        return self._attempts.create_attempt(command_id)

    def fail_pre_dispatch(
        self,
        command_id: CommandID | str,
        attempt_id: str,
        *,
        error_code: ErrorCode,
        transport: str,
        operational_failure_category: (
            OperationalFailureCategory | None
        ) = None,
        evidence_refs: tuple[str, ...] = (),
    ) -> tuple[RequestSnapshot, AttemptSnapshot]:
        """Commit a proven pre-dispatch failure and terminal outbox."""
        return self._attempts.fail_pre_dispatch(
            command_id,
            attempt_id,
            error_code=error_code,
            transport=transport,
            operational_failure_category=operational_failure_category,
            evidence_refs=evidence_refs,
        )

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
        return self._attempts.record_dispatch_intent(
            command_id,
            attempt_id,
        )

    def record_dispatch_intent_and_reserve_artifacts(
        self,
        command_id: CommandID | str,
        attempt_id: str,
        *,
        expected_manifest_digest: str,
    ) -> tuple[
        RequestSnapshot,
        AttemptSnapshot,
        LeaseSnapshot,
    ]:
        """Commit replay boundary, bind lease attempt ID, and reserve verified artifacts atomically in ONE transaction."""
        return self._attempts.record_dispatch_intent_and_reserve_artifacts(
            command_id,
            attempt_id,
            expected_manifest_digest=expected_manifest_digest,
        )

    def record_start_uncertain(
        self,
        command_id: CommandID | str,
        attempt_id: str,
    ) -> tuple[RequestSnapshot, AttemptSnapshot]:
        """Commit START_UNCERTAIN without claiming process identity."""
        return self._attempts.record_start_uncertain(
            command_id,
            attempt_id,
        )

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
        return self._attempts.record_running(
            command_id,
            attempt_id,
            process_identity=process_identity,
        )

    def begin_cancellation(
        self,
        command_id: CommandID | str,
        attempt_id: str,
    ) -> tuple[RequestSnapshot, AttemptSnapshot]:
        """Persist CANCELLING without performing process cancellation."""
        return self._attempts.begin_cancellation(
            command_id,
            attempt_id,
        )

    def begin_assessment(
        self,
        command_id: CommandID | str,
        attempt_id: str,
    ) -> tuple[RequestSnapshot, AttemptSnapshot]:
        """Persist ASSESSING from injected terminal process evidence."""
        return self._attempts.begin_assessment(
            command_id,
            attempt_id,
        )

    def complete_attempt(
        self,
        command_id: CommandID | str,
        attempt_id: str,
        *,
        result: AskResult,
        transport: str,
        started_at: int,
        process_integrity: bool,
        operational_failure_category: (
            OperationalFailureCategory | None
        ) = None,
        evidence_refs: tuple[str, ...] = (),
    ) -> tuple[RequestSnapshot, AttemptSnapshot]:
        """Commit the derived terminal state and canonical outbox event."""
        return self._attempts.complete_attempt(
            command_id,
            attempt_id,
            result=result,
            transport=transport,
            started_at=started_at,
            process_integrity=process_integrity,
            operational_failure_category=operational_failure_category,
            evidence_refs=evidence_refs,
        )

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
        return self._attempts.authorize_retry(
            command_id,
            previous_attempt_id,
            reconciliation_complete=reconciliation_complete,
            heartbeat_timeout_ms=heartbeat_timeout_ms,
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

    def _close_lease_in_unit(
        self,
        unit: UnitOfWork,
        request: LeaseCloseRequest,
        timestamp: int,
    ) -> LeaseSnapshot:
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
        return updated

    def close_lease(
        self,
        request: LeaseCloseRequest,
    ) -> LeaseSnapshot:
        """Close an active lease in state storage."""

        timestamp = self._clock.now()
        with self._store.unit_of_work() as unit:
            updated = self._close_lease_in_unit(
                unit,
                request,
                timestamp,
            )
            self._faults.hit(FaultPoint.BEFORE_COMMIT)
            unit.commit()

        self._faults.hit(FaultPoint.AFTER_COMMIT)
        return updated

    def complete_attempt_with_artifacts_and_lease(
        self,
        command_id: CommandID | str,
        attempt_id: str,
        *,
        result: AskResult,
        transport: str,
        started_at: int,
        final_fence: LeaseFenceTuple,
        process_integrity: bool = True,
        operational_failure_category: (
            OperationalFailureCategory | None
        ) = None,
        evidence_refs: tuple[str, ...] = (),
    ) -> tuple[RequestSnapshot, AttemptSnapshot]:
        """Commit attempt completion, consume reserved artifacts, and close session lease atomically in ONE transaction."""
        return self._attempts.complete_attempt_with_artifacts_and_lease(
            command_id,
            attempt_id,
            result=result,
            transport=transport,
            started_at=started_at,
            final_fence=final_fence,
            process_integrity=process_integrity,
            operational_failure_category=operational_failure_category,
            evidence_refs=evidence_refs,
        )

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
