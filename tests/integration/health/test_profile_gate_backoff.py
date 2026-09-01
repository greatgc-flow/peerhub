import pytest
from peerhub.core.context import Clock, IdSource
from peerhub.health.service import HealthService
from peerhub.health.contract import AdmissionState, AvailabilityState, AdmissionSnapshotEntry, AdmissionSnapshot
from peerhub.application.direct_ask import _DirectAskRouteRequestFactory, DirectAskRequest
from peerhub.routing.contract import RouteRequest
from peerhub.dispatch.capability import CapabilityTier
from pathlib import Path
from peerhub.persistence.sqlite import SqliteStateStore
from peerhub.telemetry.projections import TelemetryProjector
from peerhub.health.contract import HealthPolicy, HealthScopeMembershipSnapshot
from tests.fakes import SequentialIdSource

class FixedClock(Clock):
    def __init__(self, value: int = 100_000) -> None:
        self.value = value

    def now(self) -> int:
        return self.value

@pytest.fixture
def clock() -> FixedClock:
    return FixedClock()

@pytest.fixture
def ids() -> SequentialIdSource:
    return SequentialIdSource()

@pytest.fixture
def sqlite_health_service(tmp_path: Path, clock: FixedClock, ids: SequentialIdSource) -> HealthService:
    store = SqliteStateStore(tmp_path / "test.db", workspace_home_id="test_home")
    store.initialize()
    policy = HealthPolicy(
        policy_id="test-pol",
        revision=1,
        readiness_freshness_seconds=3600,
        recovery_backoff_seconds=(30, 60),
        recovery_jitter_fraction=0.0,
        readiness_observation_threshold=1,
        administrative_recovery_probe_limit=1,
    )
    membership = HealthScopeMembershipSnapshot(
        configuration_revision=1,
        configuration_digest="0" * 64,
        configured_members=(),
        bindings=(),
    )
    with store.unit_of_work() as unit:
        unit.add_health_policy_revision(policy)
        unit.commit()
    return HealthService(
        store,
        telemetry=TelemetryProjector(store, ids=ids, freshness_ttl=3600),
        policy=policy,
        membership=membership,
        clock=clock,
        ids=ids,
    )

def test_apply_and_check_backoff(
    sqlite_health_service: HealthService,
    clock: Clock,
) -> None:
    # 1. Initially not backed off
    assert not sqlite_health_service.is_profile_gate_backed_off("prof_1", evaluated_at=clock.now())
    
    # 2. Apply backoff
    sqlite_health_service.apply_transient_backoff("prof_1", 300, "rate_limit")
    
    # 3. Check within window
    assert sqlite_health_service.is_profile_gate_backed_off("prof_1", evaluated_at=clock.now() + 100)
    
    # 4. Check after expiration (self-clearing)
    assert not sqlite_health_service.is_profile_gate_backed_off("prof_1", evaluated_at=clock.now() + 301)

def test_repeated_calls_extend_never_shorten(
    sqlite_health_service: HealthService,
    clock: Clock,
) -> None:
    # 1. Apply backoff for 300 seconds
    sqlite_health_service.apply_transient_backoff("prof_1", 300, "rate_limit")
    assert sqlite_health_service.is_profile_gate_backed_off("prof_1", evaluated_at=clock.now() + 299)
    assert not sqlite_health_service.is_profile_gate_backed_off("prof_1", evaluated_at=clock.now() + 301)
    
    # 2. Attempt to shorten to 10 seconds - should be ignored (take max)
    sqlite_health_service.apply_transient_backoff("prof_1", 10, "shorten_attempt")
    assert sqlite_health_service.is_profile_gate_backed_off("prof_1", evaluated_at=clock.now() + 299)
    
    # 3. Extend to 600 seconds
    sqlite_health_service.apply_transient_backoff("prof_1", 600, "extend")
    assert sqlite_health_service.is_profile_gate_backed_off("prof_1", evaluated_at=clock.now() + 599)
    assert not sqlite_health_service.is_profile_gate_backed_off("prof_1", evaluated_at=clock.now() + 601)

