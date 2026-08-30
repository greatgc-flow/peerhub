from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from peerhub.application.peer_registry import PeerRegistryService
from peerhub.application.role_assignment import (
    RoleAssigneeUnavailableError,
    RoleAssignmentService,
    RoleReleaseDisposition,
)
from peerhub.core.context import Clock
from peerhub.core.errors import InvalidMutationError
from peerhub.governance.broker import GovernanceBroker
from peerhub.governance.contract import OutboxState
from peerhub.health.contract import (
    AdmissionState,
    AvailabilityState,
    HealthPolicy,
    HealthProjectionSnapshot,
    HealthScopeMembershipSnapshot,
)
from peerhub.health.service import HealthService
from peerhub.persistence.sqlite import SqliteStateStore
from peerhub.telemetry.projections import TelemetryProjector
from peerhub.telemetry.contract import (
    EvidenceRef,
    EvidenceState,
    EvidenceValue,
    ReadinessMeasurement,
    ReadinessObserved,
)
from tests.fakes import SequentialIdSource


class FixedClock(Clock):
    def __init__(self, value: int = 10_000) -> None:
        self.value = value

    def now(self) -> int:
        return self.value


@pytest.fixture
def services(
    tmp_path: Path,
) -> Iterator[
    tuple[
        RoleAssignmentService,
        GovernanceBroker,
        HealthService,
        SqliteStateStore,
        FixedClock,
    ]
]:
    store = SqliteStateStore(
        tmp_path / "role-assignment.sqlite3",
        workspace_home_id="role-assignment-test",
    )
    store.initialize()
    clock = FixedClock()
    ids = SequentialIdSource()
    broker = GovernanceBroker(store, clock=clock, ids=ids)
    peer_registry = PeerRegistryService(broker, clock=clock, ids=ids)
    policy = HealthPolicy(
        policy_id="role-assignment-health-v1",
        revision=1,
        readiness_freshness_seconds=7200,
        recovery_backoff_seconds=(30, 60),
        recovery_jitter_fraction=0.0,
        readiness_observation_threshold=1,
        administrative_recovery_probe_limit=1,
    )
    with store.unit_of_work() as unit:
        unit.add_health_policy_revision(policy)
        unit.commit()
    telemetry = TelemetryProjector(
        store,
        ids=ids,
        freshness_ttl=7200,
    )
    health = HealthService(
        store,
        telemetry=telemetry,
        policy=policy,
        membership=HealthScopeMembershipSnapshot(
            configuration_revision=1,
            configuration_digest="a" * 64,
            configured_members=(("cc", "cc.standard"),),
            bindings=(),
        ),
        clock=clock,
        ids=ids,
    )
    service = RoleAssignmentService(
        broker,
        peer_registry=peer_registry,
        health=health,
        clock=clock,
        ids=ids,
    )
    try:
        yield service, broker, health, store, clock
    finally:
        store.close()


def _seed_projection(
    store: SqliteStateStore,
    health: HealthService,
    *,
    availability: AvailabilityState,
    admission: AdmissionState,
    updated_at: int = 10_000,
) -> HealthProjectionSnapshot:
    import uuid
    obs_id = "obs-" + uuid.uuid4().hex[:8]
    valid_until = updated_at + health.policy.readiness_freshness_seconds
    readiness = ReadinessObserved(
        observation_id=obs_id,
        instance_id="cc",
        profile_id="cc.standard",
        evidence=EvidenceValue(
            state=EvidenceState.MEASURED,
            source_tag="test",
            provider_id="test",
            provider_version="1",
            observed_at=updated_at,
            captured_at=updated_at,
            freshness_ttl=health.policy.readiness_freshness_seconds,
            evidence_ref=EvidenceRef("sha256:00"),
            value=ReadinessMeasurement(
                runtime_revision="rev",
                issued_at=updated_at,
                valid_until=valid_until,
                integrity_verified=True,
            )
        )
    )
    
    projection = HealthProjectionSnapshot(
        projection_id="health-projection-1",
        instance_id="cc",
        profile_id="cc.standard",
        availability_state=availability,
        admission_state=admission,
        readiness_observation_id=obs_id,
        operational_projection_id=None,
        operational_projection_revision=None,
        policy_id=health.policy.policy_id,
        policy_revision=health.policy.revision,
        cooldown_until=None,
        evidence_refs=(obs_id,),
        revision=1,
        created_at=updated_at,
        updated_at=updated_at,
    )
    with store.unit_of_work() as unit:
        unit.add_readiness_observation(readiness)
        unit.add_health_projection(projection)
        unit.commit()
    return projection


