from __future__ import annotations

from peerhub.core.context import Clock, IdSource
from peerhub.core.errors import (
    InvalidMutationError,
    RecordNotFoundError,
    StaleRevisionError,
)
from peerhub.state.contract import StateStore, UnitOfWork

from .contract import (
    LeaseCloseRequest,
    LeaseCreateRequest,
    LeaseFenceCheckRequest,
    LeaseRenewRequest,
    LeaseSnapshot,
    RecoveryReceipt,
    RecoveryTrigger,
    SessionBindingKey,
    SessionBindingSnapshot,
    SessionResumeRequest,
)
from .model import (
    close_lease,
    create_lease,
    create_session_binding,
    expire_and_recover_lease,
    renew_lease,
    resume_session_binding,
    validate_lease_fence,
)
from .unit_of_work import DispatchUnitOfWork, FaultInjector, FaultPoint, _NoFaultInjector  # pyright: ignore[reportPrivateUsage]


class SessionLeaseCoordinator:
    """Orchestrate Phase 1 session binding, lease lifecycle, and recovery."""

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
        current = unit.get_lease(request.lease_id)  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType, reportUnknownVariableType]
        if current is None:
            raise RecordNotFoundError(
                "lease",
                request.lease_id,
            )

        updated = close_lease(
            current,  # pyright: ignore[reportUnknownArgumentType]
            request,
            updated_at=timestamp,
        )

        if not unit.cas_update_lease(current, updated):  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType]
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
