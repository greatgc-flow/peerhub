from __future__ import annotations

from collections.abc import Sequence

from peerhub.core.context import Clock, IdSource
from peerhub.core.errors import (
    InvalidMutationError,
    RecordNotFoundError,
)
from peerhub.core.protocol import (
    CommandID,
    ErrorCode,
    OperationalFailureCategory,
)
from peerhub.state.contract import StateStore, UnitOfWork

from .contract import (
    AskResult,
    AttemptSnapshot,
    LeaseCloseRequest,
    LeaseFenceTuple,
    LeaseReservationRequest,
    LeaseSnapshot,
    ProcessBirthIdentity,
    RequestSnapshot,
)
from .model import (
    authorize_retry as reduce_authorize_retry,
    begin_assessment as reduce_begin_assessment,
    begin_cancellation as reduce_begin_cancellation,
    close_lease,
    complete_attempt as reduce_complete_attempt,
    create_attempt as reduce_create_attempt,
    fail_pre_dispatch as reduce_fail_pre_dispatch,
    record_dispatch_intent as reduce_dispatch_intent,
    record_running as reduce_running,
    record_start_uncertain as reduce_start_uncertain,
    reserve_lease,
)
from .helpers import (
    attempt_terminal_event as _attempt_terminal_event,
    cas_request_attempt as _cas_request_attempt,
    dispatch_event as _dispatch_event,
    raise_attempt_cas as _raise_attempt_cas,
    raise_request_cas as _raise_request_cas,
    require_attempt as _require_attempt,
    require_lease as _require_lease,
    require_request as _require_request,
)
from .unit_of_work import DispatchUnitOfWork, FaultInjector, FaultPoint, _NoFaultInjector