def _governance_event_count(store: SqliteStateStore) -> int:
    with store.read_unit_of_work() as unit:
        return len(
            unit.list_outbox_events(
                (OutboxState.PENDING,),
                limit=100,
                governance_only=True,
            )
        )


def test_assignment_succeeds_without_health_projection(
    services: tuple[
        RoleAssignmentService,
        GovernanceBroker,
        HealthService,
        SqliteStateStore,
        FixedClock,
    ],
) -> None:
    service, _, _, _, _ = services

    service.assign_role(
        role="implementer",
        peer_node_id="cc",
        actor_id="operator-1",
    )

    target = service.get_role("implementer")
    assert target is not None
    assert target.state["peer_node_id"] == "cc"
    assert target.state["health_basis"] == {
        "projection_id": None,
        "projection_revision": None,
        "availability_state": None,
        "admission_state": None,
    }


def test_assignment_succeeds_with_healthy_open_projection(
    services: tuple[
        RoleAssignmentService,
        GovernanceBroker,
        HealthService,
        SqliteStateStore,
        FixedClock,
    ],
) -> None:
    service, _, health, store, _ = services
    projection = _seed_projection(
        store,
        health,
        availability=AvailabilityState.HEALTHY,
        admission=AdmissionState.OPEN,
    )

    service.assign_role(
        role="reviewer",
        peer_node_id="cc",
        actor_id="operator-1",
    )

    target = service.get_role("reviewer")
    assert target is not None
    assert target.state["health_basis"] == {
        "projection_id": projection.projection_id,
        "projection_revision": projection.revision,
        "availability_state": "HEALTHY",
        "admission_state": "OPEN",
    }


@pytest.mark.parametrize(
    "availability",
    (AvailabilityState.UNAVAILABLE, AvailabilityState.STALE),
)
def test_assignment_rejects_denied_availability_without_mutation(
    services: tuple[
        RoleAssignmentService,
        GovernanceBroker,
        HealthService,
        SqliteStateStore,
        FixedClock,
    ],
    availability: AvailabilityState,
) -> None:
    service, broker, health, store, _ = services
    _seed_projection(
        store,
        health,
        availability=availability,
        admission=AdmissionState.OPEN,
    )

    with pytest.raises(RoleAssigneeUnavailableError) as raised:
        service.assign_role(
            role="reviewer",
            peer_node_id="cc",
            actor_id="operator-1",
        )

    assert raised.value.availability_state is availability
    assert broker.get_target("role-assignment:reviewer") is None
    assert _governance_event_count(store) == 0


@pytest.mark.parametrize(
    "admission",
    (
        AdmissionState.QUARANTINED,
        AdmissionState.COOLDOWN,
        AdmissionState.RECOVERY_REQUIRED,
    ),
)
def test_assignment_rejects_denied_admission_without_mutation(
    services: tuple[
        RoleAssignmentService,
        GovernanceBroker,
        HealthService,
        SqliteStateStore,
        FixedClock,
    ],
    admission: AdmissionState,
) -> None:
    service, broker, health, store, _ = services
    _seed_projection(
        store,
        health,
        availability=AvailabilityState.HEALTHY,
        admission=admission,
    )

    with pytest.raises(RoleAssigneeUnavailableError) as raised:
        service.assign_role(
            role="reviewer",
            peer_node_id="cc",
            actor_id="operator-1",
        )

    assert raised.value.admission_state is admission
    assert broker.get_target("role-assignment:reviewer") is None
    assert _governance_event_count(store) == 0


def test_assignment_rejects_projection_stale_at_read_time(
    services: tuple[
        RoleAssignmentService,
        GovernanceBroker,
        HealthService,
        SqliteStateStore,
        FixedClock,
    ],
) -> None:
    service, broker, health, store, clock = services
    _seed_projection(
        store,
        health,
        availability=AvailabilityState.HEALTHY,
        admission=AdmissionState.OPEN,
        updated_at=(
            clock.value - health.policy.readiness_freshness_seconds - 1
        ),
    )

    with pytest.raises(RoleAssigneeUnavailableError) as raised:
        service.assign_role(
            role="reviewer",
            peer_node_id="cc",
            actor_id="operator-1",
        )

    assert raised.value.stale is True
    assert raised.value.status == "STALE"
    assert broker.get_target("role-assignment:reviewer") is None


