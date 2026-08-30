from __future__ import annotations

from collections.abc import Iterator, Mapping
from pathlib import Path

import pytest

from peerhub.application.leadership import (
    LeadershipClaimDisposition,
    LeadershipIncumbentProtectedError,
    LeadershipMonopolyError,
    LeadershipPolicy,
    LeadershipService,
)
from peerhub.application.peer_registry import PeerRegistryService
from peerhub.core.context import Clock
from peerhub.core.errors import RecordNotFoundError
from peerhub.governance.broker import GovernanceBroker
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
from tests.fakes import SequentialIdSource

_START = 10_000


class MovableClock(Clock):
    def __init__(self, value: int = _START) -> None:
        self.value = value

    def now(self) -> int:
        return self.value

    def advance(self, seconds: int) -> None:
        self.value += seconds


@pytest.fixture
def services(
    tmp_path: Path,
) -> Iterator[
    tuple[LeadershipService, GovernanceBroker, SqliteStateStore, MovableClock]
]:
    store = SqliteStateStore(
        tmp_path / "leadership.sqlite3",
        workspace_home_id="leadership-test",
    )
    store.initialize()
    clock = MovableClock()
    ids = SequentialIdSource()
    broker = GovernanceBroker(store, clock=clock, ids=ids)
    peer_registry = PeerRegistryService(broker, clock=clock, ids=ids)
    policy = HealthPolicy(
        policy_id="leadership-health-v1",
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
    telemetry = TelemetryProjector(store, ids=ids, freshness_ttl=7200)
    health = HealthService(
        store,
        telemetry=telemetry,
        policy=policy,
        membership=HealthScopeMembershipSnapshot(
            configuration_revision=1,
            configuration_digest="b" * 64,
            configured_members=(
                ("cc", "cc.standard"),
                ("cx", "cx.standard"),
            ),
            bindings=(),
        ),
        clock=clock,
        ids=ids,
    )
    service = LeadershipService(
        broker,
        peer_registry=peer_registry,
        health=health,
        clock=clock,
        ids=ids,
    )
    try:
        yield service, broker, store, clock
    finally:
        store.close()


def _seed_projection(
    store: SqliteStateStore,
    *,
    instance_id: str = "cc",
    profile_id: str = "cc.standard",
    availability: AvailabilityState = AvailabilityState.HEALTHY,
    admission: AdmissionState = AdmissionState.OPEN,
    updated_at: int = _START,
) -> None:
    projection = HealthProjectionSnapshot(
        projection_id=f"health-projection-{instance_id}",
        instance_id=instance_id,
        profile_id=profile_id,
        availability_state=availability,
        admission_state=admission,
        readiness_observation_id=None,
        operational_projection_id=None,
        operational_projection_revision=None,
        policy_id="leadership-health-v1",
        policy_revision=1,
        cooldown_until=None,
        evidence_refs=(),
        revision=1,
        created_at=updated_at,
        updated_at=updated_at,
    )
    with store.unit_of_work() as unit:
        unit.add_health_projection(projection)
        unit.commit()


def _leader_id(target: object) -> str | None:
    state = getattr(target, "state", None)
    assert isinstance(state, Mapping)
    leader = state.get("leader")
    if not isinstance(leader, Mapping):
        return None
    value = leader.get("peer_node_id")
    return value if isinstance(value, str) else None


# --- claim dispositions ------------------------------------------------


def test_vacant_claim_on_an_empty_workspace(services) -> None:
    service, broker, _, clock = services

    result = service.claim_leadership(
        peer_node_id="cc",
        actor_id="operator-1",
        reason="planning_round",
        domain="design",
    )

    assert result.disposition is LeadershipClaimDisposition.VACANT_CLAIM
    target = broker.get_target("leadership:workspace")
    assert target is not None
    assert target.state["status"] == "PENDING"
    assert target.state["term"] == 1
    assert target.state["domain"] == "design"
    assert target.state["reason"] == "planning_round"
    assert target.state["challenge_until"] == clock.value + 60
    assert target.state["claimed_by"] == "operator-1"
    assert _leader_id(target) == "cc"
    assert target.state["leader"]["peer_kind"] == "cc"
    assert target.state["leader"]["profile_id"] == "cc.standard"
    assert target.state["claim_basis"]["disposition"] == "VACANT_CLAIM"
    # No health was consulted on this path.
    assert target.state["claim_basis"]["availability_state"] is None
    assert target.state["claim_basis"]["stale_at_read"] is None
    assert len(target.state["coordinator_history"]) == 1


def test_claim_applies_legacy_domain_and_reason_fallbacks(services) -> None:
    service, broker, _, _ = services

    service.claim_leadership(peer_node_id="cc", actor_id="operator-1")
    target = broker.get_target("leadership:workspace")
    assert target is not None
    assert target.state["reason"] == "manual_claim"
    assert target.state["domain"] == "general"

    # domain falls back to reason before "general".
    service.claim_leadership(
        peer_node_id="cc", actor_id="operator-1", reason="failover"
    )
    target = broker.get_target("leadership:workspace")
    assert target is not None
    assert target.state["domain"] == "failover"


def test_self_reclaim_needs_no_health_projection(services) -> None:
    service, broker, _, clock = services
    service.claim_leadership(peer_node_id="cc", actor_id="operator-1")
    # Past the challenge window, so only the self-reclaim branch can allow it.
    clock.advance(600)

    result = service.claim_leadership(
        peer_node_id="cc", actor_id="operator-1"
    )

    assert result.disposition is LeadershipClaimDisposition.SELF_RECLAIM
    target = broker.get_target("leadership:workspace")
    assert target is not None
    assert target.state["term"] == 2
    assert target.state["challenge_until"] == clock.value + 60


def test_open_challenge_window_beats_a_healthy_incumbent(services) -> None:
    service, broker, store, clock = services
    _seed_projection(store, availability=AvailabilityState.HEALTHY)
    service.claim_leadership(peer_node_id="cc", actor_id="operator-1")

    # Still inside cc's challenge window.
    clock.advance(30)
    result = service.claim_leadership(
        peer_node_id="cx", actor_id="operator-2"
    )

    assert (
        result.disposition
        is LeadershipClaimDisposition.OPEN_WINDOW_CHALLENGE
    )
    target = broker.get_target("leadership:workspace")
    assert target is not None
    assert _leader_id(target) == "cx"
    assert (
        target.state["claim_basis"]["incumbent_peer_node_id"] == "cc"
    )


def test_healthy_incumbent_is_protected_after_the_window(services) -> None:
    service, broker, store, clock = services
    _seed_projection(store, availability=AvailabilityState.HEALTHY)
    service.claim_leadership(peer_node_id="cc", actor_id="operator-1")
    clock.advance(600)

    with pytest.raises(LeadershipIncumbentProtectedError):
        service.claim_leadership(peer_node_id="cx", actor_id="operator-2")

    target = broker.get_target("leadership:workspace")
    assert target is not None
    assert _leader_id(target) == "cc"
    assert target.state["term"] == 1


def test_absent_health_evidence_protects_the_incumbent(services) -> None:
    service, broker, _, clock = services
    service.claim_leadership(peer_node_id="cc", actor_id="operator-1")
    clock.advance(600)

    # No projection was ever seeded: legacy's UNKNOWN is not RED/STALE, so
    # the incumbent survives.
    with pytest.raises(LeadershipIncumbentProtectedError):
        service.claim_leadership(peer_node_id="cx", actor_id="operator-2")

    target = broker.get_target("leadership:workspace")
    assert target is not None
    assert _leader_id(target) == "cc"


@pytest.mark.parametrize(
    ("availability", "admission"),
    [
        (AvailabilityState.UNAVAILABLE, AdmissionState.OPEN),
        (AvailabilityState.STALE, AdmissionState.OPEN),
        (AvailabilityState.HEALTHY, AdmissionState.QUARANTINED),
        (AvailabilityState.HEALTHY, AdmissionState.COOLDOWN),
        (AvailabilityState.HEALTHY, AdmissionState.RECOVERY_REQUIRED),
    ],
)
def test_failed_incumbent_can_be_taken_over(
    services,
    availability: AvailabilityState,
    admission: AdmissionState,
) -> None:
    service, broker, store, clock = services
    _seed_projection(store, availability=availability, admission=admission)
    service.claim_leadership(peer_node_id="cc", actor_id="operator-1")
    clock.advance(600)

    result = service.claim_leadership(
        peer_node_id="cx", actor_id="operator-2", reason="failover"
    )

    assert (
        result.disposition
        is LeadershipClaimDisposition.FAILED_INCUMBENT_TAKEOVER
    )
    target = broker.get_target("leadership:workspace")
    assert target is not None
    assert _leader_id(target) == "cx"
    basis = target.state["claim_basis"]
    assert basis["incumbent_peer_node_id"] == "cc"
    assert basis["projection_id"] == "health-projection-cc"
    assert basis["availability_state"] == availability.value
    assert basis["admission_state"] == admission.value
    assert basis["stale_at_read"] is False


def test_stale_projection_allows_takeover(services) -> None:
    service, broker, store, clock = services
    _seed_projection(store, availability=AvailabilityState.HEALTHY)
    service.claim_leadership(peer_node_id="cc", actor_id="operator-1")
    # Past readiness_freshness_seconds (7200) as well as the window.
    clock.advance(8000)

    result = service.claim_leadership(
        peer_node_id="cx", actor_id="operator-2"
    )

    assert (
        result.disposition
        is LeadershipClaimDisposition.FAILED_INCUMBENT_TAKEOVER
    )
    target = broker.get_target("leadership:workspace")
    assert target is not None
    assert target.state["claim_basis"]["stale_at_read"] is True


def test_claim_requires_a_resolvable_peer_node(services) -> None:
    service, broker, _, _ = services

    with pytest.raises(RecordNotFoundError):
        service.claim_leadership(
            peer_node_id="not-registered", actor_id="operator-1"
        )

    assert broker.get_target("leadership:workspace") is None


# --- AP-20 -------------------------------------------------------------


def test_ap20_allows_three_consecutive_terms_and_rejects_the_fourth(
    services,
) -> None:
    service, broker, _, clock = services

    for expected_term in (1, 2, 3):
        result = service.claim_leadership(
            peer_node_id="cc", actor_id="operator-1"
        )
        assert result.target.state["term"] == expected_term
        clock.advance(5)

    with pytest.raises(LeadershipMonopolyError) as caught:
        service.claim_leadership(peer_node_id="cc", actor_id="operator-1")
    assert caught.value.threshold == 3

    target = broker.get_target("leadership:workspace")
    assert target is not None
    assert target.state["term"] == 3


def test_ap20_applies_even_when_another_peer_broke_the_streak(
    services,
) -> None:
    service, _, _, clock = services

    service.claim_leadership(peer_node_id="cc", actor_id="operator-1")
    clock.advance(5)
    service.claim_leadership(peer_node_id="cx", actor_id="operator-2")
    clock.advance(5)
    for _ in range(2):
        service.claim_leadership(peer_node_id="cc", actor_id="operator-1")
        clock.advance(5)

    # History tail is [cx, cc, cc] -- not a monopoly yet.
    service.claim_leadership(peer_node_id="cc", actor_id="operator-1")
    clock.advance(5)
    with pytest.raises(LeadershipMonopolyError):
        service.claim_leadership(peer_node_id="cc", actor_id="operator-1")


def test_history_is_bounded_to_the_policy_limit(tmp_path: Path) -> None:
    store = SqliteStateStore(
        tmp_path / "leadership-history.sqlite3",
        workspace_home_id="leadership-history-test",
    )
    store.initialize()
    clock = MovableClock()
    ids = SequentialIdSource()
    broker = GovernanceBroker(store, clock=clock, ids=ids)
    policy = HealthPolicy(
        policy_id="leadership-health-v1",
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
    service = LeadershipService(
        broker,
        peer_registry=PeerRegistryService(broker, clock=clock, ids=ids),
        health=HealthService(
            store,
            telemetry=TelemetryProjector(store, ids=ids, freshness_ttl=7200),
            policy=policy,
            membership=HealthScopeMembershipSnapshot(
                configuration_revision=1,
                configuration_digest="c" * 64,
                configured_members=(),
                bindings=(),
            ),
            clock=clock,
            ids=ids,
        ),
        clock=clock,
        ids=ids,
        # monopoly_threshold=1 would reject every re-claim; keep AP-20 wide
        # so the history bound is what this test actually exercises.
        policy=LeadershipPolicy(monopoly_threshold=99, history_limit=3),
    )
    try:
        for _ in range(5):
            service.claim_leadership(peer_node_id="cc", actor_id="operator-1")
            clock.advance(5)
        target = broker.get_target("leadership:workspace")
        assert target is not None
        history = target.state["coordinator_history"]
        assert len(history) == 3
        assert [entry["term"] for entry in history] == [3, 4, 5]
    finally:
        store.close()


def test_policy_validates_and_clamps() -> None:
    assert LeadershipPolicy().monopoly_threshold == 3
    with pytest.raises(ValueError):
        LeadershipPolicy(challenge_window_seconds=0)
    with pytest.raises(ValueError):
        LeadershipPolicy(history_limit=-1)


# --- yield -------------------------------------------------------------


def test_yield_retains_term_and_history_and_nulls_the_claim(
    services,
) -> None:
    service, broker, _, clock = services
    service.claim_leadership(
        peer_node_id="cc", actor_id="operator-1", reason="planning"
    )
    clock.advance(5)

    result = service.yield_leadership(
        yielding_peer_id="cc", actor_id="operator-1", reason="handing off"
    )

    assert result.owner_mismatch is False
    assert result.previous_leader_peer_node_id == "cc"
    target = broker.get_target("leadership:workspace")
    assert target is not None
    assert target.state["status"] == "VACANT"
    for nulled in (
        "leader",
        "claim_id",
        "challenge_until",
        "claimed_at",
        "claimed_by",
        "domain",
        "reason",
        "claim_basis",
    ):
        assert target.state[nulled] is None, nulled
    assert target.state["yielded_by"] == "cc"
    assert target.state["yielded_at"] == clock.value
    assert target.state["yield_reason"] == "handing off"
    # Retained: legacy siblings that a yield never touches.
    assert target.state["term"] == 1
    assert len(target.state["coordinator_history"]) == 1
    assert service.get_current_leader() is None


def test_yield_by_a_non_leader_warns_but_still_vacates(services) -> None:
    service, broker, _, _ = services
    service.claim_leadership(peer_node_id="cc", actor_id="operator-1")

    result = service.yield_leadership(
        yielding_peer_id="cx", actor_id="operator-2"
    )

    assert result.owner_mismatch is True
    assert result.previous_leader_peer_node_id == "cc"
    target = broker.get_target("leadership:workspace")
    assert target is not None
    assert target.state["status"] == "VACANT"
    assert target.state["yielded_by"] == "cx"
    assert target.state["yield_reason"] == "none"


def test_yield_on_an_absent_target_still_mutates(services) -> None:
    service, broker, _, _ = services
    assert broker.get_target("leadership:workspace") is None

    result = service.yield_leadership(
        yielding_peer_id="cc", actor_id="operator-1"
    )

    assert result.owner_mismatch is False
    assert result.previous_leader_peer_node_id is None
    target = broker.get_target("leadership:workspace")
    assert target is not None
    assert target.revision == 1
    assert target.state["status"] == "VACANT"
    assert target.state["term"] == 0
    assert target.state["coordinator_history"] == ()


def test_yield_on_an_already_vacant_target_still_mutates(services) -> None:
    service, broker, _, _ = services
    service.yield_leadership(yielding_peer_id="cc", actor_id="operator-1")
    first = broker.get_target("leadership:workspace")
    assert first is not None

    service.yield_leadership(yielding_peer_id="cx", actor_id="operator-2")

    second = broker.get_target("leadership:workspace")
    assert second is not None
    assert second.revision == first.revision + 1
    assert second.state["yielded_by"] == "cx"


def test_claim_after_a_yield_is_a_vacant_claim_and_keeps_the_term(
    services,
) -> None:
    service, broker, _, clock = services
    service.claim_leadership(peer_node_id="cc", actor_id="operator-1")
    service.yield_leadership(yielding_peer_id="cc", actor_id="operator-1")
    clock.advance(5)

    result = service.claim_leadership(
        peer_node_id="cx", actor_id="operator-2"
    )

    assert result.disposition is LeadershipClaimDisposition.VACANT_CLAIM
    target = broker.get_target("leadership:workspace")
    assert target is not None
    assert target.state["term"] == 2
    assert target.state["yielded_by"] is None
    assert target.state["yield_reason"] is None


# --- reads -------------------------------------------------------------


def test_get_current_leader_hides_vacant_records(services) -> None:
    service, _, _, _ = services
    assert service.get_leadership() is None
    assert service.get_current_leader() is None

    service.claim_leadership(peer_node_id="cc", actor_id="operator-1")
    assert service.get_current_leader() is not None

    service.yield_leadership(yielding_peer_id="cc", actor_id="operator-1")
    assert service.get_current_leader() is None
    # The record itself is still readable.
    assert service.get_leadership() is not None

def test_claim_and_yield_reject_empty_strings(services) -> None:
    service, _, _, _ = services
    with pytest.raises(ValueError):
        service.claim_leadership(peer_node_id="", actor_id="operator-1")
    with pytest.raises(ValueError):
        service.claim_leadership(peer_node_id="cc", actor_id="")
    with pytest.raises(ValueError):
        service.yield_leadership(yielding_peer_id="", actor_id="operator-1")
    with pytest.raises(ValueError):
        service.yield_leadership(yielding_peer_id="cc", actor_id="")

def test_ap20_does_not_reject_interspersed_claims(services) -> None:
    service, _, _, clock = services
    
    # 2 terms for cc
    service.claim_leadership(peer_node_id="cc", actor_id="operator-1")
    clock.advance(5)
    service.claim_leadership(peer_node_id="cc", actor_id="operator-1")
    clock.advance(5)
    
    # 1 term for cx (breaks streak)
    service.claim_leadership(peer_node_id="cx", actor_id="operator-2")
    clock.advance(5)
    
    # 2 terms for cc
    service.claim_leadership(peer_node_id="cc", actor_id="operator-1")
    clock.advance(5)
    service.claim_leadership(peer_node_id="cc", actor_id="operator-1")
    clock.advance(5)
    
    # this makes it 5 claims total, but no run of 3 for cc. It should succeed.
    result = service.claim_leadership(peer_node_id="cc", actor_id="operator-1")
    assert getattr(result, "disposition", None) is not None
