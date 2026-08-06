"""Transactional health, admission, and recovery orchestration.

This module performs persistence orchestration around the pure reducers in
``peerhub.health.model``. It does not probe providers, mutate configuration,
read dispatch state, or derive raw telemetry aggregates.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Protocol

from peerhub.core.context import Clock, IdSource
from peerhub.core.errors import (
    InvalidMutationError,
    RecordNotFoundError,
    RecoveryProbeGrantConflictError,
    StaleRevisionError,
)
from peerhub.core.protocol import OperationalFailureCategory
from peerhub.state.contract import StateStore, UnitOfWork
from peerhub.telemetry.contract import (
    OperationalProjectionSnapshot,
    ReadinessObserved,
    TelemetryProjectionReader,
)

from .contract import (
    AdmissionSnapshot,
    AdmissionSnapshotEntry,
    AdmissionState,
    AutomaticClearanceResult,
    CircuitState,
    CooldownEvaluation,
    EvidenceSubject,
    HealthCircuitSnapshot,
    HealthPolicy,
    HealthProjectionSnapshot,
    HealthScopeMembershipSnapshot,
    HealthStageObservation,
    PolicyAction,
    PolicyReceipt,
    PolicyScope,
    ProbeDisposition,
    RecoveryProbeApplication,
    RecoveryProbeAuthorization,
    RecoveryProbeClaimResult,
    RecoveryProbeGrant,
    RecoveryProbeReceipt,
    ReadinessEvaluation,
)
from .model import (
    apply_automatic_clearance as reduce_automatic_clearance,
)
from .model import (
    apply_policy_action as reduce_policy_action,
)
from .model import (
    apply_recovery_probe_result as reduce_probe_result,
)
from .model import (
    authorize_recovery_probe as reduce_authorize_recovery,
)
from .model import (
    canonical_admission_snapshot_digest,
    claim_recovery_probe as reduce_claim_probe,
    classify_health_failure,
    compose_health_projection_evidence_refs,
    derive_policy_action,
    evaluate_cooldown as reduce_evaluate_cooldown,
    evaluate_readiness_evidence,
    freeze_admission_snapshot as reduce_freeze_admission,
    resolve_admission_state,
    resolve_projection_cooldown_until,
)


InstanceProfilePair = tuple[str, str]


class HealthUnitOfWork(UnitOfWork, Protocol):
    """Persistence operations required by the health service."""

    def get_health_policy_revision(
        self,
        policy_id: str,
        revision: int,
    ) -> HealthPolicy | None:
        """Return one immutable health-policy revision."""

        ...

    def add_readiness_observation(
        self,
        observed: ReadinessObserved,
    ) -> None:
        """Insert one immutable readiness observation."""

        ...

    def get_health_projection(
        self,
        instance_id: str,
        profile_id: str,
    ) -> HealthProjectionSnapshot | None:
        """Return the live health projection for one pair."""

        ...

    def add_health_projection(
        self,
        projection: HealthProjectionSnapshot,
    ) -> None:
        """Insert a revision-one live health projection."""

        ...

    def cas_update_health_projection(
        self,
        current: HealthProjectionSnapshot,
        updated: HealthProjectionSnapshot,
    ) -> bool:
        """CAS-update a health projection by revision."""

        ...

    def get_health_circuit(
        self,
        scope: PolicyScope | str,
        subject: str,
    ) -> HealthCircuitSnapshot | None:
        """Return the circuit for one policy scope and subject."""

        ...

    def add_health_circuit(
        self,
        circuit: HealthCircuitSnapshot,
    ) -> None:
        """Insert a revision-one health circuit."""

        ...

    def cas_update_health_circuit(
        self,
        current: HealthCircuitSnapshot,
        updated: HealthCircuitSnapshot,
    ) -> bool:
        """CAS-update a health circuit by revision."""

        ...

    def add_recovery_probe_grant(
        self,
        grant: RecoveryProbeGrant,
    ) -> None:
        """Insert one unconsumed recovery-probe grant."""

        ...

    def get_recovery_probe_grant(
        self,
        grant_id: str,
    ) -> RecoveryProbeGrant | None:
        """Return a recovery-probe grant by ID."""

        ...

    def get_live_recovery_probe_grant(
        self,
        circuit_id: str,
    ) -> RecoveryProbeGrant | None:
        """Return the sole unconsumed grant for a circuit."""

        ...

    def cas_claim_recovery_probe_grant(
        self,
        current: RecoveryProbeGrant,
        updated: RecoveryProbeGrant,
    ) -> bool:
        """Contention-safe single-use grant claim."""

        ...

    def add_recovery_probe_receipt(
        self,
        receipt: RecoveryProbeReceipt,
    ) -> None:
        """Insert one immutable recovery-probe receipt."""

        ...

    def add_admission_snapshot(
        self,
        snapshot: AdmissionSnapshot,
    ) -> None:
        """Insert an immutable admission snapshot and entries."""

        ...


class FaultPoint(str):
    """Named transaction boundaries for deterministic tests."""

    AFTER_READINESS_OBSERVATION_WRITE = (
        "AFTER_READINESS_OBSERVATION_WRITE"
    )
    AFTER_HEALTH_PROJECTION_WRITE = (
        "AFTER_HEALTH_PROJECTION_WRITE"
    )
    AFTER_HEALTH_PROJECTION_CAS = (
        "AFTER_HEALTH_PROJECTION_CAS"
    )
    AFTER_HEALTH_CIRCUIT_WRITE = (
        "AFTER_HEALTH_CIRCUIT_WRITE"
    )
    AFTER_HEALTH_CIRCUIT_CAS = (
        "AFTER_HEALTH_CIRCUIT_CAS"
    )
    AFTER_RECOVERY_GRANT_WRITE = (
        "AFTER_RECOVERY_GRANT_WRITE"
    )
    AFTER_RECOVERY_GRANT_CAS = "AFTER_RECOVERY_GRANT_CAS"
    AFTER_RECOVERY_RECEIPT_WRITE = (
        "AFTER_RECOVERY_RECEIPT_WRITE"
    )
    AFTER_ADMISSION_SNAPSHOT_WRITE = (
        "AFTER_ADMISSION_SNAPSHOT_WRITE"
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


class HealthService:
    """Own the live policy-derived health and admission projection."""

    def __init__(
        self,
        store: StateStore[HealthUnitOfWork],
        *,
        telemetry: TelemetryProjectionReader,
        policy: HealthPolicy,
        membership: HealthScopeMembershipSnapshot,
        clock: Clock,
        ids: IdSource,
        fault_injector: FaultInjector | None = None,
    ) -> None:
        self._store = store
        self._telemetry = telemetry
        self._policy = policy
        self._membership = membership
        self._clock = clock
        self._ids = ids
        self._faults = fault_injector or _NoFaultInjector()

    def _require_policy(
        self,
        unit: HealthUnitOfWork,
    ) -> HealthPolicy:
        policy = unit.get_health_policy_revision(
            self._policy.policy_id,
            self._policy.revision,
        )
        if policy is None:
            raise RecordNotFoundError(
                "health_policy_revision",
                (
                    f"{self._policy.policy_id}"
                    f"@{self._policy.revision}"
                ),
            )
        if policy != self._policy:
            raise InvalidMutationError(
                "injected health policy differs from its "
                "persisted policy revision"
            )
        return policy

    @staticmethod
    def _require_projection(
        unit: HealthUnitOfWork,
        instance_id: str,
        profile_id: str,
    ) -> HealthProjectionSnapshot:
        projection = unit.get_health_projection(
            instance_id,
            profile_id,
        )
        if projection is None:
            raise RecordNotFoundError(
                "health_projection",
                f"{instance_id}/{profile_id}",
            )
        return projection

    @staticmethod
    def _require_circuit(
        unit: HealthUnitOfWork,
        scope: PolicyScope,
        subject: str,
    ) -> HealthCircuitSnapshot:
        circuit = unit.get_health_circuit(scope, subject)
        if circuit is None:
            raise RecordNotFoundError(
                "health_circuit",
                f"{scope.value}/{subject}",
            )
        return circuit

    @staticmethod
    def _require_grant(
        unit: HealthUnitOfWork,
        grant_id: str,
    ) -> RecoveryProbeGrant:
        grant = unit.get_recovery_probe_grant(grant_id)
        if grant is None:
            raise RecordNotFoundError(
                "recovery_probe_grant",
                grant_id,
            )
        return grant

    @staticmethod
    def _raise_projection_cas(
        unit: HealthUnitOfWork,
        current: HealthProjectionSnapshot,
    ) -> None:
        latest = unit.get_health_projection(
            current.instance_id,
            current.profile_id,
        )
        raise StaleRevisionError(
            current.projection_id,
            current.revision,
            0 if latest is None else latest.revision,
        )

    @staticmethod
    def _raise_circuit_cas(
        unit: HealthUnitOfWork,
        current: HealthCircuitSnapshot,
    ) -> None:
        latest = unit.get_health_circuit(
            current.scope,
            current.subject,
        )
        raise StaleRevisionError(
            current.circuit_id,
            current.revision,
            0 if latest is None else latest.revision,
        )

    @staticmethod
    def _raise_grant_cas(
        unit: HealthUnitOfWork,
        current: RecoveryProbeGrant,
    ) -> None:
        latest = unit.get_recovery_probe_grant(
            current.grant_id
        )
        raise StaleRevisionError(
            current.grant_id,
            current.revision,
            0 if latest is None else latest.revision,
        )

    def _require_configured_pair(
        self,
        pair: InstanceProfilePair,
    ) -> None:
        if pair not in self._membership.configured_members:
            raise InvalidMutationError(
                "instance/profile pair is not present in the "
                "injected configuration population"
            )

    @staticmethod
    def _require_projection_baseline(
        projection: HealthProjectionSnapshot,
    ) -> ReadinessEvaluation:
        if (
            projection.readiness_evaluation is None
            or projection.sealed_runtime_revision is None
            or projection.adapter_declares_probe_safe is None
        ):
            raise InvalidMutationError(
                "health projection lacks a durable readiness "
                "evaluation context; record fresh readiness "
                "evidence before recomputing it"
            )
        return projection.readiness_evaluation

    @staticmethod
    def _require_projection_policy(
        projection: HealthProjectionSnapshot,
        policy: HealthPolicy,
    ) -> None:
        if (
            projection.policy_id != policy.policy_id
            or projection.policy_revision != policy.revision
        ):
            raise InvalidMutationError(
                "health projection was evaluated under a "
                "different health-policy revision"
            )

    def _members_for_scope(
        self,
        scope: PolicyScope,
        subject: str,
    ) -> tuple[InstanceProfilePair, ...]:
        if scope is PolicyScope.PROFILE:
            members = tuple(
                pair
                for pair in self._membership.configured_members
                if pair[1] == subject
            )
        else:
            members = ()
            for binding in self._membership.bindings:
                if (
                    binding.scope is scope
                    and binding.subject == subject
                ):
                    members = binding.members
                    break

        if not members:
            raise InvalidMutationError(
                "health circuit scope/subject has no members "
                "in the injected configuration snapshot"
            )
        return members

    def _circuit_keys_for_pair(
        self,
        pair: InstanceProfilePair,
    ) -> tuple[tuple[PolicyScope, str], ...]:
        self._require_configured_pair(pair)
        keys: list[tuple[PolicyScope, str]] = [
            (PolicyScope.PROFILE, pair[1])
        ]
        for binding in self._membership.bindings:
            if pair in binding.members:
                keys.append(
                    (binding.scope, binding.subject)
                )
        return tuple(keys)

    def _read_operational_projection(
        self,
        instance_id: str,
        profile_id: str,
    ) -> OperationalProjectionSnapshot | None:
        try:
            return self._telemetry.get(
                instance_id,
                profile_id,
            )
        except RecordNotFoundError:
            return None

    def _aggregate_for_pair(
        self,
        unit: HealthUnitOfWork,
        pair: InstanceProfilePair,
        *,
        readiness: ReadinessEvaluation,
        policy: HealthPolicy,
        now: int,
    ) -> tuple[AdmissionState, int | None]:
        circuit_states: list[AdmissionState] = []
        cooldown_evaluations: list[CooldownEvaluation] = []

        for scope, subject in self._circuit_keys_for_pair(pair):
            circuit = unit.get_health_circuit(
                scope,
                subject,
            )
            if circuit is None:
                continue

            cooldown = reduce_evaluate_cooldown(
                circuit,
                policy=policy,
                now=now,
            )
            cooldown_evaluations.append(cooldown)

            live_grant = (
                unit.get_live_recovery_probe_grant(
                    circuit.circuit_id
                )
            )
            valid_live_grant = (
                live_grant is not None
                and circuit.state is CircuitState.CIRCUIT_OPEN
                and circuit.receipt is not None
                and live_grant.receipt == circuit.receipt
                and cooldown.admission_state
                is AdmissionState.RECOVERY_REQUIRED
            )
            circuit_states.append(
                AdmissionState.PROBE_AUTHORIZED
                if valid_live_grant
                else cooldown.admission_state
            )

        admission_state = resolve_admission_state(
            readiness,
            circuit_states=tuple(circuit_states),
        )
        cooldown_until = (
            resolve_projection_cooldown_until(
                admission_state,
                circuit_evaluations=tuple(
                    cooldown_evaluations
                ),
            )
        )
        return admission_state, cooldown_until

    @staticmethod
    def _same_projection_content(
        current: HealthProjectionSnapshot,
        updated: HealthProjectionSnapshot,
    ) -> bool:
        return (
            replace(
                updated,
                revision=current.revision,
                updated_at=current.updated_at,
            )
            == current
        )

    def _write_projection(
        self,
        unit: HealthUnitOfWork,
        current: HealthProjectionSnapshot | None,
        updated: HealthProjectionSnapshot,
    ) -> HealthProjectionSnapshot:
        if current is None:
            unit.add_health_projection(updated)
            self._faults.hit(
                FaultPoint.AFTER_HEALTH_PROJECTION_WRITE
            )
            return updated

        if self._same_projection_content(current, updated):
            return current

        if not unit.cas_update_health_projection(
            current,
            updated,
        ):
            self._raise_projection_cas(unit, current)
        self._faults.hit(
            FaultPoint.AFTER_HEALTH_PROJECTION_CAS
        )
        return updated

    def _write_circuit(
        self,
        unit: HealthUnitOfWork,
        current: HealthCircuitSnapshot | None,
        updated: HealthCircuitSnapshot,
    ) -> HealthCircuitSnapshot:
        if current is None:
            unit.add_health_circuit(updated)
            self._faults.hit(
                FaultPoint.AFTER_HEALTH_CIRCUIT_WRITE
            )
            return updated

        if not unit.cas_update_health_circuit(
            current,
            updated,
        ):
            self._raise_circuit_cas(unit, current)
        self._faults.hit(
            FaultPoint.AFTER_HEALTH_CIRCUIT_CAS
        )
        return updated

    def _recompute_pair(
        self,
        unit: HealthUnitOfWork,
        pair: InstanceProfilePair,
        *,
        policy: HealthPolicy,
        now: int,
        projection_seed: (
            HealthProjectionSnapshot | None
        ) = None,
    ) -> HealthProjectionSnapshot:
        current = self._require_projection(
            unit,
            pair[0],
            pair[1],
        )
        self._require_projection_policy(
            current,
            policy,
        )
        readiness = self._require_projection_baseline(
            current
        )

        if (
            projection_seed is not None
            and projection_seed.projection_id
            != current.projection_id
        ):
            raise InvalidMutationError(
                "projection seed does not identify the "
                "current health projection"
            )

        admission_state, cooldown_until = (
            self._aggregate_for_pair(
                unit,
                pair,
                readiness=readiness,
                policy=policy,
                now=now,
            )
        )
        base = (
            projection_seed
            if projection_seed is not None
            else current
        )
        updated = replace(
            base,
            availability_state=readiness.availability_state,
            admission_state=admission_state,
            policy_id=policy.policy_id,
            policy_revision=policy.revision,
            cooldown_until=cooldown_until,
            revision=current.revision + 1,
            updated_at=now,
        )
        return self._write_projection(
            unit,
            current,
            updated,
        )

    def _recompute_members(
        self,
        unit: HealthUnitOfWork,
        members: tuple[InstanceProfilePair, ...],
        *,
        policy: HealthPolicy,
        now: int,
        seed_pair: InstanceProfilePair | None = None,
        seed_projection: (
            HealthProjectionSnapshot | None
        ) = None,
    ) -> dict[
        InstanceProfilePair,
        HealthProjectionSnapshot,
    ]:
        persisted: dict[
            InstanceProfilePair,
            HealthProjectionSnapshot,
        ] = {}
        for pair in tuple(sorted(set(members))):
            persisted[pair] = self._recompute_pair(
                unit,
                pair,
                policy=policy,
                now=now,
                projection_seed=(
                    seed_projection
                    if seed_pair == pair
                    else None
                ),
            )
        return persisted

    def evaluate_and_persist_readiness(
        self,
        readiness: ReadinessObserved,
        *,
        sealed_runtime_revision: str,
        adapter_declares_probe_safe: bool,
    ) -> HealthProjectionSnapshot:
        """Persist readiness and update its aggregate projection."""

        pair = (
            readiness.instance_id,
            readiness.profile_id,
        )
        self._require_configured_pair(pair)
        operational = self._read_operational_projection(
            readiness.instance_id,
            readiness.profile_id,
        )
        timestamp = self._clock.now()

        with self._store.unit_of_work() as unit:
            policy = self._require_policy(unit)
            evaluation = evaluate_readiness_evidence(
                readiness,
                sealed_runtime_revision=(
                    sealed_runtime_revision
                ),
                policy=policy,
                adapter_declares_probe_safe=(
                    adapter_declares_probe_safe
                ),
            )

            unit.add_readiness_observation(readiness)
            self._faults.hit(
                FaultPoint.AFTER_READINESS_OBSERVATION_WRITE
            )

            current = unit.get_health_projection(
                readiness.instance_id,
                readiness.profile_id,
            )
            admission_state, cooldown_until = (
                self._aggregate_for_pair(
                    unit,
                    pair,
                    readiness=evaluation,
                    policy=policy,
                    now=timestamp,
                )
            )
            evidence_refs = (
                compose_health_projection_evidence_refs(
                    readiness.evidence.evidence_ref,
                    operational_refs=(
                        operational.evidence_refs
                        if operational is not None
                        else ()
                    ),
                )
            )

            if current is None:
                updated = HealthProjectionSnapshot(
                    projection_id=self._ids.new_id(
                        "health-projection"
                    ),
                    instance_id=readiness.instance_id,
                    profile_id=readiness.profile_id,
                    availability_state=(
                        evaluation.availability_state
                    ),
                    admission_state=admission_state,
                    readiness_observation_id=(
                        readiness.observation_id
                    ),
                    operational_projection_id=(
                        operational.projection_id
                        if operational is not None
                        else None
                    ),
                    operational_projection_revision=(
                        operational.revision
                        if operational is not None
                        else None
                    ),
                    policy_id=policy.policy_id,
                    policy_revision=policy.revision,
                    cooldown_until=cooldown_until,
                    evidence_refs=evidence_refs,
                    revision=1,
                    created_at=timestamp,
                    updated_at=timestamp,
                    readiness_evaluation=evaluation,
                    sealed_runtime_revision=(
                        sealed_runtime_revision
                    ),
                    adapter_declares_probe_safe=(
                        adapter_declares_probe_safe
                    ),
                )
            else:
                updated = replace(
                    current,
                    availability_state=(
                        evaluation.availability_state
                    ),
                    admission_state=admission_state,
                    readiness_observation_id=(
                        readiness.observation_id
                    ),
                    operational_projection_id=(
                        operational.projection_id
                        if operational is not None
                        else None
                    ),
                    operational_projection_revision=(
                        operational.revision
                        if operational is not None
                        else None
                    ),
                    policy_id=policy.policy_id,
                    policy_revision=policy.revision,
                    cooldown_until=cooldown_until,
                    evidence_refs=evidence_refs,
                    revision=current.revision + 1,
                    updated_at=timestamp,
                    readiness_evaluation=evaluation,
                    sealed_runtime_revision=(
                        sealed_runtime_revision
                    ),
                    adapter_declares_probe_safe=(
                        adapter_declares_probe_safe
                    ),
                )

            persisted = self._write_projection(
                unit,
                current,
                updated,
            )
            self._faults.hit(FaultPoint.BEFORE_COMMIT)
            unit.commit()

        self._faults.hit(FaultPoint.AFTER_COMMIT)
        return persisted

    def classify_and_open_circuit(
        self,
        attempted_trace: (
            tuple[HealthStageObservation, ...]
            | list[HealthStageObservation]
        ),
        *,
        usage_failure_reason: (
            OperationalFailureCategory | None
        ) = None,
        http_status: int | None = None,
        verified_family_evidence: bool | None = None,
        admission_only: bool = False,
        evidence_subject: EvidenceSubject | None,
        receipt: PolicyReceipt | None,
    ) -> PolicyAction | None:
        """Classify a failure and atomically apply its policy action."""

        classification = classify_health_failure(
            attempted_trace,
            usage_failure_reason=usage_failure_reason,
            http_status=http_status,
            verified_family_evidence=(
                verified_family_evidence
            ),
            admission_only=admission_only,
        )
        action = derive_policy_action(
            classification,
            evidence_subject=evidence_subject,
            receipt=receipt,
        )
        if action is None:
            return None

        timestamp = self._clock.now()
        members = self._members_for_scope(
            action.scope,
            action.subject,
        )

        with self._store.unit_of_work() as unit:
            policy = self._require_policy(unit)
            current = unit.get_health_circuit(
                action.scope,
                action.subject,
            )
            updated = reduce_policy_action(
                action,
                current,
                circuit_id=(
                    self._ids.new_id("health-circuit")
                    if current is None
                    else None
                ),
                created_at=(
                    timestamp
                    if current is None
                    else current.created_at
                ),
                updated_at=timestamp,
            )
            self._write_circuit(
                unit,
                current,
                updated,
            )
            self._recompute_members(
                unit,
                members,
                policy=policy,
                now=timestamp,
            )
            self._faults.hit(FaultPoint.BEFORE_COMMIT)
            unit.commit()

        self._faults.hit(FaultPoint.AFTER_COMMIT)
        return action

    def clear_circuit_automatically(
        self,
        scope: PolicyScope,
        subject: str,
        *,
        clearance_receipt: PolicyReceipt | None,
    ) -> AutomaticClearanceResult:
        """Apply receipt-fenced automatic clearance transactionally."""

        timestamp = self._clock.now()
        members = self._members_for_scope(scope, subject)

        with self._store.unit_of_work() as unit:
            policy = self._require_policy(unit)
            current = self._require_circuit(
                unit,
                scope,
                subject,
            )
            result = reduce_automatic_clearance(
                current,
                clearance_receipt=clearance_receipt,
                updated_at=timestamp,
            )
            if result.circuit != current:
                self._write_circuit(
                    unit,
                    current,
                    result.circuit,
                )

            self._recompute_members(
                unit,
                members,
                policy=policy,
                now=timestamp,
            )
            self._faults.hit(FaultPoint.BEFORE_COMMIT)
            unit.commit()

        self._faults.hit(FaultPoint.AFTER_COMMIT)
        return result

    def evaluate_cooldown(
        self,
        scope: PolicyScope,
        subject: str,
    ) -> CooldownEvaluation:
        """Evaluate one circuit and refresh affected projections."""

        timestamp = self._clock.now()
        members = self._members_for_scope(scope, subject)

        with self._store.unit_of_work() as unit:
            policy = self._require_policy(unit)
            circuit = self._require_circuit(
                unit,
                scope,
                subject,
            )
            evaluation = reduce_evaluate_cooldown(
                circuit,
                policy=policy,
                now=timestamp,
            )
            self._recompute_members(
                unit,
                members,
                policy=policy,
                now=timestamp,
            )
            self._faults.hit(FaultPoint.BEFORE_COMMIT)
            unit.commit()

        self._faults.hit(FaultPoint.AFTER_COMMIT)
        return evaluation

    def authorize_recovery(
        self,
        instance_id: str,
        profile_id: str,
        scope: PolicyScope,
        subject: str,
        *,
        authorized_by: str,
    ) -> RecoveryProbeAuthorization:
        """Authorize one receipt-fenced recovery probe."""

        pair = (instance_id, profile_id)
        self._require_configured_pair(pair)
        members = self._members_for_scope(scope, subject)
        if pair not in members:
            raise InvalidMutationError(
                "authorized probe pair is not affected by "
                "the requested health circuit"
            )
        timestamp = self._clock.now()

        with self._store.unit_of_work() as unit:
            policy = self._require_policy(unit)
            projection = self._require_projection(
                unit,
                instance_id,
                profile_id,
            )
            self._require_projection_policy(
                projection,
                policy,
            )
            self._require_projection_baseline(projection)
            circuit = self._require_circuit(
                unit,
                scope,
                subject,
            )

            circuit_admission = reduce_evaluate_cooldown(
                circuit,
                policy=policy,
                now=timestamp,
            )
            if (
                circuit_admission.admission_state
                is not AdmissionState.RECOVERY_REQUIRED
            ):
                raise InvalidMutationError(
                    "recovery probe can be authorized only "
                    "after the target circuit reaches "
                    "RECOVERY_REQUIRED"
                )

            existing = (
                unit.get_live_recovery_probe_grant(
                    circuit.circuit_id
                )
            )
            if existing is not None:
                raise RecoveryProbeGrantConflictError(
                    circuit.circuit_id,
                    existing.grant_id,
                )

            authorization = reduce_authorize_recovery(
                projection,
                circuit,
                grant_id=self._ids.new_id(
                    "recovery-probe-grant"
                ),
                authorized_by=authorized_by,
                authorized_at=timestamp,
                policy=policy,
            )
            unit.add_recovery_probe_grant(
                authorization.grant
            )
            self._faults.hit(
                FaultPoint.AFTER_RECOVERY_GRANT_WRITE
            )

            projections = self._recompute_members(
                unit,
                members,
                policy=policy,
                now=timestamp,
                seed_pair=pair,
                seed_projection=(
                    authorization.projection
                ),
            )
            persisted_authorization = (
                RecoveryProbeAuthorization(
                    projection=projections[pair],
                    circuit=authorization.circuit,
                    grant=authorization.grant,
                )
            )
            self._faults.hit(FaultPoint.BEFORE_COMMIT)
            unit.commit()

        self._faults.hit(FaultPoint.AFTER_COMMIT)
        return persisted_authorization

    def claim_probe(
        self,
        grant_id: str,
        *,
        attempt_id: str,
        claimed_at: int | None = None,
    ) -> RecoveryProbeClaimResult:
        """Contention-safely claim one recovery-probe grant."""

        timestamp = (
            self._clock.now()
            if claimed_at is None
            else claimed_at
        )
        with self._store.unit_of_work() as unit:
            current = self._require_grant(
                unit,
                grant_id,
            )
            result = reduce_claim_probe(
                current,
                attempt_id=attempt_id,
                claimed_at=timestamp,
            )
            if (
                result.disposition
                is ProbeDisposition.REJECTED
            ):
                return result

            if not unit.cas_claim_recovery_probe_grant(
                current,
                result.grant,
            ):
                latest = self._require_grant(
                    unit,
                    grant_id,
                )
                contended = reduce_claim_probe(
                    latest,
                    attempt_id=attempt_id,
                    claimed_at=timestamp,
                )
                if (
                    contended.disposition
                    is not ProbeDisposition.REJECTED
                ):
                    self._raise_grant_cas(
                        unit,
                        current,
                    )
                return contended

            self._faults.hit(
                FaultPoint.AFTER_RECOVERY_GRANT_CAS
            )
            self._faults.hit(FaultPoint.BEFORE_COMMIT)
            unit.commit()

        self._faults.hit(FaultPoint.AFTER_COMMIT)
        return result

    def apply_probe_result(
        self,
        circuit_scope: PolicyScope,
        circuit_subject: str,
        receipt: RecoveryProbeReceipt,
    ) -> RecoveryProbeApplication:
        """Persist a probe receipt and apply its fenced circuit result."""

        timestamp = self._clock.now()
        members = self._members_for_scope(
            circuit_scope,
            circuit_subject,
        )

        with self._store.unit_of_work() as unit:
            policy = self._require_policy(unit)
            current = self._require_circuit(
                unit,
                circuit_scope,
                circuit_subject,
            )
            grant = self._require_grant(
                unit,
                receipt.grant_id,
            )
            if grant.circuit_id != current.circuit_id:
                raise InvalidMutationError(
                    "probe receipt grant belongs to a "
                    "different health circuit"
                )
            if (
                grant.consumed_at is None
                or grant.consumed_by_attempt_id
                != receipt.attempt_id
            ):
                raise InvalidMutationError(
                    "probe receipt does not match the "
                    "claimed recovery-probe attempt"
                )

            application = reduce_probe_result(
                current,
                receipt,
                updated_at=timestamp,
            )
            unit.add_recovery_probe_receipt(receipt)
            self._faults.hit(
                FaultPoint.AFTER_RECOVERY_RECEIPT_WRITE
            )

            if application.circuit != current:
                self._write_circuit(
                    unit,
                    current,
                    application.circuit,
                )
                self._recompute_members(
                    unit,
                    members,
                    policy=policy,
                    now=timestamp,
                )

            self._faults.hit(FaultPoint.BEFORE_COMMIT)
            unit.commit()

        self._faults.hit(FaultPoint.AFTER_COMMIT)
        return application

    def freeze_admission_snapshot(
        self,
    ) -> AdmissionSnapshot:
        """Freeze every configured pair's current live projection."""

        timestamp = self._clock.now()
        with self._store.unit_of_work() as unit:
            policy = self._require_policy(unit)
            entries: list[AdmissionSnapshotEntry] = []

            for (
                instance_id,
                profile_id,
            ) in self._membership.configured_members:
                projection = self._require_projection(
                    unit,
                    instance_id,
                    profile_id,
                )
                self._require_projection_policy(
                    projection,
                    policy,
                )
                self._require_projection_baseline(
                    projection
                )
                entries.append(
                    AdmissionSnapshotEntry(
                        instance_id=instance_id,
                        profile_id=profile_id,
                        health_projection_id=(
                            projection.projection_id
                        ),
                        health_projection_revision=(
                            projection.revision
                        ),
                        availability_state=(
                            projection.availability_state
                        ),
                        admission_state=(
                            projection.admission_state
                        ),
                        evidence_refs=(
                            projection.evidence_refs
                        ),
                    )
                )

            frozen_entries = tuple(entries)
            digest = (
                canonical_admission_snapshot_digest(
                    frozen_entries,
                    configuration_revision=(
                        self._membership
                        .configuration_revision
                    ),
                    configuration_digest=(
                        self._membership
                        .configuration_digest
                    ),
                    policy_id=policy.policy_id,
                    policy_revision=policy.revision,
                )
            )
            snapshot = reduce_freeze_admission(
                frozen_entries,
                snapshot_id=self._ids.new_id(
                    "admission-snapshot"
                ),
                digest=digest,
                configuration_revision=(
                    self._membership.configuration_revision
                ),
                configuration_digest=(
                    self._membership.configuration_digest
                ),
                policy_id=policy.policy_id,
                policy_revision=policy.revision,
                revision=1,
                created_at=timestamp,
            )
            unit.add_admission_snapshot(snapshot)
            self._faults.hit(
                FaultPoint.AFTER_ADMISSION_SNAPSHOT_WRITE
            )
            self._faults.hit(FaultPoint.BEFORE_COMMIT)
            unit.commit()

        self._faults.hit(FaultPoint.AFTER_COMMIT)
        return snapshot
