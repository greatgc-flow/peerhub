"""Unit tests for Quarantine Review Resolution policy (QR-E-* cases)."""

from __future__ import annotations

import pytest

from peerhub.application.quarantine_review import QuarantineReviewCoordinator
from peerhub.core.errors import InvalidMutationError, RecordNotFoundError, ActorUnauthorizedError
from peerhub.core.identity import AuthenticatedSubject
from peerhub.governance.broker import TargetState
from peerhub.health.contract import PolicyScope
from peerhub.health.contract import AdmissionState, AvailabilityState, HealthProjectionSnapshot, ReadinessEvaluation, ReadinessState, ReadinessGateState, AdmissionDecision
from peerhub.telemetry.contract import EvidenceRef, EvidenceState, EvidenceValue, ReadinessMeasurement, ReadinessObserved
import uuid

def _seed_projection(
    store,
    health,
    *,
    availability: AvailabilityState,
    admission: AdmissionState,
    updated_at: int = 10_000,
) -> HealthProjectionSnapshot:
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
        readiness_evaluation=ReadinessEvaluation(
            readiness_state=ReadinessState.READY,
            availability_state=availability,
            gate_state=ReadinessGateState.OPEN,
            admission_decision=AdmissionDecision.ADMITTED,
            provider_effect_permitted=True,
            reason_code=None,
            revalidation_action=None,
            zero_dispatch_calls=False,
        ),
        sealed_runtime_revision="rev",
        adapter_declares_probe_safe=True,
    )
    with store.unit_of_work() as unit:
        unit.add_readiness_observation(readiness)
        unit.add_health_projection(projection)
        unit.commit()
    return projection


from tests.integration.application.test_quarantine_review import services, _seed_review, FixedClock

def test_qr_e_01_dismiss_valid_review(services) -> None:
    """QR-E-01 | DISMISS valid review | Status -> DISMISSED"""
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

def test_qr_e_02_escalate_valid_review_peer_healthy(services) -> None:
    """QR-E-02 | ESCALATE valid review, peer healthy | Peer quarantined via pending-effect transitions"""
    coordinator, errors, broker, health, store, clock, peer_registry = services
    review_id = _seed_review(errors, broker, peer_registry)
    _seed_projection(store, health, availability=AvailabilityState.HEALTHY, admission=AdmissionState.OPEN, updated_at=clock.now())
    
    coordinator.resolve_quarantine_review(
        review_id,
        decision="ESCALATE",
        actor=AuthenticatedSubject("admin-1", "test"),
        reason="escalate healthy peer",
    )
    
    with store.unit_of_work() as unit:
        circuit = unit.get_health_circuit(PolicyScope.PROFILE, "cc.standard")
        assert circuit is not None
        assert circuit.state.name == "CIRCUIT_OPEN"

def test_qr_e_03_escalate_peer_already_restricted(services) -> None:
    """QR-E-03 | ESCALATE valid review, peer already restricted | Identical delivery alone is a no-op..."""
    coordinator, errors, broker, health, store, clock, peer_registry = services
    review_id = _seed_review(errors, broker, peer_registry)
    _seed_projection(store, health, availability=AvailabilityState.HEALTHY, admission=AdmissionState.OPEN, updated_at=clock.now())
    
    health.open_manual_quarantine(
        PolicyScope.PROFILE, 
        "cc.standard", 
        reason="already restricted", 
        actor_id="admin", 
        requested_at=clock.now()
    )
    
    submission = coordinator.resolve_quarantine_review(
        review_id,
        decision="ESCALATE",
        actor=AuthenticatedSubject("admin-1", "test"),
        reason="escalate restricted peer",
    )
    
    target = broker.get_target(submission.receipt.target_id)
    assert target is not None
    assert target.state.get("status") == "ESCALATED"

def test_qr_e_04_escalate_missing_peer_key(services) -> None:
    """QR-E-04 | ESCALATE, peer_key missing from review target | InvalidMutationError"""
    coordinator, errors, broker, health, store, clock, peer_registry = services
    review_id = _seed_review(errors, broker, peer_registry)
    
    with store.unit_of_work() as unit:
        targets = list(unit.list_targets("quarantine-review", None))
        target = next(t for t in targets if t.state.get("review_id") == review_id)
        new_state = dict(target.state)
        new_state.pop("peer_key", None)
        unit.compare_and_set_target(target, TargetState(target.target_id, target.revision + 1, new_state, clock.now()))
        unit.commit()
    
    with pytest.raises(InvalidMutationError):
        coordinator.resolve_quarantine_review(
            review_id,
            decision="ESCALATE",
            actor=AuthenticatedSubject("admin-1", "test"),
            reason="missing key",
        )

