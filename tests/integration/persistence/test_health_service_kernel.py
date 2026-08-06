"""Integration tests for the transactional health/admission service."""

from __future__ import annotations

from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from peerhub.core.errors import (
    InvalidMutationError,
    RecoveryProbeGrantConflictError,
)
from peerhub.core.evidence import EvidenceRef, EvidenceState, EvidenceValue
from peerhub.health.contract import (
    AdmissionState,
    AvailabilityState,
    CircuitState,
    EvidenceSubject,
    HealthPolicy,
    HealthScopeBinding,
    HealthScopeMembershipSnapshot,
    HealthStage,
    HealthStageObservation,
    HealthStageStatus,
    PolicyReceipt,
    PolicyScope,
    ProbeDisposition,
    ProbeResult,
    RecoveryProbeReceipt,
)
from peerhub.health.model import canonical_admission_snapshot_digest
from peerhub.health.service import FaultPoint, HealthService
from peerhub.persistence.sqlite import SqliteStateStore
from peerhub.telemetry.contract import (
    ReadinessMeasurement,
    ReadinessObserved,
)
from peerhub.telemetry.projections import TelemetryProjector
from tests.fakes import DeterministicClock, SequentialIdSource


@pytest.fixture
def store(tmp_path: Path) -> Iterator[SqliteStateStore]:
    state_store = SqliteStateStore(
        tmp_path / "health-service.sqlite3",
        workspace_home_id="workspace-health-service",
    )
    state_store.initialize()
    try:
        yield state_store
    finally:
        state_store.close()


def _policy() -> HealthPolicy:
    return HealthPolicy(
        policy_id="v1-health-default-r1",
        revision=1,
        readiness_freshness_seconds=7200,
        recovery_backoff_seconds=(30, 60, 120, 240, 480, 900),
        recovery_jitter_fraction=0.2,
        readiness_observation_threshold=1,
        administrative_recovery_probe_limit=1,
    )


def _membership() -> HealthScopeMembershipSnapshot:
    return HealthScopeMembershipSnapshot(
        configuration_revision=11,
        configuration_digest="e" * 64,
        configured_members=(
            ("ag", "ag.default"),
            ("cx", "cx.deepthink"),
        ),
        bindings=(
            HealthScopeBinding(
                scope=PolicyScope.QUOTA_FAMILY,
                subject="family-x",
                members=(("ag", "ag.default"),),
            ),
        ),
    )


def _readiness(
    *,
    observation_id: str,
    instance_id: str = "ag",
    profile_id: str = "ag.default",
    observed_at: int = 100,
    valid_until: int = 10_000,
) -> ReadinessObserved:
    return ReadinessObserved(
        observation_id=observation_id,
        instance_id=instance_id,
        profile_id=profile_id,
        evidence=EvidenceValue(
            state=EvidenceState.MEASURED,
            source_tag="empirical_probe",
            provider_id="phase0-readiness",
            provider_version="1",
            observed_at=observed_at,
            captured_at=observed_at,
            freshness_ttl=7200,
            evidence_ref=EvidenceRef(f"sha256:{observation_id}"),
            value=ReadinessMeasurement(
                runtime_revision="runtime-r17",
                issued_at=1,
                valid_until=valid_until,
                integrity_verified=True,
            ),
        ),
    )


def _service(
    store: SqliteStateStore,
    *,
    start: int = 100,
    fault_injector=None,
) -> HealthService:
    telemetry = TelemetryProjector(
        store,
        ids=SequentialIdSource(),
        freshness_ttl=3600,
    )
    return HealthService(
        store,
        telemetry=telemetry,
        policy=_policy(),
        membership=_membership(),
        clock=DeterministicClock(start=start),
        ids=SequentialIdSource(),
        fault_injector=fault_injector,
    )


def _seed_policy(store: SqliteStateStore) -> None:
    with store.unit_of_work() as unit:
        unit.add_health_policy_revision(_policy())
        unit.commit()


def _failing_trace() -> tuple[HealthStageObservation, ...]:
    return (
        HealthStageObservation(
            stage=HealthStage.RESOLVE_EXECUTABLE,
            status=HealthStageStatus.OK,
        ),
        HealthStageObservation(
            stage=HealthStage.VALIDATE_ENVIRONMENT,
            status=HealthStageStatus.FAILED,
        ),
    )


def _receipt(incident: str = "incident-01") -> PolicyReceipt:
    return PolicyReceipt(
        incident=incident,
        gate_generation=1,
        timestamp=100,
        fingerprint="fingerprint-01",
    )


class _RaisingFaultInjector:
    def __init__(self, point: str) -> None:
        self._point = point
        self.hits: list[str] = []

    def hit(self, point: str) -> None:
        self.hits.append(point)
        if point == self._point:
            raise RuntimeError(f"injected fault at {point}")


