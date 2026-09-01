"""Coordinate expired process-lease recovery and residual PID cleanup."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from peerhub.core.context import Clock, IdSource
from peerhub.core.protocol import canonical_json_bytes, require_text
from peerhub.dispatch.contract import (
    LeaseState,
    ProcessBirthIdentity,
    RecoveryDecision,
    RecoveryTrigger,
)
from peerhub.dispatch.pipe import TreeController
from peerhub.dispatch.session_lease import SessionLeaseCoordinator
from peerhub.dispatch.tree_controller import verify_process_identity
from peerhub.dispatch.unit_of_work import (
    DispatchReadUnitOfWork,
    DispatchUnitOfWork,
)
from peerhub.health.service import HealthService
from peerhub.state.contract import StateStore


@dataclass(frozen=True, slots=True)
class ProcessLeaseSweepItem:
    """Auditable outcome for one heartbeat-expired process lease."""

    lease_id: str
    profile_id: str
    pre_state: LeaseState
    post_state: LeaseState
    process_alive: bool
    process_identity_matches: bool
    actual_process_creation_time: int
    recovery_receipt_id: str
    recovery_decision: RecoveryDecision
    reaped: bool
    reap_signal: str | None
    backoff_duration_seconds: int


@dataclass(frozen=True, slots=True)
class ProcessLeaseSweepReport:
    """Bounded result of one process-lease sweep invocation."""

    sweep_id: str
    as_of: int
    swept: tuple[ProcessLeaseSweepItem, ...]


def _recovery_evidence_digest(
    *,
    lease_id: str,
    heartbeat_expires_at: int,
    as_of: int,
    identity: ProcessBirthIdentity | None,
    actual_process_creation_time: int,
    process_alive: bool,
    process_identity_matches: bool,
) -> str:
    evidence = {
        "kind": "process-lease-heartbeat-timeout",
        "lease_id": lease_id,
        "heartbeat_expires_at": heartbeat_expires_at,
        "as_of": as_of,
        "expected_pid": identity.pid if identity is not None else None,
        "expected_process_creation_time": (
            identity.process_creation_time if identity is not None else None
        ),
        "actual_process_creation_time": actual_process_creation_time,
        "process_alive": process_alive,
        "process_identity_matches": process_identity_matches,
    }
    return "sha256:" + hashlib.sha256(canonical_json_bytes(evidence)).hexdigest()


class ProcessLeaseSweepCoordinator:
    """Compose lease recovery, profile backoff, and residual PID cleanup.

    Most Windows orphans are already contained by Job Object
    ``KILL_ON_JOB_CLOSE``. Persisted lease state cannot reconstruct the live
    Job Object or POSIX process-group handle, so optional reaping here is an
    identity-verified *single-process* kill of the root PID, never a claim of
    cross-process tree termination.
    """

    def __init__(
        self,
        sessions: SessionLeaseCoordinator,
        *,
        store: StateStore[DispatchUnitOfWork, DispatchReadUnitOfWork],
        tree_controller: TreeController,
        health: HealthService,
        clock: Clock,
        ids: IdSource,
        policy_id: str,
        policy_revision: int,
    ) -> None:
        self._sessions = sessions
        self._store = store
        self._tree_controller = tree_controller
        self._health = health
        self._clock = clock
        self._ids = ids
        self._policy_id = require_text(policy_id, "policy_id")
        if type(policy_revision) is not int or policy_revision < 1:
            raise ValueError("policy_revision must be a positive integer")
        self._policy_revision = policy_revision
        # Lease expiry is one transient incident, so use the configured first
        # backoff rung rather than inventing an action-specific duration.
        self._backoff_duration_seconds = (
            health.policy.recovery_backoff_seconds[0]
        )

    @property
    def backoff_duration_seconds(self) -> int:
        """Return the health-policy-derived transient backoff duration."""

        return self._backoff_duration_seconds

    def sweep(
        self,
        *,
        recovery_actor_principal_id: str,
        as_of: int | None = None,
        limit: int = 100,
        reap: bool = True,
    ) -> ProcessLeaseSweepReport:
        """Recover expired leases and optionally kill verified root PIDs.

        Recovery moves each lease out of the active scan set, so an immediate
        second call naturally has no lease-level effects and needs no parallel
        "already swept" marker.
        """

        actor_id = require_text(
            recovery_actor_principal_id,
            "recovery_actor_principal_id",
        )
        evaluated_at = self._clock.now() if as_of is None else as_of
        if type(evaluated_at) is not int or evaluated_at < 0:
            raise ValueError("as_of must be a nonnegative integer")
        if type(limit) is not int or limit < 1:
            raise ValueError("limit must be a positive integer")
        if type(reap) is not bool:
            raise ValueError("reap must be a boolean")

        with self._store.read_unit_of_work() as unit:
            candidates = unit.list_expired_leases(
                evaluated_at,
                limit=limit,
            )

        results: list[ProcessLeaseSweepItem] = []
        for lease in candidates:
            identity = lease.fence.owner_process_birth_identity
            actual_creation_time = 0
            process_identity_matches = False
            process_alive = False
            if identity is not None:
                process_identity_matches, actual_creation_time = (
                    verify_process_identity(identity.pid, identity)
                )
                process_alive = actual_creation_time > 0

            evidence_digest = _recovery_evidence_digest(
                lease_id=lease.lease_id,
                heartbeat_expires_at=lease.heartbeat_expires_at,
                as_of=evaluated_at,
                identity=identity,
                actual_process_creation_time=actual_creation_time,
                process_alive=process_alive,
                process_identity_matches=process_identity_matches,
            )
            recovered, receipt = self._sessions.recover_lease(
                lease.lease_id,
                recovery_actor_principal_id=actor_id,
                trigger=RecoveryTrigger.HEARTBEAT_TIMEOUT,
                evidence_digest=evidence_digest,
                policy_id=self._policy_id,
                policy_revision=self._policy_revision,
                is_process_alive=process_alive,
                process_identity_matches=process_identity_matches,
            )

            reap_receipt = None
            if (
                reap
                and identity is not None
                and process_alive
                and process_identity_matches
            ):
                reap_receipt = self._tree_controller.kill_by_identity(identity)

            # owner_peer_id is the persisted fully-resolved dispatch target.
            # Older callers may leave it empty, so retain a narrow compatibility
            # fallback while keeping the write scoped to this lease's owner.
            profile_id = (
                lease.fence.owner_peer_id
                or lease.fence.owner_instance_id
            )
            self._health.apply_transient_backoff(
                profile_id,
                self._backoff_duration_seconds,
                "lease_expired",
            )

            results.append(ProcessLeaseSweepItem(
                lease_id=lease.lease_id,
                profile_id=profile_id,
                pre_state=lease.state,
                post_state=recovered.state,
                process_alive=process_alive,
                process_identity_matches=process_identity_matches,
                actual_process_creation_time=actual_creation_time,
                recovery_receipt_id=receipt.recovery_receipt_id,
                recovery_decision=receipt.decision,
                reaped=(
                    reap_receipt.dispatched
                    if reap_receipt is not None
                    else False
                ),
                reap_signal=(
                    reap_receipt.signal_name
                    if reap_receipt is not None
                    else None
                ),
                backoff_duration_seconds=self._backoff_duration_seconds,
            ))

        return ProcessLeaseSweepReport(
            sweep_id=self._ids.new_id("process-lease-sweep"),
            as_of=evaluated_at,
            swept=tuple(results),
        )
