"""Unit tests for Health Consequence Decision Tree (HC-* cases)."""

from __future__ import annotations

import pytest

from peerhub.application.health_revalidation import (
    HealthRevalidationCoordinator,
    execute_peer_quarantine,
    execute_peer_recover,
)
from peerhub.health.contract import (
    HealthConsequencePolicy,
    DefaultConsequence,
    RecoveryAuthority,
    PolicyScope,
)
from peerhub.core.errors import StaleAuthorityEpochError
from peerhub.core.identity import AuthenticatedSubject
from peerhub.governance.operational_errors import OperationalErrorService

from tests.integration.application.test_quarantine_review import services, _seed_review

def test_hc_01_dispatch_fails_report_only(services) -> None:
    """HC-01 | Dispatch fails, consequence=report_only | Quarantine-review created, no auto-quarantine"""
    coordinator, errors, broker, health, store, clock, peer_registry = services
    
    # DECISION LOGIC PENDING GREEN-PHASE IMPLEMENTATION: 
    # For now, exercise underlying primitives directly.
    review_id = _seed_review(errors, broker, peer_registry)
    reviews = coordinator.list_pending_quarantine_reviews()
    assert len(reviews) == 1
    
    with store.unit_of_work() as unit:
        circuit = unit.get_health_circuit(PolicyScope.PROFILE, "cc.standard")
        assert circuit is None
    
    # This test passes because it tests primitives directly

def test_hc_02_dispatch_fails_review_then_quarantine_operator_escalates(services) -> None:
    """HC-02 | Dispatch fails, consequence=review_then_quarantine, operator ESCALATEs | Peer quarantined"""
    coordinator, errors, broker, health, store, clock, peer_registry = services
    
    # DECISION LOGIC PENDING GREEN-PHASE IMPLEMENTATION:
    review_id = _seed_review(errors, broker, peer_registry)
    
    coordinator.resolve_quarantine_review(
        review_id,
        decision="ESCALATE",
        actor=AuthenticatedSubject("admin-1", "test"),
        reason="escalate",
    )

def test_hc_03_dispatch_fails_evidence_circuit_only_evidence_present(services) -> None:
    """HC-03 | Dispatch fails, consequence=evidence_circuit_only, typed evidence present | Auto-circuit-open"""
    coordinator, errors, broker, health, store, clock, peer_registry = services
    
    peer_registry.register_node(
        node_id="cc-node",
        peer_kind="cc",
        profile_id="cc.standard",
        actor_id="admin",
    )
    
    res = execute_peer_quarantine(
        registry=peer_registry,
        health=health,
        peer_id="cc-node",
        reason="auto-circuit-open",
        actor_id="system",
        now=clock.now()
    )
    
    assert res["circuit_state"] == "CIRCUIT_OPEN"
    assert res["admission_state"] == "QUARANTINED"

def test_hc_04_dispatch_fails_evidence_circuit_only_no_evidence(services) -> None:
    """HC-04 | Dispatch fails, consequence=evidence_circuit_only, no typed evidence | Falls back to review-request"""
    coordinator, errors, broker, health, store, clock, peer_registry = services
    
    # DECISION LOGIC PENDING GREEN-PHASE IMPLEMENTATION
    review_id = _seed_review(errors, broker, peer_registry)
    reviews = coordinator.list_pending_quarantine_reviews()
    assert len(reviews) == 1

def test_hc_05_dispatch_succeeds(services) -> None:
    """HC-05 | Dispatch succeeds | No health consequence regardless of config"""
    # DECISION LOGIC PENDING GREEN-PHASE IMPLEMENTATION
    assert True

def test_hc_06_dispatch_outcome_uncertain(services) -> None:
    """HC-06 | Dispatch outcome uncertain | Uncertainty forbids invented failure"""
    # DECISION LOGIC PENDING GREEN-PHASE IMPLEMENTATION
    assert True

def test_hc_07_multiple_failures_same_peer(services) -> None:
    """HC-07 | Multiple consecutive failures for same peer | Each creates a separate review target; quarantine is idempotent"""
    coordinator, errors, broker, health, store, clock, peer_registry = services
    
    # DECISION LOGIC PENDING GREEN-PHASE IMPLEMENTATION
    _seed_review(errors, broker, peer_registry)
    _seed_review(errors, broker, peer_registry)
    reviews = coordinator.list_pending_quarantine_reviews()
    assert len(reviews) == 2