def test_evaluate_and_persist_readiness_creates_open_projection(
    store: SqliteStateStore,
) -> None:
    _seed_policy(store)
    service = _service(store)

    projection = service.evaluate_and_persist_readiness(
        _readiness(observation_id="readiness-01"),
        sealed_runtime_revision="runtime-r17",
        adapter_declares_probe_safe=True,
    )

    assert projection.admission_state is AdmissionState.OPEN
    assert projection.availability_state is AvailabilityState.HEALTHY
    assert projection.revision == 1
    assert projection.readiness_evaluation is not None
    assert projection.sealed_runtime_revision == "runtime-r17"
    assert projection.adapter_declares_probe_safe is True
    assert projection.evidence_refs == (
        EvidenceRef("sha256:readiness-01"),
    )


def test_evaluate_and_persist_readiness_rejects_unconfigured_pair(
    store: SqliteStateStore,
) -> None:
    _seed_policy(store)
    service = _service(store)

    with pytest.raises(InvalidMutationError):
        service.evaluate_and_persist_readiness(
            _readiness(
                observation_id="readiness-01",
                instance_id="unknown",
                profile_id="unknown.default",
            ),
            sealed_runtime_revision="runtime-r17",
            adapter_declares_probe_safe=True,
        )


def test_evaluate_and_persist_readiness_second_observation_increments_revision(
    store: SqliteStateStore,
) -> None:
    _seed_policy(store)
    service = _service(store)
    service.evaluate_and_persist_readiness(
        _readiness(observation_id="readiness-01"),
        sealed_runtime_revision="runtime-r17",
        adapter_declares_probe_safe=True,
    )

    second = service.evaluate_and_persist_readiness(
        _readiness(observation_id="readiness-02", observed_at=200),
        sealed_runtime_revision="runtime-r17",
        adapter_declares_probe_safe=True,
    )

    assert second.revision == 2
    assert second.readiness_observation_id == "readiness-02"


def test_classify_and_open_circuit_opens_circuit_and_recomputes_projection(
    store: SqliteStateStore,
) -> None:
    _seed_policy(store)
    service = _service(store, start=100)
    service.evaluate_and_persist_readiness(
        _readiness(observation_id="readiness-01"),
        sealed_runtime_revision="runtime-r17",
        adapter_declares_probe_safe=True,
    )

    action = service.classify_and_open_circuit(
        _failing_trace(),
        evidence_subject=EvidenceSubject(
            scope=PolicyScope.PROFILE,
            subject="ag.default",
        ),
        receipt=_receipt(),
    )
    assert action is not None

    with store.unit_of_work() as unit:
        projection = unit.get_health_projection(
            "ag", "ag.default"
        )
        circuit = unit.get_health_circuit(
            PolicyScope.PROFILE, "ag.default"
        )

    assert circuit is not None
    assert projection is not None
    # Freshly opened: retry boundary (updated_at + 30) has not elapsed yet.
    assert projection.admission_state is AdmissionState.COOLDOWN
    assert projection.cooldown_until == circuit.updated_at + 30


def test_evaluate_cooldown_transitions_to_recovery_required_after_backoff(
    store: SqliteStateStore,
) -> None:
    _seed_policy(store)
    service = _service(store, start=100)
    service.evaluate_and_persist_readiness(
        _readiness(observation_id="readiness-01"),
        sealed_runtime_revision="runtime-r17",
        adapter_declares_probe_safe=True,
    )
    service.classify_and_open_circuit(
        _failing_trace(),
        evidence_subject=EvidenceSubject(
            scope=PolicyScope.PROFILE,
            subject="ag.default",
        ),
        receipt=_receipt(),
    )

    later_service = _service(store, start=1_000)
    evaluation = later_service.evaluate_cooldown(
        PolicyScope.PROFILE, "ag.default"
    )
    assert evaluation.admission_state is AdmissionState.RECOVERY_REQUIRED

    with store.unit_of_work() as unit:
        projection = unit.get_health_projection(
            "ag", "ag.default"
        )
    assert projection is not None
    assert (
        projection.admission_state
        is AdmissionState.RECOVERY_REQUIRED
    )
    assert projection.cooldown_until is None


def _open_and_reach_recovery_required(
    store: SqliteStateStore,
) -> None:
    _seed_policy(store)
    service = _service(store, start=100)
    service.evaluate_and_persist_readiness(
        _readiness(observation_id="readiness-01"),
        sealed_runtime_revision="runtime-r17",
        adapter_declares_probe_safe=True,
    )
    service.classify_and_open_circuit(
        _failing_trace(),
        evidence_subject=EvidenceSubject(
            scope=PolicyScope.PROFILE,
            subject="ag.default",
        ),
        receipt=_receipt(),
    )
    later_service = _service(store, start=1_000)
    later_service.evaluate_cooldown(
        PolicyScope.PROFILE, "ag.default"
    )