from peerhub.health.contract import HealthProjectionSnapshot

def test_composition_with_quarantine(
    sqlite_health_service: HealthService,
    clock: Clock,
) -> None:
    # Force a quarantined projection
    with sqlite_health_service._store.unit_of_work() as unit:
        unit.add_health_projection(
            HealthProjectionSnapshot(
                projection_id="proj_1",
                revision=1,
                instance_id="inst_1",
                profile_id="prof_1",
                readiness_observation_id=None,
                operational_projection_id=None,
                operational_projection_revision=None,
                availability_state=AvailabilityState.HEALTHY,
                admission_state=AdmissionState.COOLDOWN,
                policy_id="test-pol",
                policy_revision=1,
                cooldown_until=None,
                evidence_refs=(),
                created_at=clock.now(),
                updated_at=clock.now(),
            )
        )
        unit.commit()
    
    # Verify quarantined
    read = sqlite_health_service.read_health_projection("inst_1", "prof_1")
    assert read is not None
    assert read.effective_admission_state == AdmissionState.COOLDOWN
    assert not read.profile_gate_backed_off
    
    # Apply backoff
    sqlite_health_service.apply_transient_backoff("prof_1", 300, "rate_limit")
    
    # Now both are active
    read = sqlite_health_service.read_health_projection("inst_1", "prof_1")
    assert read is not None
    assert read.effective_admission_state == AdmissionState.COOLDOWN
    assert read.profile_gate_backed_off
    
    # Expire backoff -> still quarantined
    read_future = sqlite_health_service.read_health_projection("inst_1", "prof_1", evaluated_at=clock.now() + 301)
    assert read_future is not None
    assert read_future.effective_admission_state == AdmissionState.COOLDOWN
    assert not read_future.profile_gate_backed_off

def test_routing_eligibility_exclusion(
    clock: Clock,
    ids: IdSource,
) -> None:
    class FakeProfile:
        profile_id = "prof_1"
    class FakeAdapter:
        pass
    class FakeTarget:
        peer_kind = "inst_1"
        profile = FakeProfile()
        adapter = FakeAdapter()

    factory = _DirectAskRouteRequestFactory(
        target=FakeTarget(), # type: ignore
        clock=clock,
        ids=ids,
        client_request_id="req_1",
        policy_id="pol_1",
        policy_revision=1,
        required_capability_tier=CapabilityTier.READ_ONLY,
    )
    
    # 1. Eligible (OPEN, not backed off)
    snapshot = AdmissionSnapshot(
        snapshot_id="snap1",
        revision=1,
        digest="0" * 64,
        policy_id="pol_1",
        policy_revision=1,
        created_at=clock.now(),
        configuration_revision=1,
        configuration_digest="0" * 64,
        entries=(
            AdmissionSnapshotEntry(
                instance_id="inst_1",
                profile_id="prof_1",
                health_projection_id="proj_1",
                health_projection_revision=1,
                availability_state=AvailabilityState.HEALTHY,
                admission_state=AdmissionState.OPEN,
                evidence_refs=(),
                profile_gate_backed_off=False,
            ),
        ),
    )
    route_req = factory(snapshot)
    assert route_req.candidates[0].eligible
    
    # 2. Ineligible because backed off
    snapshot_backed_off = AdmissionSnapshot(
        snapshot_id="snap2",
        revision=1,
        digest="0" * 64,
        policy_id="pol_1",
        policy_revision=1,
        created_at=clock.now(),
        configuration_revision=1,
        configuration_digest="0" * 64,
        entries=(
            AdmissionSnapshotEntry(
                instance_id="inst_1",
                profile_id="prof_1",
                health_projection_id="proj_1",
                health_projection_revision=1,
                availability_state=AvailabilityState.HEALTHY,
                admission_state=AdmissionState.OPEN,
                evidence_refs=(),
                profile_gate_backed_off=True,
            ),
        ),
    )
    route_req_backed_off = factory(snapshot_backed_off)
    assert not route_req_backed_off.candidates[0].eligible