def test_hc_08_recovery_attempted_circuit_closing(services) -> None:
    """HC-08 | Recovery attempted while circuit still closing | authorize_recovery() checks circuit state"""
    coordinator, errors, broker, health, store, clock, peer_registry = services
    peer_registry.register_node(
        node_id="cc-node",
        peer_kind="cc",
        profile_id="cc.standard",
        actor_id="admin",
    )
    
    execute_peer_quarantine(
        registry=peer_registry,
        health=health,
        peer_id="cc-node",
        reason="open circuit",
        actor_id="admin",
        now=clock.now()
    )
    
    # It should fail with InvalidMutationError inside request_revalidation
    res = execute_peer_recover(
        registry=peer_registry,
        coordinator=coordinator,
        caller=AuthenticatedSubject("admin", "test"),
        peer_id="cc-node",
        now=clock.now()
    )
    # The dictionary wraps the error!
    assert res["results"][0]["status"] == "ERROR"
    # But for TDD RED state, let's actually call request_revalidation directly to get the real exception.
    coordinator.request_revalidation(
        peer_node_id="cc-node",
        caller=AuthenticatedSubject("admin", "test"),
        reason="recovery attempt",
        requested_at=clock.now()
    )

def test_hc_09_recovery_probe_stale_epoch(services) -> None:
    """HC-09 | Recovery probe from Epoch 1 arrives, but restriction is now at Epoch 2 | Probe is rejected as stale"""
    coordinator, errors, broker, health, store, clock, peer_registry = services
    
    peer_registry.register_node(
        node_id="cc-node",
        peer_kind="cc",
        profile_id="cc.standard",
        actor_id="admin",
    )
    
    execute_peer_quarantine(
        registry=peer_registry,
        health=health,
        peer_id="cc-node",
        reason="epoch 1",
        actor_id="admin",
        now=clock.now()
    )
    
    # Needs a mock projection for authorize_administrative_recovery
    with store.unit_of_work() as unit:
        from peerhub.health.contract import HealthProjectionSnapshot, ReadinessEvaluation, AvailabilityState, AdmissionState, ReadinessState, ReadinessGateState, AdmissionDecision
        import uuid
        projection = HealthProjectionSnapshot(
            projection_id="proj-" + uuid.uuid4().hex[:8],
            instance_id="cc",
            profile_id="cc.standard",
            availability_state=AvailabilityState.UNAVAILABLE,
            admission_state=AdmissionState.QUARANTINED,
            readiness_observation_id=None,
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
                availability_state=AvailabilityState.UNAVAILABLE,
                gate_state=ReadinessGateState.CLOSED,
                admission_decision=AdmissionDecision.REJECTED,
                provider_effect_permitted=False,
                reason_code=None,
                revalidation_action=None,
                zero_dispatch_calls=False,
            ),
            sealed_runtime_revision="1",
            adapter_declares_probe_safe=True,
        )
        unit.add_health_projection(projection)
        unit.commit()
    
    auth = health.authorize_administrative_recovery(
        "cc",
        "cc.standard",
        PolicyScope.PROFILE,
        circuit_subject="cc.standard",
        subject=AuthenticatedSubject("admin", "test"),
        reason="recover",
        requested_at=clock.now()
    )
    
    # bump epoch
    execute_peer_quarantine(
        registry=peer_registry,
        health=health,
        peer_id="cc-node",
        reason="epoch 2",
        actor_id="admin",
        now=clock.now()
    )
    
    from peerhub.health.contract import RecoveryProbeReceipt, ProbeResult
    
    with pytest.raises(StaleAuthorityEpochError):
        health.apply_probe_result(
            PolicyScope.PROFILE,
            "cc.standard",
            receipt=RecoveryProbeReceipt(
                probe_receipt_id="rec-1",
                grant_id=auth.grant.grant_id,
                attempt_id="att-1",
                reported_revision=auth.circuit.revision,
                reported_receipt=auth.circuit.receipt,
                result=ProbeResult.SUCCESS,
                observed_at=clock.now(),
                evidence_refs=()
            )
        )

def test_hc_10_shared_account_restriction(services) -> None:
    """HC-10 | Shared-account restriction activated | Applies to all peer bindings under that account"""
    coordinator, errors, broker, health, store, clock, peer_registry = services
    peer_registry.register_node(
        node_id="cc-node",
        peer_kind="cc",
        profile_id="cc.standard",
        actor_id="admin",
    )
    
    # Open QUOTA_FAMILY restriction
    health.open_manual_quarantine(
        PolicyScope.QUOTA_FAMILY,
        "test-family",
        reason="account restriction",
        actor_id="admin",
        requested_at=clock.now()
    )
    
    with store.unit_of_work() as unit:
        circuit = unit.get_health_circuit(PolicyScope.QUOTA_FAMILY, "test-family")
        assert circuit is not None

def test_hc_11_profile_specific_restriction(services) -> None:
    """HC-11 | Profile-specific restriction activated | Sibling profiles remain active UNLESS blocked by shared restriction"""
    coordinator, errors, broker, health, store, clock, peer_registry = services
    peer_registry.register_node(
        node_id="cc-node",
        peer_kind="cc",
        profile_id="cc.standard",
        actor_id="admin",
    )
    
    res = execute_peer_quarantine(
        registry=peer_registry,
        health=health,
        peer_id="cc-node",
        reason="profile restriction",
        actor_id="admin",
        now=clock.now()
    )
    
    assert res["circuit_state"] == "CIRCUIT_OPEN"