def test_identical_reassignment_gets_fresh_identity_and_revision(
    services: tuple[
        RoleAssignmentService,
        GovernanceBroker,
        HealthService,
        SqliteStateStore,
        FixedClock,
    ],
) -> None:
    service, _, _, _, _ = services
    service.assign_role(
        role="implementer",
        peer_node_id="cc",
        actor_id="operator-1",
    )
    before = service.get_role("implementer")
    assert before is not None

    service.assign_role(
        role="implementer",
        peer_node_id="cc",
        actor_id="operator-1",
    )
    after = service.get_role("implementer")

    assert after is not None
    assert after.revision == before.revision + 1
    assert after.state["assignment_id"] != before.state["assignment_id"]


def test_release_unassigned_is_noop_without_broker_submission(
    services: tuple[
        RoleAssignmentService,
        GovernanceBroker,
        HealthService,
        SqliteStateStore,
        FixedClock,
    ],
) -> None:
    service, _, _, store, _ = services
    before = _governance_event_count(store)

    result = service.release_role(
        role="missing",
        actor_id="operator-1",
    )

    assert result.disposition is RoleReleaseDisposition.NOT_ASSIGNED
    assert result.submission is None
    assert result.target is None
    assert _governance_event_count(store) == before


def test_release_owner_mismatch_is_error_without_mutation(
    services: tuple[
        RoleAssignmentService,
        GovernanceBroker,
        HealthService,
        SqliteStateStore,
        FixedClock,
    ],
) -> None:
    service, _, _, store, _ = services
    service.assign_role(
        role="implementer",
        peer_node_id="cc",
        actor_id="operator-1",
    )
    before = service.get_role("implementer")
    before_events = _governance_event_count(store)

    with pytest.raises(InvalidMutationError, match="belongs to cc"):
        service.release_role(
            role="implementer",
            peer_node_id="ag",
            actor_id="operator-2",
        )

    assert service.get_role("implementer") == before
    assert _governance_event_count(store) == before_events


@pytest.mark.parametrize("owner_assertion", (None, "cc"))
def test_release_hides_role_but_preserves_released_target(
    services: tuple[
        RoleAssignmentService,
        GovernanceBroker,
        HealthService,
        SqliteStateStore,
        FixedClock,
    ],
    owner_assertion: str | None,
) -> None:
    service, broker, _, store, _ = services
    service.assign_role(
        role="implementer",
        peer_node_id="cc",
        actor_id="operator-1",
    )

    result = service.release_role(
        role="implementer",
        peer_node_id=owner_assertion,
        actor_id="operator-2",
    )

    assert result.disposition is RoleReleaseDisposition.RELEASED
    assert result.submission is not None
    assert result.target is not None
    assert result.target.state["status"] == "RELEASED"
    assert service.get_role("implementer") is None
    assert service.list_roles() == ()
    persisted = broker.get_target("role-assignment:implementer")
    assert persisted is not None
    assert persisted.state["status"] == "RELEASED"

    before_events = _governance_event_count(store)
    repeated = service.release_role(
        role="implementer",
        actor_id="operator-2",
    )
    assert repeated.disposition is RoleReleaseDisposition.NOT_ASSIGNED
    assert repeated.submission is None
    assert repeated.target == persisted
    assert _governance_event_count(store) == before_events

def test_assign_role_rejects_empty_strings(
    services: tuple[
        RoleAssignmentService,
        GovernanceBroker,
        HealthService,
        SqliteStateStore,
        FixedClock,
    ],
) -> None:
    service, _, _, _, _ = services
    
    with pytest.raises(ValueError):
        service.assign_role(role="", peer_node_id="cc", actor_id="operator-1")
        
    with pytest.raises(ValueError):
        service.assign_role(role="implementer", peer_node_id="", actor_id="operator-1")
        
    with pytest.raises(ValueError):
        service.assign_role(role="implementer", peer_node_id="cc", actor_id="")
