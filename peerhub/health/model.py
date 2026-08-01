"""Phase 0 readiness evidence evaluation model."""

from __future__ import annotations

from peerhub.health.contract import (
    AdmissionDecision,
    AvailabilityState,
    HealthPolicy,
    ReadinessEvaluation,
    ReadinessGateState,
    ReadinessState,
    RevalidationAction,
)
from peerhub.telemetry.contract import ReadinessObserved


def evaluate_readiness_evidence(
    readiness: ReadinessObserved,
    *,
    sealed_runtime_revision: str,
    policy: HealthPolicy,
    adapter_declares_probe_safe: bool,
) -> ReadinessEvaluation:
    """Evaluate readiness measurement evidence against policy and sealed runtime context."""
    del policy  # Unused in Slice 4 freshness evaluation; encoded via valid_until.
    del adapter_declares_probe_safe  # Unused in Slice 4; safe-revalidation probe branch is un-implemented backlog.

    evidence = readiness.evidence
    observed_at = evidence.observed_at
    measurement = evidence.value

    # Branch 3: Expired evidence (valid_until < observed_at).
    if (
        measurement is not None
        and observed_at is not None
        and measurement.valid_until < observed_at
    ):
        return ReadinessEvaluation(
            readiness_state=ReadinessState.READINESS_STALE,
            availability_state=AvailabilityState.STALE,
            gate_state=ReadinessGateState.CLOSED,
            admission_decision=AdmissionDecision.REJECTED,
            provider_effect_permitted=False,
            reason_code="READINESS_STALE",
            revalidation_action=RevalidationAction.REVALIDATION_REQUIRED,
            zero_dispatch_calls=True,
        )

    # Note on boundary: valid_until == observed_at is treated as still-fresh per task brief.
    # Branch 2: Unverified integrity or runtime_revision mismatch.
    if (
        measurement is None
        or not measurement.integrity_verified
        or measurement.runtime_revision != sealed_runtime_revision
    ):
        return ReadinessEvaluation(
            readiness_state=ReadinessState.PROBE_INCONCLUSIVE,
            availability_state=AvailabilityState.UNKNOWN,
            gate_state=ReadinessGateState.CLOSED,
            admission_decision=AdmissionDecision.REJECTED,
            provider_effect_permitted=False,
            reason_code=None,
            revalidation_action=None,
            zero_dispatch_calls=False,
        )

    # Branch 1: Fresh, verified, matching runtime revision.
    return ReadinessEvaluation(
        readiness_state=ReadinessState.READY,
        availability_state=AvailabilityState.HEALTHY,
        gate_state=ReadinessGateState.OPEN,
        admission_decision=AdmissionDecision.ADMITTED,
        provider_effect_permitted=True,
        reason_code=None,
        revalidation_action=None,
        zero_dispatch_calls=False,
    )