def test_authorize_recovery_requires_recovery_required_state(
    store: SqliteStateStore,
) -> None:
    _seed_policy(store)
    service = _service(store, start=100)
    service.evaluate_and_persist_readiness(
        _readiness(observation_id="readiness-01"),
        sealed_runtime_revision="runtime-r17",
        adapter_declares_probe_safe=True,
    )
    service.classify_and_open_circuit(
        _failing_trace(),
        evidence_subject=EvidenceSubject(
            scope=PolicyScope.PROFILE,
            subject="ag.default",
        ),
        receipt=_receipt(),
    )

    # Still cooling down: not yet RECOVERY_REQUIRED.
    with pytest.raises(InvalidMutationError):
        service.authorize_recovery(
            "ag",
            "ag.default",
            PolicyScope.PROFILE,
            "ag.default",
            authorized_by="administrator",
        )


def test_full_recovery_cycle_authorize_claim_apply_closes_circuit(
    store: SqliteStateStore,
) -> None:
    _open_and_reach_recovery_required(store)

    recovery_service = _service(store, start=2_000)
    authorization = recovery_service.authorize_recovery(
        "ag",
        "ag.default",
        PolicyScope.PROFILE,
        "ag.default",
        authorized_by="administrator",
    )
    assert (
        authorization.projection.admission_state
        is AdmissionState.PROBE_AUTHORIZED
    )

    claim = recovery_service.claim_probe(
        authorization.grant.grant_id,
        attempt_id="probe-attempt-01",
    )
    assert claim.disposition is ProbeDisposition.EXECUTED

    receipt = RecoveryProbeReceipt(
        probe_receipt_id="probe-receipt-01",
        grant_id=authorization.grant.grant_id,
        attempt_id="probe-attempt-01",
        reported_revision=authorization.circuit.revision,
        reported_receipt=authorization.circuit.receipt,
        result=ProbeResult.SUCCESS,
        observed_at=2_100,
        evidence_refs=(),
    )
    application = recovery_service.apply_probe_result(
        PolicyScope.PROFILE,
        "ag.default",
        receipt,
    )
    assert application.reported_matches_current is True

    with store.unit_of_work() as unit:
        circuit = unit.get_health_circuit(
            PolicyScope.PROFILE, "ag.default"
        )
        projection = unit.get_health_projection(
            "ag", "ag.default"
        )

    assert circuit is not None
    assert circuit.state is CircuitState.CIRCUIT_CLOSED
    assert projection is not None
    assert projection.admission_state is AdmissionState.OPEN


def test_authorize_recovery_conflicts_on_second_live_grant(
    store: SqliteStateStore,
) -> None:
    _open_and_reach_recovery_required(store)

    recovery_service = _service(store, start=2_000)
    recovery_service.authorize_recovery(
        "ag",
        "ag.default",
        PolicyScope.PROFILE,
        "ag.default",
        authorized_by="administrator",
    )

    second_service = _service(store, start=3_000)
    with pytest.raises(RecoveryProbeGrantConflictError):
        second_service.authorize_recovery(
            "ag",
            "ag.default",
            PolicyScope.PROFILE,
            "ag.default",
            authorized_by="administrator",
        )


def test_clear_circuit_automatically_with_matching_receipt(
    store: SqliteStateStore,
) -> None:
    _seed_policy(store)
    service = _service(store, start=100)
    service.evaluate_and_persist_readiness(
        _readiness(observation_id="readiness-01"),
        sealed_runtime_revision="runtime-r17",
        adapter_declares_probe_safe=True,
    )
    receipt = _receipt()
    service.classify_and_open_circuit(
        _failing_trace(),
        evidence_subject=EvidenceSubject(
            scope=PolicyScope.PROFILE,
            subject="ag.default",
        ),
        receipt=receipt,
    )

    clearance_service = _service(store, start=500)
    result = clearance_service.clear_circuit_automatically(
        PolicyScope.PROFILE,
        "ag.default",
        clearance_receipt=receipt,
    )
    assert result.clearance_applied is True

    with store.unit_of_work() as unit:
        projection = unit.get_health_projection(
            "ag", "ag.default"
        )
    assert projection is not None
    assert projection.admission_state is AdmissionState.OPEN


def test_clear_circuit_automatically_rejects_mismatched_receipt(
    store: SqliteStateStore,
) -> None:
    _seed_policy(store)
    service = _service(store, start=100)
    service.evaluate_and_persist_readiness(
        _readiness(observation_id="readiness-01"),
        sealed_runtime_revision="runtime-r17",
        adapter_declares_probe_safe=True,
    )
    service.classify_and_open_circuit(
        _failing_trace(),
        evidence_subject=EvidenceSubject(
            scope=PolicyScope.PROFILE,
            subject="ag.default",
        ),
        receipt=_receipt(incident="incident-01"),
    )

    clearance_service = _service(store, start=500)
    result = clearance_service.clear_circuit_automatically(
        PolicyScope.PROFILE,
        "ag.default",
        clearance_receipt=_receipt(incident="incident-DIFFERENT"),
    )
    assert result.clearance_applied is False
    assert result.reason == "CLEARANCE_RECEIPT_MISMATCH"


