"""Application coordinator for peer health revalidation."""

from __future__ import annotations

from dataclasses import dataclass

from peerhub.adapters.registry import resolve_peer_target
from peerhub.core.context import Clock, IdSource
from peerhub.core.errors import InvalidMutationError
from peerhub.core.identity import AuthenticatedSubject
from peerhub.core.evidence import EvidenceState
from peerhub.health.contract import (
    AdmissionState,
    AvailabilityState,
    PolicyScope,
    ProbeResult,
    RecoveryProbeReceipt,
)
from peerhub.health.service import HealthService
from peerhub.application.bootstrap import produce_readiness_evidence
from peerhub.application.peer_registry import PeerRegistryService


@dataclass(frozen=True)
class RevalidationResult:
    """The outcome of a health revalidation request."""
    probe_outcome: ProbeResult
    admission_state: AdmissionState
    availability_state: AvailabilityState
    circuit_closed: bool


class HealthRevalidationCoordinator:
    """Coordinate health revalidation probing and state application."""

    def __init__(
        self,
        registry: PeerRegistryService,
        health: HealthService,
        *,
        clock: Clock,
        ids: IdSource,
    ) -> None:
        self._registry = registry
        self._health = health
        self._clock = clock
        self._ids = ids

    def request_revalidation(
        self,
        *,
        peer_node_id: str,
        caller: AuthenticatedSubject,
        reason: str,
        requested_at: int,
    ) -> RevalidationResult:
        """Trigger revalidation for a peer, automatically handling circuit rules."""
        
        node = self._registry.get_node(peer_node_id)
        peer_kind = str(node.state["peer_kind"])
        profile_id = str(node.state["profile_id"])

        target = resolve_peer_target(peer_kind, profile_id=profile_id)

        projection_read = self._health.read_health_projection(
            peer_kind, profile_id, evaluated_at=requested_at
        )
        adm_state = projection_read.effective_admission_state if projection_read else AdmissionState.OPEN

        grant_id = None
        attempt_id = None
        auth = None

        if adm_state == AdmissionState.QUARANTINED:
            auth = self._health.authorize_administrative_recovery(
                peer_kind,
                profile_id,
                PolicyScope.PROFILE,
                circuit_subject=profile_id,
                subject=caller,
                reason=reason,
                requested_at=requested_at,
            )
            grant_id = auth.grant.grant_id
            attempt_id = self._ids.new_id("probe-attempt")
            self._health.claim_probe(grant_id, attempt_id=attempt_id, claimed_at=requested_at)
        elif adm_state == AdmissionState.RECOVERY_REQUIRED:
            auth = self._health.authorize_recovery(
                peer_kind,
                profile_id,
                PolicyScope.PROFILE,
                subject=profile_id,
                authorized_by=caller.principal_id,
            )
            grant_id = auth.grant.grant_id
            attempt_id = self._ids.new_id("probe-attempt")
            self._health.claim_probe(grant_id, attempt_id=attempt_id, claimed_at=requested_at)
        elif adm_state == AdmissionState.COOLDOWN:
            raise InvalidMutationError("Cannot revalidate circuit while it is in COOLDOWN")

        # Run the actual probe
        readiness = produce_readiness_evidence(target, clock=self._clock, ids=self._ids)

        is_success = readiness.evidence.state is EvidenceState.MEASURED
        probe_result = ProbeResult.SUCCESS if is_success else ProbeResult.FAILURE

        circuit_closed = False
        if grant_id is not None and attempt_id is not None:
            assert auth is not None, "grant_id/attempt_id are only set alongside auth"
            if auth.circuit.receipt is None:
                raise InvalidMutationError(
                    "circuit has no receipt to report against a claimed probe"
                )
            receipt = RecoveryProbeReceipt(
                probe_receipt_id=self._ids.new_id("probe-receipt"),
                grant_id=grant_id,
                attempt_id=attempt_id,
                reported_revision=auth.circuit.revision,
                reported_receipt=auth.circuit.receipt,
                result=probe_result,
                observed_at=self._clock.now(),
                evidence_refs=(readiness.evidence.evidence_ref,),
            )
            application = self._health.apply_probe_result(
                PolicyScope.PROFILE,
                profile_id,
                receipt=receipt,
            )
            circuit_closed = application.circuit.state.name == "CIRCUIT_CLOSED"

        runtime_revision = (
            readiness.evidence.value.runtime_revision
            if readiness.evidence.value is not None
            else "unknown"
        )
        
        adapter_declares_probe_safe = getattr(
            target.adapter.descriptor, "adapter_declares_probe_safe", True
        )

        self._health.evaluate_and_persist_readiness(
            readiness,
            sealed_runtime_revision=runtime_revision,
            adapter_declares_probe_safe=adapter_declares_probe_safe,
        )

        # read effective state again
        final_read = self._health.read_health_projection(
            peer_kind, profile_id, evaluated_at=requested_at
        )
        if final_read is None:
             raise RuntimeError("Projection vanished during revalidation")

        return RevalidationResult(
            probe_outcome=probe_result,
            admission_state=final_read.effective_admission_state,
            availability_state=final_read.effective_availability_state,
            circuit_closed=circuit_closed,
        )
