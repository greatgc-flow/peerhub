"""Integration tests for budgeted administrative recovery authorization."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path

import pytest

from peerhub.core.errors import (
    ActorUnauthorizedError,
    AdministrativeRecoveryBudgetExceededError,
    InvalidMutationError,
    RecoveryProbeGrantConflictError,
)
from peerhub.core.evidence import EvidenceRef, EvidenceState, EvidenceValue
from peerhub.core.identity import AuthenticatedSubject
from peerhub.health.contract import (
    AdmissionState,
    CircuitState,
    EvidenceSubject,
    HealthPolicy,
    HealthScopeMembershipSnapshot,
    HealthStage,
    HealthStageObservation,
    HealthStageStatus,
    PolicyReceipt,
    PolicyScope,
    ProbeDisposition,
    ProbeResult,
    QuarantineAuthorityClass,
    RecoveryAuthorizationMode,
    RecoveryGrantState,
    RecoveryProbeReceipt,
)
from peerhub.health.service import HealthService
from peerhub.persistence.sqlite import SqliteStateStore
from peerhub.telemetry.contract import ReadinessMeasurement, ReadinessObserved
from peerhub.telemetry.projections import TelemetryProjector
from tests.fakes import SequentialIdSource


class _MutableClock:
    def __init__(self, value: int) -> None:
        self.value = value

    def now(self) -> int:
        return self.value


@pytest.fixture
def store(tmp_path: Path) -> Iterator[SqliteStateStore]:
    state_store = SqliteStateStore(
        tmp_path / "administrative-recovery.sqlite3",
        workspace_home_id="workspace-administrative-recovery",
    )
    state_store.initialize()
    try:
        yield state_store
    finally:
        state_store.close()


def _policy(*, limit: int = 3) -> HealthPolicy:
    return HealthPolicy(
        policy_id="v1-health-default-r1",
        revision=1,
        readiness_freshness_seconds=7200,
        recovery_backoff_seconds=(30, 60, 120),
        recovery_jitter_fraction=0.0,
        readiness_observation_threshold=1,
        administrative_recovery_probe_limit=limit,
    )


def _readiness() -> ReadinessObserved:
    return ReadinessObserved(
        observation_id="readiness-administrative",
        instance_id="ag",
        profile_id="ag.default",
        evidence=EvidenceValue(
            state=EvidenceState.MEASURED,
            source_tag="empirical_probe",
            provider_id="phase0-readiness",
            provider_version="1",
            observed_at=100,
            captured_at=100,
            freshness_ttl=7200,
            evidence_ref=EvidenceRef("sha256:readiness-administrative"),
            value=ReadinessMeasurement(
                runtime_revision="runtime-r17",
                issued_at=1,
                valid_until=100_000,
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


def _receipt() -> PolicyReceipt:
    return PolicyReceipt(
        incident="incident-administrative",
        gate_generation=1,
        timestamp=100,
        fingerprint="fingerprint-administrative",
    )


def _operator() -> AuthenticatedSubject:
    return AuthenticatedSubject(
        principal_id="local-cli:test-operator",
        evidence_source="test-process-owner",
    )


def _seed_open_circuit(
    store: SqliteStateStore,
    *,
    authority: QuarantineAuthorityClass,
    limit: int = 3,
) -> tuple[HealthService, _MutableClock]:
    policy = _policy(limit=limit)
    membership = HealthScopeMembershipSnapshot(
        configuration_revision=1,
        configuration_digest="a" * 64,
        configured_members=(("ag", "ag.default"),),
        bindings=(),
    )
    clock = _MutableClock(100)
    service = HealthService(
        store,
        telemetry=TelemetryProjector(
            store,
            ids=SequentialIdSource(),
            freshness_ttl=3600,
        ),
        policy=policy,
        membership=membership,
        clock=clock,
        ids=SequentialIdSource(),
    )
    with store.unit_of_work() as unit:
        unit.add_health_policy_revision(policy)
        unit.commit()
    service.evaluate_and_persist_readiness(
        _readiness(),
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
    if authority is not QuarantineAuthorityClass.AUTOMATIC:
        with store.unit_of_work() as unit:
            current = unit.get_health_circuit(
                PolicyScope.PROFILE,
                "ag.default",
            )
            assert current is not None
            updated = replace(
                current,
                quarantine_authority_class=authority,
                revision=current.revision + 1,
            )
            assert unit.cas_update_health_circuit(current, updated)
            unit.commit()
        evaluation = service.evaluate_cooldown(
            PolicyScope.PROFILE,
            "ag.default",
        )
        assert evaluation.admission_state is AdmissionState.QUARANTINED
    return service, clock


def _complete_probe(
    service: HealthService,
    clock: _MutableClock,
    authorization,
    *,
    result: ProbeResult,
    at: int,
) -> None:
    claim = service.claim_probe(
        authorization.grant.grant_id,
        attempt_id=f"attempt-{at}",
        claimed_at=at,
    )
    assert claim.disposition is ProbeDisposition.EXECUTED
    clock.value = at + 1
    service.apply_probe_result(
        PolicyScope.PROFILE,
        "ag.default",
        RecoveryProbeReceipt(
            probe_receipt_id=f"receipt-{at}",
            grant_id=authorization.grant.grant_id,
            attempt_id=f"attempt-{at}",
            reported_revision=authorization.circuit.revision,
            reported_receipt=authorization.circuit.receipt,
            result=result,
            observed_at=at + 1,
            evidence_refs=(),
        ),
    )


def test_administrative_authorization_creates_budgeted_grant(
    store: SqliteStateStore,
) -> None:
    service, _ = _seed_open_circuit(
        store,
        authority=QuarantineAuthorityClass.MANUAL,
    )

    authorization = service.authorize_administrative_recovery(
        "ag",
        "ag.default",
        subject=_operator(),
        reason="operator requested a measured recovery probe",
        requested_at=1_000,
    )

    assert authorization.projection.admission_state is AdmissionState.PROBE_AUTHORIZED
    assert authorization.grant.authorization_mode is RecoveryAuthorizationMode.ADMINISTRATIVE
    assert authorization.grant.state is RecoveryGrantState.GRANTED
    assert authorization.grant.authorized_by == "local-cli:test-operator"
    assert authorization.grant.authorized_circuit_revision == authorization.circuit.revision
    with store.unit_of_work() as unit:
        budget = unit.get_administrative_recovery_budget("workspace")
        projection = unit.get_health_projection("ag", "ag.default")
    assert budget is not None
    assert budget.window_start == 1_000
    assert budget.count == 1
    assert projection is not None
    assert projection.admission_state is AdmissionState.PROBE_AUTHORIZED


def test_administrative_authorization_rejects_automatic_circuit(
    store: SqliteStateStore,
) -> None:
    service, _ = _seed_open_circuit(
        store,
        authority=QuarantineAuthorityClass.AUTOMATIC,
    )

    with pytest.raises(InvalidMutationError, match="non-automatic"):
        service.authorize_administrative_recovery(
            "ag",
            "ag.default",
            subject=_operator(),
            reason="not eligible",
            requested_at=1_000,
        )


def test_administrative_authorization_requires_authenticated_subject(
    store: SqliteStateStore,
) -> None:
    service, _ = _seed_open_circuit(
        store,
        authority=QuarantineAuthorityClass.MANUAL,
    )

    with pytest.raises(ActorUnauthorizedError):
        service.authorize_administrative_recovery(
            "ag",
            "ag.default",
            subject=object(),  # type: ignore[arg-type]
            reason="forged caller",
            requested_at=1_000,
        )

    with store.unit_of_work() as unit:
        assert unit.get_administrative_recovery_budget("workspace") is None


def test_administrative_authorization_budget_exhaustion_is_side_effect_free(
    store: SqliteStateStore,
) -> None:
    service, clock = _seed_open_circuit(
        store,
        authority=QuarantineAuthorityClass.MANUAL,
        limit=2,
    )
    for requested_at in (1_000, 1_100):
        authorization = service.authorize_administrative_recovery(
            "ag",
            "ag.default",
            subject=_operator(),
            reason="retry after measured failure",
            requested_at=requested_at,
        )
        _complete_probe(
            service,
            clock,
            authorization,
            result=ProbeResult.FAILURE,
            at=requested_at + 1,
        )

    with pytest.raises(
        AdministrativeRecoveryBudgetExceededError,
        match="budget_exceeded",
    ) as excinfo:
        service.authorize_administrative_recovery(
            "ag",
            "ag.default",
            subject=_operator(),
            reason="one attempt too many",
            requested_at=1_200,
        )

    assert excinfo.value.details["reason"] == "budget_exceeded"
    with store.unit_of_work() as unit:
        budget = unit.get_administrative_recovery_budget("workspace")
        circuit = unit.get_health_circuit(
            PolicyScope.PROFILE,
            "ag.default",
        )
        assert circuit is not None
        live = unit.get_live_recovery_probe_grant(
            circuit.circuit_id
        )
    assert budget is not None
    assert budget.count == 2
    assert live is None


def test_administrative_authorization_enforces_single_flight(
    store: SqliteStateStore,
) -> None:
    service, _ = _seed_open_circuit(
        store,
        authority=QuarantineAuthorityClass.MANUAL,
    )
    first = service.authorize_administrative_recovery(
        "ag",
        "ag.default",
        subject=_operator(),
        reason="first probe",
        requested_at=1_000,
    )
    claim = service.claim_probe(
        first.grant.grant_id,
        attempt_id="attempt-still-running",
        claimed_at=1_001,
    )
    assert claim.grant.state is RecoveryGrantState.CLAIMED

    with pytest.raises(RecoveryProbeGrantConflictError) as excinfo:
        service.authorize_administrative_recovery(
            "ag",
            "ag.default",
            subject=_operator(),
            reason="parallel probe",
            requested_at=1_002,
        )

    assert excinfo.value.current_grant_id == first.grant.grant_id
    with store.unit_of_work() as unit:
        budget = unit.get_administrative_recovery_budget("workspace")
    assert budget is not None
    assert budget.count == 1


def test_administrative_recovery_full_probe_cycle_closes_circuit(
    store: SqliteStateStore,
) -> None:
    service, clock = _seed_open_circuit(
        store,
        authority=QuarantineAuthorityClass.MANUAL,
    )
    authorization = service.authorize_administrative_recovery(
        "ag",
        "ag.default",
        subject=_operator(),
        reason="prove recovery",
        requested_at=1_000,
    )

    _complete_probe(
        service,
        clock,
        authorization,
        result=ProbeResult.SUCCESS,
        at=1_001,
    )

    with store.unit_of_work() as unit:
        circuit = unit.get_health_circuit(
            PolicyScope.PROFILE,
            "ag.default",
        )
        projection = unit.get_health_projection("ag", "ag.default")
        grant = unit.get_recovery_probe_grant(
            authorization.grant.grant_id
        )
    assert circuit is not None
    assert circuit.state is CircuitState.CIRCUIT_CLOSED
    assert projection is not None
    assert projection.admission_state is AdmissionState.OPEN
    assert grant is not None
    assert grant.state is RecoveryGrantState.SUCCEEDED


def test_administrative_budget_resets_at_anchored_window_boundary(
    store: SqliteStateStore,
) -> None:
    service, clock = _seed_open_circuit(
        store,
        authority=QuarantineAuthorityClass.MANUAL,
        limit=1,
    )
    first = service.authorize_administrative_recovery(
        "ag",
        "ag.default",
        subject=_operator(),
        reason="first window",
        requested_at=1_000,
    )
    _complete_probe(
        service,
        clock,
        first,
        result=ProbeResult.FAILURE,
        at=1_001,
    )

    second = service.authorize_administrative_recovery(
        "ag",
        "ag.default",
        subject=_operator(),
        reason="next anchored window",
        requested_at=19_000,
    )

    assert second.grant.state is RecoveryGrantState.GRANTED
    with store.unit_of_work() as unit:
        budget = unit.get_administrative_recovery_budget("workspace")
    assert budget is not None
    assert budget.window_start == 19_000
    assert budget.count == 1
    assert budget.revision == 2