def test_quota_family_circuit_propagates_to_bound_profile_projection(
    store: SqliteStateStore,
) -> None:
    _seed_policy(store)
    service = _service(store, start=100)
    service.evaluate_and_persist_readiness(
        _readiness(observation_id="readiness-01"),
        sealed_runtime_revision="runtime-r17",
        adapter_declares_probe_safe=True,
    )

    quota_action = service.classify_and_open_circuit(
        _failing_trace(),
        evidence_subject=EvidenceSubject(
            scope=PolicyScope.QUOTA_FAMILY,
            subject="family-x",
        ),
        receipt=_receipt(),
    )
    assert quota_action is not None

    with store.unit_of_work() as unit:
        profile_circuit = unit.get_health_circuit(
            PolicyScope.PROFILE, "ag.default"
        )
        projection = unit.get_health_projection(
            "ag", "ag.default"
        )

    # No PROFILE-scoped circuit was ever opened directly...
    assert profile_circuit is None
    # ...yet the QUOTA_FAMILY circuit still gates ag.default's projection.
    assert projection is not None
    assert projection.admission_state is AdmissionState.COOLDOWN


def test_freeze_admission_snapshot_covers_every_configured_pair(
    store: SqliteStateStore,
) -> None:
    _seed_policy(store)
    service = _service(store, start=100)
    service.evaluate_and_persist_readiness(
        _readiness(observation_id="readiness-ag"),
        sealed_runtime_revision="runtime-r17",
        adapter_declares_probe_safe=True,
    )
    service.evaluate_and_persist_readiness(
        _readiness(
            observation_id="readiness-cx",
            instance_id="cx",
            profile_id="cx.deepthink",
        ),
        sealed_runtime_revision="runtime-r17",
        adapter_declares_probe_safe=True,
    )

    snapshot = service.freeze_admission_snapshot()

    assert len(snapshot.entries) == 2
    assert tuple(
        (e.instance_id, e.profile_id) for e in snapshot.entries
    ) == (
        ("ag", "ag.default"),
        ("cx", "cx.deepthink"),
    )
    expected_digest = canonical_admission_snapshot_digest(
        snapshot.entries,
        configuration_revision=11,
        configuration_digest=snapshot.configuration_digest,
        policy_id="v1-health-default-r1",
        policy_revision=1,
    )
    assert snapshot.digest == expected_digest


def test_evaluate_and_persist_readiness_rolls_back_on_fault_before_commit(
    store: SqliteStateStore,
) -> None:
    _seed_policy(store)
    injector = _RaisingFaultInjector(FaultPoint.BEFORE_COMMIT)
    service = _service(store, fault_injector=injector)

    with pytest.raises(RuntimeError):
        service.evaluate_and_persist_readiness(
            _readiness(observation_id="readiness-01"),
            sealed_runtime_revision="runtime-r17",
            adapter_declares_probe_safe=True,
        )

    with store.unit_of_work() as unit:
        projection = unit.get_health_projection(
            "ag", "ag.default"
        )
    assert projection is None
    assert FaultPoint.BEFORE_COMMIT in injector.hits


def test_concurrent_circuit_open_for_same_incident_merges_safely(
    store: SqliteStateStore,
) -> None:
    _seed_policy(store)
    service = _service(store)
    service.evaluate_and_persist_readiness(
        _readiness(observation_id="readiness-01"),
        sealed_runtime_revision="runtime-r17",
        adapter_declares_probe_safe=True,
    )

    def open_circuit(_: int):
        return _service(store).classify_and_open_circuit(
            _failing_trace(),
            evidence_subject=EvidenceSubject(
                scope=PolicyScope.PROFILE,
                subject="ag.default",
            ),
            receipt=_receipt(),
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        actions = list(executor.map(open_circuit, (1, 2)))

    assert all(action is not None for action in actions)

    with store.unit_of_work() as unit:
        circuit = unit.get_health_circuit(
            PolicyScope.PROFILE, "ag.default"
        )

    # SQLite's per-transaction exclusive write lock fully serializes
    # HealthService's read-decide-write sequence: exactly one circuit
    # row exists, and the second writer's report of the *same* incident
    # correctly merges onto it (revision 2) rather than racing a
    # duplicate insert or corrupting state.
    assert circuit is not None
    assert circuit.revision == 2
    assert circuit.backoff_count == 0
