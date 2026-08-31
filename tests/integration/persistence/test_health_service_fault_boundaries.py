"""Fault-boundary tests for every HealthService write boundary (Step 7)."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from peerhub.core.evidence import EvidenceRef, EvidenceState, EvidenceValue
from peerhub.health.contract import (
    AdmissionState,
    EvidenceSubject,
    HealthPolicy,
    HealthScopeBinding,
    HealthScopeMembershipSnapshot,
    HealthStage,
    HealthStageObservation,
    HealthStageStatus,
    PolicyReceipt,
    PolicyScope,
    ProbeResult,
    RecoveryGrantState,
    RecoveryProbeReceipt,
)
from peerhub.health.service import FaultPoint, HealthService
from peerhub.persistence.sqlite import SqliteStateStore
from peerhub.telemetry.contract import ReadinessMeasurement, ReadinessObserved
from peerhub.telemetry.projections import TelemetryProjector
from tests.fakes import DeterministicClock, SequentialIdSource


class RaisingFaultInjector:
    """Raise at one exact health-service transaction boundary."""

    def __init__(self, target: str) -> None:
        self._target = target

    def hit(self, point: str) -> None:
        if point == self._target:
            raise RuntimeError(f"injected fault at {point}")


@pytest.fixture
def store(tmp_path: Path) -> Iterator[SqliteStateStore]:
    state_store = SqliteStateStore(
        tmp_path / "health-service-faults.sqlite3",
        workspace_home_id="workspace-health-service-faults",
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
        configured_members=(("ag", "ag.default"),),
        bindings=(),
    )


def _readiness(
    observation_id: str,
    *,
    observed_at: int = 100,
) -> ReadinessObserved:
    return ReadinessObserved(
        observation_id=observation_id,
        instance_id="ag",
        profile_id="ag.default",
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
                valid_until=10_000,
                integrity_verified=True,
            ),
        ),
    )


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


def _service(
    store: SqliteStateStore,
    *,
    start: int = 200,
    fault_point: str | None = None,
    ids: SequentialIdSource | None = None,
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
        ids=ids if ids is not None else SequentialIdSource(),
        fault_injector=(
            RaisingFaultInjector(fault_point)
            if fault_point is not None
            else None
        ),
    )


def _seed_policy(store: SqliteStateStore) -> None:
    with store.unit_of_work() as unit:
        unit.add_health_policy_revision(_policy())
        unit.commit()


@pytest.mark.parametrize(
    "fault_point",
    (
        FaultPoint.AFTER_READINESS_OBSERVATION_WRITE,
        FaultPoint.AFTER_HEALTH_PROJECTION_WRITE,
        FaultPoint.BEFORE_COMMIT,
    ),
)
def test_readiness_write_boundaries_roll_back_completely(
    store: SqliteStateStore,
    fault_point: str,
) -> None:
    _seed_policy(store)
    service = _service(store, fault_point=fault_point)

    with pytest.raises(RuntimeError, match=fault_point):
        service.evaluate_and_persist_readiness(
            _readiness("readiness-01"),
            sealed_runtime_revision="runtime-r17",
            adapter_declares_probe_safe=True,
        )

    with store.unit_of_work() as unit:
        assert unit.get_health_projection("ag", "ag.default") is None
        assert unit.get_readiness_observation("readiness-01") is None

    clean = _service(store, start=500)
    projection = clean.evaluate_and_persist_readiness(
        _readiness("readiness-02"),
        sealed_runtime_revision="runtime-r17",
        adapter_declares_probe_safe=True,
    )
    assert projection.revision == 1


def test_readiness_after_commit_fault_leaves_durable_projection(
    store: SqliteStateStore,
) -> None:
    _seed_policy(store)
    service = _service(
        store, fault_point=FaultPoint.AFTER_COMMIT
    )

    with pytest.raises(RuntimeError, match="AFTER_COMMIT"):
        service.evaluate_and_persist_readiness(
            _readiness("readiness-01"),
            sealed_runtime_revision="runtime-r17",
            adapter_declares_probe_safe=True,
        )

    with store.unit_of_work() as unit:
        projection = unit.get_health_projection("ag", "ag.default")
        observation = unit.get_readiness_observation("readiness-01")

    assert projection is not None
    assert projection.admission_state is AdmissionState.OPEN
    assert observation is not None


@pytest.mark.parametrize(
    "fault_point",
    (
        FaultPoint.AFTER_HEALTH_CIRCUIT_WRITE,
        FaultPoint.AFTER_HEALTH_PROJECTION_CAS,
        FaultPoint.BEFORE_COMMIT,
    ),
)
def test_circuit_open_write_boundaries_roll_back_completely(
    store: SqliteStateStore,
    fault_point: str,
) -> None:
    _seed_policy(store)
    shared_ids = SequentialIdSource()
    clean = _service(store, ids=shared_ids)
    clean.evaluate_and_persist_readiness(
        _readiness("readiness-01"),
        sealed_runtime_revision="runtime-r17",
        adapter_declares_probe_safe=True,
    )

    faulting = _service(
        store,
        start=500,
        fault_point=fault_point,
        ids=shared_ids,
    )
    with pytest.raises(RuntimeError, match=fault_point):
        faulting.classify_and_open_circuit(
            _failing_trace(),
            evidence_subject=EvidenceSubject(
                scope=PolicyScope.PROFILE,
                subject="ag.default",
            ),
            receipt=_receipt(),
        )

    with store.unit_of_work() as unit:
        circuit = unit.get_health_circuit(
            PolicyScope.PROFILE, "ag.default"
        )
        projection = unit.get_health_projection("ag", "ag.default")

    assert circuit is None
    assert projection is not None
    assert projection.admission_state is AdmissionState.OPEN
    assert projection.revision == 1


@pytest.mark.parametrize(
    "fault_point",
    (
        FaultPoint.AFTER_RECOVERY_GRANT_WRITE,
        FaultPoint.BEFORE_COMMIT,
    ),
)
def test_authorize_recovery_write_boundaries_roll_back_completely(
    store: SqliteStateStore,
    fault_point: str,
) -> None:
    _seed_policy(store)
    shared_ids = SequentialIdSource()
    clean = _service(store, ids=shared_ids)
    clean.evaluate_and_persist_readiness(
        _readiness("readiness-01"),
        sealed_runtime_revision="runtime-r17",
        adapter_declares_probe_safe=True,
    )
    clean.classify_and_open_circuit(
        _failing_trace(),
        evidence_subject=EvidenceSubject(
            scope=PolicyScope.PROFILE,
            subject="ag.default",
        ),
        receipt=_receipt(),
    )
    later = _service(store, start=1_000, ids=shared_ids)
    later.evaluate_cooldown(PolicyScope.PROFILE, "ag.default")

    faulting = _service(
        store,
        start=2_000,
        fault_point=fault_point,
        ids=shared_ids,
    )
    with pytest.raises(RuntimeError, match=fault_point):
        faulting.authorize_recovery(
            "ag",
            "ag.default",
            PolicyScope.PROFILE,
            "ag.default",
            authorized_by="administrator",
        )

    with store.unit_of_work() as unit:
        projection = unit.get_health_projection("ag", "ag.default")

    assert (
        projection.admission_state
        is AdmissionState.RECOVERY_REQUIRED
    )


def test_claim_probe_cas_fault_rolls_back_grant_claim(
    store: SqliteStateStore,
) -> None:
    _seed_policy(store)
    shared_ids = SequentialIdSource()
    clean = _service(store, ids=shared_ids)
    clean.evaluate_and_persist_readiness(
        _readiness("readiness-01"),
        sealed_runtime_revision="runtime-r17",
        adapter_declares_probe_safe=True,
    )
    clean.classify_and_open_circuit(
        _failing_trace(),
        evidence_subject=EvidenceSubject(
            scope=PolicyScope.PROFILE,
            subject="ag.default",
        ),
        receipt=_receipt(),
    )
    later = _service(store, start=1_000, ids=shared_ids)
    later.evaluate_cooldown(PolicyScope.PROFILE, "ag.default")
    authorization = later.authorize_recovery(
        "ag",
        "ag.default",
        PolicyScope.PROFILE,
        "ag.default",
        authorized_by="administrator",
    )

    faulting = _service(
        store,
        start=2_000,
        fault_point=FaultPoint.AFTER_RECOVERY_GRANT_CAS,
        ids=shared_ids,
    )
    with pytest.raises(
        RuntimeError, match="AFTER_RECOVERY_GRANT_CAS"
    ):
        faulting.claim_probe(
            authorization.grant.grant_id,
            attempt_id="probe-attempt-01",
            claimed_at=authorization.grant.authorized_at + 1,
        )

    with store.unit_of_work() as unit:
        grant = unit.get_recovery_probe_grant(
            authorization.grant.grant_id
        )
    assert grant is not None
    assert grant.consumed_at is None
    assert grant.state is RecoveryGrantState.GRANTED


@pytest.mark.parametrize(
    "fault_point",
    (
        FaultPoint.AFTER_RECOVERY_RECEIPT_WRITE,
        FaultPoint.AFTER_HEALTH_CIRCUIT_CAS,
        FaultPoint.BEFORE_COMMIT,
    ),
)
def test_apply_probe_result_write_boundaries_roll_back_completely(
    store: SqliteStateStore,
    fault_point: str,
) -> None:
    _seed_policy(store)
    shared_ids = SequentialIdSource()
    clean = _service(store, ids=shared_ids)
    clean.evaluate_and_persist_readiness(
        _readiness("readiness-01"),
        sealed_runtime_revision="runtime-r17",
        adapter_declares_probe_safe=True,
    )
    clean.classify_and_open_circuit(
        _failing_trace(),
        evidence_subject=EvidenceSubject(
            scope=PolicyScope.PROFILE,
            subject="ag.default",
        ),
        receipt=_receipt(),
    )
    later = _service(store, start=1_000, ids=shared_ids)
    later.evaluate_cooldown(PolicyScope.PROFILE, "ag.default")
    authorization = later.authorize_recovery(
        "ag",
        "ag.default",
        PolicyScope.PROFILE,
        "ag.default",
        authorized_by="administrator",
    )
    claim = later.claim_probe(
        authorization.grant.grant_id,
        attempt_id="probe-attempt-01",
    )

    faulting = _service(
        store,
        start=2_000,
        fault_point=fault_point,
        ids=shared_ids,
    )
    receipt = RecoveryProbeReceipt(
        probe_receipt_id="probe-receipt-01",
        grant_id=claim.grant.grant_id,
        attempt_id="probe-attempt-01",
        reported_revision=authorization.circuit.revision,
        reported_receipt=authorization.circuit.receipt,
        result=ProbeResult.SUCCESS,
        observed_at=2_100,
        evidence_refs=(),
    )
    with pytest.raises(RuntimeError, match=fault_point):
        faulting.apply_probe_result(
            PolicyScope.PROFILE,
            "ag.default",
            receipt,
        )

    with store.unit_of_work() as unit:
        circuit = unit.get_health_circuit(
            PolicyScope.PROFILE, "ag.default"
        )
        persisted_receipt = unit.get_recovery_probe_receipt(
            "probe-receipt-01"
        )
        grant = unit.get_recovery_probe_grant(
            authorization.grant.grant_id
        )

    from peerhub.health.contract import CircuitState

    assert circuit.state is CircuitState.CIRCUIT_OPEN
    assert persisted_receipt is None
    assert grant is not None
    assert grant.state is RecoveryGrantState.CLAIMED


@pytest.mark.parametrize(
    "fault_point",
    (
        FaultPoint.AFTER_ADMISSION_SNAPSHOT_WRITE,
        FaultPoint.BEFORE_COMMIT,
    ),
)
def test_freeze_admission_snapshot_write_boundaries_roll_back_completely(
    store: SqliteStateStore,
    fault_point: str,
) -> None:
    _seed_policy(store)
    shared_ids = SequentialIdSource()
    clean = _service(store, ids=shared_ids)
    clean.evaluate_and_persist_readiness(
        _readiness("readiness-01"),
        sealed_runtime_revision="runtime-r17",
        adapter_declares_probe_safe=True,
    )

    faulting = _service(
        store,
        start=1_000,
        fault_point=fault_point,
        ids=shared_ids,
    )
    with pytest.raises(RuntimeError, match=fault_point):
        faulting.freeze_admission_snapshot()

    with store.unit_of_work() as unit:
        assert (
            unit.get_admission_snapshot("admission-snapshot-1")
            is None
        )