class AttemptLifecycleCoordinator:
    """Orchestrate Phase 1 attempt lifecycle and state transitions."""

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

    def create_attempt(
        self,
        command_id: CommandID | str,
    ) -> AttemptSnapshot:
        """Create the next monotonic attempt under PREPARED."""

        with self._store.unit_of_work() as unit:
            request = _require_request(unit, command_id)
            lease = _require_lease(
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
        transport: str,
        operational_failure_category: (
            OperationalFailureCategory | None
        ) = None,
        evidence_refs: tuple[str, ...] = (),
    ) -> tuple[RequestSnapshot, AttemptSnapshot]:
        """Commit a proven pre-dispatch failure and terminal outbox."""

        with self._store.unit_of_work() as unit:
            request = _require_request(unit, command_id)
            attempt = _require_attempt(unit, attempt_id)
            timestamp = self._clock.now()
            updated_request, updated_attempt = (
                reduce_fail_pre_dispatch(
                    request,
                    attempt,
                    error_code=error_code,
                    updated_at=timestamp,
                )
            )
            _cas_request_attempt(unit, self._faults, request, updated_request, attempt, updated_attempt)
            unit.add_outbox_event(
                _dispatch_event(
                    updated_request,
                    event_id=self._ids.new_id("outbox-event"),
                    occurred_at=timestamp,
                )
            )
            self._faults.hit(FaultPoint.AFTER_OUTBOX_WRITE)
            unit.add_outbox_event(
                _attempt_terminal_event(
                    updated_request,
                    updated_attempt,
                    event_id=self._ids.new_id(
                        "outbox-event"
                    ),
                    terminal_at=timestamp,
                    transport=transport,
                    operational_failure_category=(
                        operational_failure_category
                    ),
                    process_integrity=True,
                    started_at=None,
                    evidence_refs=evidence_refs,
                )
            )
            self._faults.hit(FaultPoint.AFTER_OUTBOX_WRITE)
            self._faults.hit(FaultPoint.BEFORE_COMMIT)
            unit.commit()

        self._faults.hit(FaultPoint.AFTER_COMMIT)
        return (updated_request, updated_attempt)

    def _record_dispatch_intent_in_unit(
        self,
        unit: UnitOfWork,
        command_id: CommandID | str,
        attempt_id: str,
        timestamp: int,
    ) -> tuple[RequestSnapshot, AttemptSnapshot, LeaseSnapshot, str]:
        request = _require_request(unit, command_id)
        attempt = _require_attempt(unit, attempt_id)
        lease = _require_lease(
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
            updated_at=timestamp,
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
        # DP-06: durable isolated-journal boundary -- INTENT_PERSISTED
        # must be durably appended here (SLICE5-KICKOFF-R1.md
        # "Ratified decisions" item 4).
        event_id = self._ids.new_id("outbox-event")
        unit.add_outbox_event(
            _dispatch_event(
                updated_request,
                event_id=event_id,
                occurred_at=timestamp,
            )
        )
        self._faults.hit(FaultPoint.AFTER_OUTBOX_WRITE)
        return (
            updated_request,
            updated_attempt,
            updated_lease,
            event_id,
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

        with self._store.unit_of_work() as unit:
            timestamp = self._clock.now()
            (
                updated_request,
                updated_attempt,
                updated_lease,
                _,
            ) = self._record_dispatch_intent_in_unit(
                unit,
                command_id,
                attempt_id,
                timestamp,
            )
            self._faults.hit(FaultPoint.BEFORE_COMMIT)
            unit.commit()

        self._faults.hit(FaultPoint.AFTER_COMMIT)
        return (
            updated_request,
            updated_attempt,
            updated_lease,
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

        with self._store.unit_of_work() as unit:
            timestamp = self._clock.now()
            (
                updated_request,
                updated_attempt,
                updated_lease,
                intent_event_id,
            ) = self._record_dispatch_intent_in_unit(
                unit,
                command_id,
                attempt_id,
                timestamp,
            )
            reserved_ok = unit.reserve_verified_artifacts_for_dispatch(
                attempt_id=attempt_id,
                expected_manifest_digest=expected_manifest_digest,
                intent_event_id=intent_event_id,
                reserved_at=timestamp,
            )
            if not reserved_ok:
                raise InvalidMutationError(
                    f"Artifact reservation failed for attempt {attempt_id}"
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
            request = _require_request(unit, command_id)
            attempt = _require_attempt(unit, attempt_id)
            timestamp = self._clock.now()
            updated_request, updated_attempt = (
                reduce_start_uncertain(
                    request,
                    attempt,
                    updated_at=timestamp,
                )
            )
            _cas_request_attempt(unit, self._faults, request, updated_request, attempt, updated_attempt)
            unit.add_outbox_event(
                _dispatch_event(
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
            request = _require_request(unit, command_id)
            attempt = _require_attempt(unit, attempt_id)
            lease = _require_lease(
                unit,
                request.lease_id,
            )
            timestamp = self._clock.now()
            (
                updated_request,
                updated_attempt,
                updated_lease,
            ) = reduce_running(
                request,
                attempt,
                lease,
                process_identity=process_identity,
                updated_at=timestamp,
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
            unit.add_outbox_event(
                _dispatch_event(
                    updated_request,
                    event_id=self._ids.new_id("outbox-event"),
                    occurred_at=timestamp,
                )
            )
            self._faults.hit(FaultPoint.AFTER_OUTBOX_WRITE)
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
            request = _require_request(unit, command_id)
            attempt = _require_attempt(unit, attempt_id)
            updated_request, updated_attempt = (
                reduce_begin_cancellation(
                    request,
                    attempt,
                    updated_at=self._clock.now(),
                )
            )
            _cas_request_attempt(unit, self._faults, request, updated_request, attempt, updated_attempt)
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
            request = _require_request(unit, command_id)
            attempt = _require_attempt(unit, attempt_id)
            updated_request, updated_attempt = (
                reduce_begin_assessment(
                    request,
                    attempt,
                    updated_at=self._clock.now(),
                )
            )
            _cas_request_attempt(unit, self._faults, request, updated_request, attempt, updated_attempt)
            self._faults.hit(FaultPoint.BEFORE_COMMIT)
            unit.commit()

        self._faults.hit(FaultPoint.AFTER_COMMIT)
        return (updated_request, updated_attempt)

    def _complete_attempt_in_unit(
        self,
        unit: UnitOfWork,
        command_id: CommandID | str,
        attempt_id: str,
        *,
        result: AskResult,
        transport: str,
        started_at: int,
        timestamp: int,
        process_integrity: bool = True,
        operational_failure_category: (
            OperationalFailureCategory | None
        ) = None,
        evidence_refs: tuple[str, ...] = (),
    ) -> tuple[RequestSnapshot, AttemptSnapshot, str]:
        request = _require_request(unit, command_id)
        attempt = _require_attempt(unit, attempt_id)
        updated_request, updated_attempt = (
            reduce_complete_attempt(
                request,
                attempt,
                result=result,
                updated_at=timestamp,
            )
        )
        _cas_request_attempt(unit, self._faults, request, updated_request, attempt, updated_attempt)
        unit.add_outbox_event(
            _dispatch_event(
                updated_request,
                event_id=self._ids.new_id("outbox-event"),
                occurred_at=timestamp,
            )
        )
        self._faults.hit(FaultPoint.AFTER_OUTBOX_WRITE)
        terminal_event_id = self._ids.new_id("outbox-event")
        unit.add_outbox_event(
            _attempt_terminal_event(
                updated_request,
                updated_attempt,
                event_id=terminal_event_id,
                terminal_at=timestamp,
                transport=transport,
                operational_failure_category=(
                    operational_failure_category
                ),
                process_integrity=process_integrity,
                started_at=started_at,
                evidence_refs=evidence_refs,
            )
        )
        self._faults.hit(FaultPoint.AFTER_OUTBOX_WRITE)
        return (updated_request, updated_attempt, terminal_event_id)

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

        with self._store.unit_of_work() as unit:
            timestamp = self._clock.now()
            (
                updated_request,
                updated_attempt,
                _,
            ) = self._complete_attempt_in_unit(
                unit,
                command_id,
                attempt_id,
                result=result,
                transport=transport,
                started_at=started_at,
                timestamp=timestamp,
                process_integrity=process_integrity,
                operational_failure_category=operational_failure_category,
                evidence_refs=evidence_refs,
            )
            self._faults.hit(FaultPoint.BEFORE_COMMIT)
            unit.commit()

        self._faults.hit(FaultPoint.AFTER_COMMIT)
        return (updated_request, updated_attempt)

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

        with self._store.unit_of_work() as unit:
            timestamp = self._clock.now()
            (
                updated_request,
                updated_attempt,
                terminal_event_id,
            ) = self._complete_attempt_in_unit(
                unit,
                command_id,
                attempt_id,
                result=result,
                transport=transport,
                started_at=started_at,
                timestamp=timestamp,
                process_integrity=process_integrity,
                operational_failure_category=operational_failure_category,
                evidence_refs=evidence_refs,
            )
            if unit.get_artifact_manifest(attempt_id) is not None:
                consumed_ok = unit.consume_reserved_artifacts(
                    attempt_id=attempt_id,
                    terminal_outcome_event_id=terminal_event_id,
                    consumed_at=timestamp,
                )
                if not consumed_ok:
                    raise InvalidMutationError(
                        f"Artifact consumption failed for attempt {attempt_id}"
                    )
            close_req = LeaseCloseRequest(
                lease_id=updated_request.lease_id,
                fence=final_fence,
            )
            self._close_lease_in_unit(
                unit,
                close_req,
                timestamp,
            )

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
            request = _require_request(unit, command_id)
            previous_attempt = _require_attempt(
                unit,
                previous_attempt_id,
            )
            current_lease = _require_lease(
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
                _raise_request_cas(unit, request)
            self._faults.hit(FaultPoint.AFTER_REQUEST_CAS)

            if updated_attempt != previous_attempt:
                if not unit.cas_update_attempt(
                    previous_attempt,
                    updated_attempt,
                ):
                    _raise_attempt_cas(
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
