"""Integration tests for the transactional routing service."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path

import pytest

from peerhub.core.errors import (
    InvalidMutationError,
    RecordNotFoundError,
)
from peerhub.core.evidence import EvidenceRef, EvidenceState, EvidenceValue
from peerhub.core.protocol import ErrorCode
from peerhub.dispatch.capability import CapabilityTier
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
    canonical_route_decision_digest,
    ConfigurationSnapshot,
    RouteCandidateInput,
    RouteRequest,
)
from peerhub.routing.service import FaultPoint, RoutingService
from tests.fakes import DeterministicClock, SequentialIdSource


@pytest.fixture
def store(tmp_path: Path) -> Iterator[SqliteStateStore]:
    state_store = SqliteStateStore(
        tmp_path / "routing-service.sqlite3",
        workspace_home_id="workspace-routing-service",
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


def _health_projection(
    projection_id: str = "health-projection-01",
) -> HealthProjectionSnapshot:
    return HealthProjectionSnapshot(
        projection_id=projection_id,
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


def _admission_snapshot(
    *,
    snapshot_id: str = "admission-snapshot-01",
    revision: int = 1,
    configuration_revision: int = 11,
) -> AdmissionSnapshot:
    return AdmissionSnapshot(
        snapshot_id=snapshot_id,
        revision=revision,
        digest="d" * 64,
        configuration_revision=configuration_revision,
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


def _candidate(
    candidate_id: str,
    *,
    eligible: bool = True,
    exclusion_reason: str | None = None,
    instance_id: str = "ag",
    profile_id: str = "ag.default",
) -> RouteCandidateInput:
    return RouteCandidateInput(
        candidate_id=candidate_id,
        instance_id=instance_id,
        representative_profile_id=profile_id,
        eligible=eligible,
        exclusion_reason=exclusion_reason,
        usage_evidence=_absent_usage(),
        in_flight_reservations=0,
        evidence_refs=(),
    )


def _request(
    *,
    candidates: tuple[RouteCandidateInput, ...],
    client_request_id: str = "client-request-01",
    configuration_revision: int = 11,
    required_capability_tier: CapabilityTier = CapabilityTier.READ_ONLY,
    admission_snapshot: AdmissionSnapshot | None = None,
) -> RouteRequest:
    return RouteRequest(
        client_request_id=client_request_id,
        configuration=ConfigurationSnapshot(
            revision=configuration_revision,
            digest="c" * 64,
        ),
        admission_snapshot=(
            admission_snapshot
            if admission_snapshot is not None
            else _admission_snapshot(
                configuration_revision=configuration_revision
            )
        ),
        required_capability_tier=required_capability_tier,
        requested_capabilities=(),
        profile_constraints={},
        required_readiness_binding=None,
        candidates=candidates,
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
    fault_injector=None,
) -> RoutingService:
    return RoutingService(
        store,
        clock=DeterministicClock(start=start),
        ids=SequentialIdSource(),
        fault_injector=fault_injector,
    )


class _RaisingFaultInjector:
    def __init__(self, point: str) -> None:
        self._point = point
        self.hits: list[str] = []

    def hit(self, point: str) -> None:
        self.hits.append(point)
        if point == self._point:
            raise RuntimeError(f"injected fault at {point}")


def test_select_route_persists_a_successful_decision(
    store: SqliteStateStore,
) -> None:
    snapshot = _admission_snapshot()
    _seed_health_prereqs(store)
    _seed_admission_snapshot(store, snapshot)
    service = _service(store)

    request = _request(
        candidates=(
            _candidate("candidate-a", eligible=True),
            _candidate(
                "candidate-b",
                eligible=False,
                exclusion_reason="ROLE_EXCLUDED",
            ),
        ),
        admission_snapshot=snapshot,
    )

    result = service.select_route(request)

    assert result.error_code is None
    assert result.decision.selected_candidate_id == "candidate-a"

    persisted = service.get_route_decision(
        result.decision.decision_id
    )
    assert persisted == result.decision
    assert (
        persisted.required_capability_tier
        is CapabilityTier.READ_ONLY
    )
    assert canonical_route_decision_digest(result.decision) != (
        canonical_route_decision_digest(
            replace(
                result.decision,
                required_capability_tier=(
                    CapabilityTier.WORKTREE_WRITE
                ),
            )
        )
    )


def test_select_route_persists_exhaustion_when_no_eligible_candidate(
    store: SqliteStateStore,
) -> None:
    snapshot = _admission_snapshot()
    _seed_health_prereqs(store)
    _seed_admission_snapshot(store, snapshot)
    service = _service(store)

    request = _request(
        candidates=(
            _candidate(
                "candidate-a",
                eligible=False,
                exclusion_reason="ROLE_EXCLUDED",
            ),
        ),
        admission_snapshot=snapshot,
    )

    result = service.select_route(request)

    assert result.error_code is ErrorCode.ROUTE_EXHAUSTED
    assert result.decision.selected_candidate_id is None

    persisted = service.get_route_decision(
        result.decision.decision_id
    )
    assert persisted == result.decision


def test_select_route_rejects_missing_admission_snapshot(
    store: SqliteStateStore,
) -> None:
    service = _service(store)
    request = _request(
        candidates=(_candidate("candidate-a"),),
    )

    with pytest.raises(RecordNotFoundError):
        service.select_route(request)


def test_select_route_rejects_mismatched_admission_snapshot(
    store: SqliteStateStore,
) -> None:
    snapshot = _admission_snapshot()
    _seed_health_prereqs(store)
    _seed_admission_snapshot(store, snapshot)
    service = _service(store)

    request = _request(
        candidates=(_candidate("candidate-a"),),
        admission_snapshot=_admission_snapshot(revision=2),
    )

    with pytest.raises(InvalidMutationError):
        service.select_route(request)


def test_validate_route_for_dispatch_permits_no_drift(
    store: SqliteStateStore,
) -> None:
    snapshot = _admission_snapshot()
    _seed_health_prereqs(store)
    _seed_admission_snapshot(store, snapshot)
    service = _service(store)
    request = _request(
        candidates=(_candidate("candidate-a"),),
        admission_snapshot=snapshot,
    )
    result = service.select_route(request)

    outcome = service.validate_route_for_dispatch(
        result.decision.decision_id,
        current_request=request,
    )

    assert outcome.validation.dispatch_permitted is True
    assert outcome.replanned_route is None


def test_validate_route_for_dispatch_replans_on_configuration_drift(
    store: SqliteStateStore,
) -> None:
    snapshot = _admission_snapshot()
    _seed_health_prereqs(store)
    _seed_admission_snapshot(store, snapshot)
    service = _service(store)
    original_request = _request(
        candidates=(_candidate("candidate-a"),),
        admission_snapshot=snapshot,
    )
    original = service.select_route(original_request)

    drifted_snapshot = _admission_snapshot(
        snapshot_id="admission-snapshot-02",
        configuration_revision=12,
    )
    _seed_admission_snapshot(store, drifted_snapshot)
    drifted_request = _request(
        candidates=(_candidate("candidate-a"),),
        configuration_revision=12,
        admission_snapshot=drifted_snapshot,
    )

    outcome = service.validate_route_for_dispatch(
        original.decision.decision_id,
        current_request=drifted_request,
    )

    assert outcome.validation.dispatch_permitted is False
    assert outcome.replanned_route is not None
    assert (
        outcome.replanned_route.decision.decision_id
        != original.decision.decision_id
    )
    assert (
        outcome.replanned_route.decision.configuration.revision
        == 12
    )

    # The original decision row is untouched.
    stale_reread = service.get_route_decision(
        original.decision.decision_id
    )
    assert stale_reread == original.decision


def test_validate_route_for_dispatch_missing_decision_raises(
    store: SqliteStateStore,
) -> None:
    service = _service(store)
    request = _request(candidates=(_candidate("candidate-a"),))

    with pytest.raises(RecordNotFoundError):
        service.validate_route_for_dispatch(
            "nonexistent-decision",
            current_request=request,
        )


def test_validate_route_for_dispatch_rejects_exhausted_decision(
    store: SqliteStateStore,
) -> None:
    snapshot = _admission_snapshot()
    _seed_health_prereqs(store)
    _seed_admission_snapshot(store, snapshot)
    service = _service(store)
    request = _request(
        candidates=(
            _candidate(
                "candidate-a",
                eligible=False,
                exclusion_reason="ROLE_EXCLUDED",
            ),
        ),
        admission_snapshot=snapshot,
    )
    exhausted = service.select_route(request)

    with pytest.raises(ValueError):
        service.validate_route_for_dispatch(
            exhausted.decision.decision_id,
            current_request=request,
        )


def test_select_route_rolls_back_on_fault_before_commit(
    store: SqliteStateStore,
) -> None:
    snapshot = _admission_snapshot()
    _seed_health_prereqs(store)
    _seed_admission_snapshot(store, snapshot)
    injector = _RaisingFaultInjector(FaultPoint.BEFORE_COMMIT)
    service = _service(store, fault_injector=injector)
    request = _request(
        candidates=(_candidate("candidate-a"),),
        admission_snapshot=snapshot,
    )

    with pytest.raises(RuntimeError):
        service.select_route(request)

    with store.unit_of_work() as unit:
        decisions = unit.get_route_decision(
            "route-decision-1"
        )
    assert decisions is None
    assert FaultPoint.BEFORE_COMMIT in injector.hits
