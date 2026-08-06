"""Fault-boundary tests for every RoutingService write boundary (Step 7)."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from peerhub.core.evidence import EvidenceRef, EvidenceState, EvidenceValue
from peerhub.health.contract import (
    AdmissionSnapshot,
    AdmissionSnapshotEntry,
    AdmissionState,
    AvailabilityState,
    HealthPolicy,
    HealthProjectionSnapshot,
)
from peerhub.persistence.sqlite import SqliteStateStore
from peerhub.routing.contract import (
    ConfigurationSnapshot,
    RouteCandidateInput,
    RouteRequest,
)
from peerhub.routing.service import FaultPoint, RoutingService
from tests.fakes import DeterministicClock, SequentialIdSource


class RaisingFaultInjector:
    """Raise at one exact routing-service transaction boundary."""

    def __init__(self, target: str) -> None:
        self._target = target

    def hit(self, point: str) -> None:
        if point == self._target:
            raise RuntimeError(f"injected fault at {point}")


@pytest.fixture
def store(tmp_path: Path) -> Iterator[SqliteStateStore]:
    state_store = SqliteStateStore(
        tmp_path / "routing-service-faults.sqlite3",
        workspace_home_id="workspace-routing-service-faults",
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


def _health_projection() -> HealthProjectionSnapshot:
    return HealthProjectionSnapshot(
        projection_id="health-projection-01",
        instance_id="ag",
        profile_id="ag.default",
        availability_state=AvailabilityState.HEALTHY,
        admission_state=AdmissionState.OPEN,
        readiness_observation_id=None,
        operational_projection_id=None,
        operational_projection_revision=None,
        policy_id="v1-health-default-r1",
        policy_revision=1,
        cooldown_until=None,
        evidence_refs=(),
        revision=1,
        created_at=100,
        updated_at=100,
    )


def _admission_snapshot() -> AdmissionSnapshot:
    return AdmissionSnapshot(
        snapshot_id="admission-snapshot-01",
        revision=1,
        digest="d" * 64,
        configuration_revision=11,
        configuration_digest="c" * 64,
        policy_id="v1-health-default-r1",
        policy_revision=1,
        entries=(
            AdmissionSnapshotEntry(
                instance_id="ag",
                profile_id="ag.default",
                health_projection_id="health-projection-01",
                health_projection_revision=1,
                availability_state=AvailabilityState.HEALTHY,
                admission_state=AdmissionState.OPEN,
                evidence_refs=(),
            ),
        ),
        created_at=100,
    )


def _absent_usage() -> EvidenceValue:
    return EvidenceValue(
        state=EvidenceState.ABSENT,
        source_tag="empirical_probe",
        provider_id="phase0-usage",
        provider_version="1",
        observed_at=None,
        captured_at=100,
        freshness_ttl=7200,
        evidence_ref=EvidenceRef("sha256:usage-absent"),
        value=None,
    )


def _candidate() -> RouteCandidateInput:
    return RouteCandidateInput(
        candidate_id="candidate-a",
        instance_id="ag",
        representative_profile_id="ag.default",
        eligible=True,
        exclusion_reason=None,
        usage_evidence=_absent_usage(),
        in_flight_reservations=0,
        evidence_refs=(),
    )


def _request(snapshot: AdmissionSnapshot) -> RouteRequest:
    return RouteRequest(
        client_request_id="client-request-01",
        configuration=ConfigurationSnapshot(
            revision=11,
            digest="c" * 64,
        ),
        admission_snapshot=snapshot,
        requested_capabilities=(),
        profile_constraints={},
        required_readiness_binding=None,
        candidates=(_candidate(),),
        routing_policy_id="v1-routing-default-r1",
        routing_policy_revision=1,
    )


def _seed_health_prereqs(store: SqliteStateStore) -> None:
    with store.unit_of_work() as unit:
        unit.add_health_policy_revision(_policy())
        unit.add_health_projection(_health_projection())
        unit.commit()


def _seed_admission_snapshot(
    store: SqliteStateStore,
    snapshot: AdmissionSnapshot,
) -> None:
    with store.unit_of_work() as unit:
        unit.add_admission_snapshot(snapshot)
        unit.commit()


def _service(
    store: SqliteStateStore,
    *,
    start: int = 200,
    fault_point: str | None = None,
    ids: SequentialIdSource | None = None,
) -> RoutingService:
    return RoutingService(
        store,
        clock=DeterministicClock(start=start),
        ids=ids if ids is not None else SequentialIdSource(),
        fault_injector=(
            RaisingFaultInjector(fault_point)
            if fault_point is not None
            else None
        ),
    )


@pytest.mark.parametrize(
    "fault_point",
    (
        FaultPoint.AFTER_ROUTE_DECISION_WRITE,
        FaultPoint.BEFORE_COMMIT,
    ),
)
def test_select_route_write_boundaries_roll_back_completely(
    store: SqliteStateStore,
    fault_point: str,
) -> None:
    _seed_health_prereqs(store)
    snapshot = _admission_snapshot()
    _seed_admission_snapshot(store, snapshot)
    service = _service(store, fault_point=fault_point)

    with pytest.raises(RuntimeError, match=fault_point):
        service.select_route(_request(snapshot))

    with store.unit_of_work() as unit:
        assert unit.get_route_decision("route-decision-1") is None


def test_select_route_after_commit_fault_leaves_durable_decision(
    store: SqliteStateStore,
) -> None:
    _seed_health_prereqs(store)
    snapshot = _admission_snapshot()
    _seed_admission_snapshot(store, snapshot)
    service = _service(
        store, fault_point=FaultPoint.AFTER_COMMIT
    )

    with pytest.raises(RuntimeError, match="AFTER_COMMIT"):
        service.select_route(_request(snapshot))

    with store.unit_of_work() as unit:
        decision = unit.get_route_decision("route-decision-1")

    assert decision is not None
    assert decision.selected_candidate_id == "candidate-a"


def test_replan_write_boundary_rolls_back_new_decision_only(
    store: SqliteStateStore,
) -> None:
    _seed_health_prereqs(store)
    snapshot = _admission_snapshot()
    _seed_admission_snapshot(store, snapshot)
    shared_ids = SequentialIdSource()
    clean = _service(store, ids=shared_ids)
    original = clean.select_route(_request(snapshot))

    drifted_snapshot = AdmissionSnapshot(
        snapshot_id="admission-snapshot-02",
        revision=1,
        digest="d" * 64,
        configuration_revision=12,
        configuration_digest="c" * 64,
        policy_id="v1-health-default-r1",
        policy_revision=1,
        entries=_admission_snapshot().entries,
        created_at=100,
    )
    _seed_admission_snapshot(store, drifted_snapshot)
    drifted_request = RouteRequest(
        client_request_id="client-request-01",
        configuration=ConfigurationSnapshot(
            revision=12,
            digest="c" * 64,
        ),
        admission_snapshot=drifted_snapshot,
        requested_capabilities=(),
        profile_constraints={},
        required_readiness_binding=None,
        candidates=(_candidate(),),
        routing_policy_id="v1-routing-default-r1",
        routing_policy_revision=1,
    )

    faulting = _service(
        store,
        start=500,
        fault_point=FaultPoint.BEFORE_COMMIT,
        ids=shared_ids,
    )
    with pytest.raises(RuntimeError, match="BEFORE_COMMIT"):
        faulting.validate_route_for_dispatch(
            original.decision.decision_id,
            current_request=drifted_request,
        )

    with store.unit_of_work() as unit:
        stale = unit.get_route_decision(
            original.decision.decision_id
        )
        replanned = unit.get_route_decision("route-decision-2")

    assert stale == original.decision
    assert replanned is None