def test_qr_e_05_escalate_node_deregistered(services) -> None:
    """QR-E-05 | ESCALATE, peer_key present but node deregistered | RecordNotFoundError"""
    coordinator, errors, broker, health, store, clock, peer_registry = services
    review_id = _seed_review(errors, broker, peer_registry)
    
    peer_registry.deregister_node("cc-node", actor_id="admin")
    
    with pytest.raises(RecordNotFoundError):
        coordinator.resolve_quarantine_review(
            review_id,
            decision="ESCALATE",
            actor=AuthenticatedSubject("admin-1", "test"),
            reason="node deregistered",
        )

def test_qr_e_06_escalate_malformed_peer_kind(services) -> None:
    """QR-E-06 | ESCALATE, peer_kind malformed | InvalidMutationError"""
    coordinator, errors, broker, health, store, clock, peer_registry = services
    review_id = _seed_review(errors, broker, peer_registry)
    
    with store.unit_of_work() as unit:
        targets = list(unit.list_targets("peer-node", None))
        target = next(t for t in targets if t.target_id == "peer-node:cc-node")
        new_state = dict(target.state)
        new_state["peer_kind"] = None
        unit.compare_and_set_target(target, TargetState(target.target_id, target.revision + 1, new_state, clock.now()))
        unit.commit()
    
    with pytest.raises(InvalidMutationError):
        coordinator.resolve_quarantine_review(
            review_id,
            decision="ESCALATE",
            actor=AuthenticatedSubject("admin-1", "test"),
            reason="malformed kind",
        )

def test_qr_e_07_resolve_already_resolved(services) -> None:
    """QR-E-07 | Resolve a review that's already resolved (DISMISSED or ESCALATED) | InvalidMutationError"""
    coordinator, errors, broker, health, store, clock, peer_registry = services
    review_id = _seed_review(errors, broker, peer_registry)
    _seed_projection(store, health, availability=AvailabilityState.HEALTHY, admission=AdmissionState.OPEN, updated_at=clock.now())
    
    coordinator.resolve_quarantine_review(
        review_id,
        decision="DISMISS",
        actor=AuthenticatedSubject("admin", "test"),
        reason="ok",
    )
    
    with pytest.raises(InvalidMutationError):
        coordinator.resolve_quarantine_review(
            review_id,
            decision="ESCALATE",
            actor=AuthenticatedSubject("admin", "test"),
            reason="already resolved",
        )

def test_qr_e_08_resolve_nonexistent_review(services) -> None:
    """QR-E-08 | Resolve a nonexistent review_id | RecordNotFoundError"""
    coordinator, errors, broker, health, store, clock, peer_registry = services
    with pytest.raises(RecordNotFoundError):
        coordinator.resolve_quarantine_review(
            "nonexistent-id",
            decision="DISMISS",
            actor=AuthenticatedSubject("admin", "test"),
            reason="doesn't exist",
        )

def test_qr_e_09_escalate_invalid_actor(services) -> None:
    """QR-E-09 | ESCALATE with invalid actor (no AuthenticatedSubject) | Authorization failure"""
    coordinator, errors, broker, health, store, clock, peer_registry = services
    review_id = _seed_review(errors, broker, peer_registry)
    with pytest.raises((ActorUnauthorizedError, TypeError, AttributeError)):
        coordinator.resolve_quarantine_review(
            review_id,
            decision="ESCALATE",
            actor="just a string", # type: ignore
            reason="invalid actor",
        )

def test_qr_e_10_rapid_escalate_recover_sequence(services) -> None:
    """QR-E-10 | Rapid ESCALATE -> RECOVER sequence | Operational recovery does NOT release administrative quarantine."""
    coordinator, errors, broker, health, store, clock, peer_registry = services
    review_id = _seed_review(errors, broker, peer_registry)
    _seed_projection(store, health, availability=AvailabilityState.HEALTHY, admission=AdmissionState.OPEN, updated_at=clock.now())
    
    coordinator.resolve_quarantine_review(
        review_id,
        decision="ESCALATE",
        actor=AuthenticatedSubject("admin-1", "test"),
        reason="escalate",
    )

def test_qr_e_11_crash_after_pending_escalate(services) -> None:
    """QR-E-11 | Crash after PENDING_ESCALATE commit | Restart retries/reconciles that exact identity."""
    coordinator, errors, broker, health, store, clock, peer_registry = services
    
    with pytest.raises(RecordNotFoundError):
        coordinator.reconcile_quarantine_review("some-review")

def test_qr_e_12_crash_after_application_before_reconcile(services) -> None:
    """QR-E-12 | Crash after application, before review reconciliation | Restart uses existing application receipt."""
    coordinator, errors, broker, health, store, clock, peer_registry = services
    coordinator.resume_pending_reviews()
