from __future__ import annotations

import hashlib
from collections.abc import Iterator
from pathlib import Path

import pytest

from peerhub.application.peer_registry import PeerRegistryService
from peerhub.application.quarantine_review import QuarantineReviewCoordinator
from peerhub.core.context import Clock
from peerhub.core.errors import RecordNotFoundError, ActorUnauthorizedError
from peerhub.core.identity import AuthenticatedSubject
from peerhub.governance.broker import GovernanceBroker
from peerhub.governance.operational_errors import OperationalErrorService
from peerhub.health.contract import (
    AdmissionState,
    AvailabilityState,
    HealthPolicy,
    HealthScopeMembershipSnapshot,
    CircuitState,
    HealthCircuitSnapshot,
    PolicyScope,
    QuarantineAuthorityClass,
)
from peerhub.health.service import HealthService
from peerhub.persistence.sqlite import SqliteStateStore
from peerhub.telemetry.projections import TelemetryProjector
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
        QuarantineReviewCoordinator,
        OperationalErrorService,
        GovernanceBroker,
        HealthService,
        SqliteStateStore,
        FixedClock,
        PeerRegistryService,
    ]
]:
    store = SqliteStateStore(
        tmp_path / "quarantine-review.sqlite3",
        workspace_home_id="quarantine-review-test",
    )
    store.initialize()
    clock = FixedClock()
    ids = SequentialIdSource()
    broker = GovernanceBroker(store, clock=clock, ids=ids)
    peer_registry = PeerRegistryService(broker, clock=clock, ids=ids)
    policy = HealthPolicy(
        policy_id="quarantine-health-v1",
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
    coordinator = QuarantineReviewCoordinator(
        broker,
        peer_registry=peer_registry,
        health=health,
        clock=clock,
        ids=ids,
    )
    operational_errors = OperationalErrorService(
        broker,
        clock=clock,
        ids=ids,
    )
    try:
        yield coordinator, operational_errors, broker, health, store, clock, peer_registry
    finally:
        store.close()


def _hash(pattern: str = "sandbox violation") -> str:
    return hashlib.sha256(pattern.encode("utf-8")).hexdigest()


def _seed_review(
    errors: OperationalErrorService,
    broker: GovernanceBroker,
    peer_registry: PeerRegistryService,
) -> str:
    # First register the peer node so that peer_registry can resolve it
    peer_registry.register_node(
        node_id="cc-node",
        peer_kind="cc",
        profile_id="cc.standard",
        actor_id="admin",
    )

    errors.report_error(
        peer_key="cc-node",
        pattern="sandbox violation",
        severity="warn",
        detail="detail",
        actor_id="admin",
        threshold=1,
    )
    
    # Extract review_id
    reviews = []
    for target in broker.list_targets("quarantine-review", None):
        if target.state.get("status") == "REQUESTED":
            reviews.append(target.state.get("review_id"))
            
    return str(reviews[0])


def test_list_pending_quarantine_reviews(
    services: tuple[
        QuarantineReviewCoordinator,
        OperationalErrorService,
        GovernanceBroker,
        HealthService,
        SqliteStateStore,
        FixedClock,
        PeerRegistryService,
    ]
) -> None:
    coordinator, errors, broker, health, store, clock, peer_registry = services
    
    review_id = _seed_review(errors, broker, peer_registry)
    
    reviews = coordinator.list_pending_quarantine_reviews()
    assert len(reviews) == 1
    assert reviews[0].state.get("review_id") == review_id
    
    # Dismiss it
    coordinator.resolve_quarantine_review(
        review_id,
        decision="DISMISS",
        actor=AuthenticatedSubject("admin", "test"),
        reason="looks fine",
    )
    
    # It should no longer be pending
    assert len(coordinator.list_pending_quarantine_reviews()) == 0


def test_resolve_quarantine_review_dismiss(
    services: tuple[
        QuarantineReviewCoordinator,
        OperationalErrorService,
        GovernanceBroker,
        HealthService,
        SqliteStateStore,
        FixedClock,
        PeerRegistryService,
    ]
) -> None:
    coordinator, errors, broker, health, store, clock, peer_registry = services
    
    review_id = _seed_review(errors, broker, peer_registry)
    
    submission = coordinator.resolve_quarantine_review(
        review_id,
        decision="DISMISS",
        actor=AuthenticatedSubject("admin-1", "test"),
        reason="looks fine",
    )
    
    target = broker.get_target(submission.receipt.target_id)
    assert target is not None
    assert target.state.get("status") == "DISMISSED"
    assert target.state.get("resolved_by") == "admin-1"
    assert target.state.get("reason") == "looks fine"
    assert target.state.get("resolved_at") == clock.now()
    
    # Health circuit state was untouched
    with store.unit_of_work() as unit:
        circuit = unit.get_health_circuit(PolicyScope.PROFILE, "cc.standard")
        assert circuit is None


def test_resolve_quarantine_review_escalate(
    services: tuple[
        QuarantineReviewCoordinator,
        OperationalErrorService,
        GovernanceBroker,
        HealthService,
        SqliteStateStore,
        FixedClock,
        PeerRegistryService,
    ]
) -> None:
    coordinator, errors, broker, health, store, clock, peer_registry = services
    
    review_id = _seed_review(errors, broker, peer_registry)
    
    # In order to authorize administrative recovery, the circuit must be OPEN
    # and admission state must be QUARANTINED, and the circuit must have non-automatic QuarantineAuthorityClass
    # (as checked by health_service's authorize_administrative_recovery).
    
    # First, let's inject a projection and an open circuit so that it passes health_service check.
    with store.unit_of_work() as unit:
        from peerhub.health.contract import HealthProjectionSnapshot, ReadinessEvaluation
        from tests.integration.application.test_role_assignment import _seed_projection
        
        # We need a projection
        import uuid
        from peerhub.telemetry.contract import ReadinessObserved, EvidenceValue, EvidenceState, ReadinessMeasurement
        from peerhub.health.contract import ReadinessState, ReadinessGateState, AdmissionDecision, RevalidationAction, PolicyReceipt
        obs_id = "obs-" + uuid.uuid4().hex[:8]
        readiness = ReadinessObserved(
            observation_id=obs_id,
            instance_id="cc",
            profile_id="cc.standard",
            evidence=EvidenceValue(
                state=EvidenceState.MEASURED,
                source_tag="test",
                provider_id="test",
                provider_version="1",
                observed_at=clock.now(),
                captured_at=clock.now(),
                freshness_ttl=3600,
                evidence_ref="ref",  # type: ignore
                value=ReadinessMeasurement(
                    runtime_revision="1",
                    issued_at=clock.now(),
                    valid_until=clock.now() + 3600,
                    integrity_verified=True,
                ),
            ),
        )
        unit.add_readiness_observation(readiness)
        
        projection = HealthProjectionSnapshot(
            projection_id="proj-1",
            instance_id="cc",
            profile_id="cc.standard",
            availability_state=AvailabilityState.HEALTHY,
            admission_state=AdmissionState.QUARANTINED,
            readiness_observation_id=obs_id,
            operational_projection_id=None,
            operational_projection_revision=None,
            policy_id=health.policy.policy_id,
            policy_revision=health.policy.revision,
            cooldown_until=None,
            evidence_refs=(),
            revision=1,
            created_at=clock.now(),
            updated_at=clock.now(),
            readiness_evaluation=ReadinessEvaluation(
                readiness_state=ReadinessState.READY,
                availability_state=AvailabilityState.HEALTHY,
                gate_state=ReadinessGateState.OPEN,
                admission_decision=AdmissionDecision.ADMITTED,
                provider_effect_permitted=True,
                reason_code=None,
                revalidation_action=None,
                zero_dispatch_calls=False,
            ),
            sealed_runtime_revision="1",
            adapter_declares_probe_safe=True,
        )
        unit.add_health_projection(projection)
        
        circuit = HealthCircuitSnapshot(
            circuit_id="circuit-1",
            scope=PolicyScope.PROFILE,
            subject="cc.standard",
            state=CircuitState.CIRCUIT_OPEN,
            receipt=PolicyReceipt(
                incident="inc-1",
                gate_generation=1,
                timestamp=clock.now(),
                fingerprint="abc",
            ),
            quarantine_authority_class=QuarantineAuthorityClass.MANUAL,
            backoff_count=0,
            cooldown_until=None,
            revision=1,
            created_at=clock.now(),
            updated_at=clock.now(),
        )
        unit.add_health_circuit(circuit)
        unit.commit()
    
    submission = coordinator.resolve_quarantine_review(
        review_id,
        decision="ESCALATE",
        actor=AuthenticatedSubject("admin-1", "test"),
        reason="real threat",
    )
    
    target = broker.get_target(submission.receipt.target_id)
    assert target is not None
    assert target.state.get("status") == "ESCALATED"
    
    # Check that a recovery grant was created by health service
    with store.unit_of_work() as unit:
        grant = unit.get_live_recovery_probe_grant("circuit-1")
        assert grant is not None
        assert grant.authorized_by == "admin-1"
        assert grant.authorization_mode.value == "ADMINISTRATIVE"

def test_resolve_rejects_invalid_decision(
    services: tuple[
        QuarantineReviewCoordinator,
        OperationalErrorService,
        GovernanceBroker,
        HealthService,
        SqliteStateStore,
        FixedClock,
        PeerRegistryService,
    ]
) -> None:
    coordinator, errors, broker, health, store, clock, peer_registry = services
    review_id = _seed_review(errors, broker, peer_registry)
    
    with pytest.raises(ValueError, match="decision must be DISMISS or ESCALATE"):
        coordinator.resolve_quarantine_review(
            review_id,
            decision="IGNORE",
            actor=AuthenticatedSubject("admin", "test"),
            reason="ignored",
        )

def test_resolve_already_resolved(
    services: tuple[
        QuarantineReviewCoordinator,
        OperationalErrorService,
        GovernanceBroker,
        HealthService,
        SqliteStateStore,
        FixedClock,
        PeerRegistryService,
    ]
) -> None:
    coordinator, errors, broker, health, store, clock, peer_registry = services
    review_id = _seed_review(errors, broker, peer_registry)
    
    # Dismiss once
    coordinator.resolve_quarantine_review(
        review_id,
        decision="DISMISS",
        actor=AuthenticatedSubject("admin", "test"),
        reason="ok",
    )
    
    clock.value += 100
    
    # Dismiss again gracefully updates resolved_at
    submission = coordinator.resolve_quarantine_review(
        review_id,
        decision="DISMISS",
        actor=AuthenticatedSubject("admin", "test"),
        reason="still ok",
    )
    
    target = broker.get_target(submission.receipt.target_id)
    assert target is not None
    assert target.state.get("resolved_at") == clock.now()
    assert target.state.get("reason") == "still ok"
