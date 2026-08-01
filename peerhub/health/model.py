"""Phase 0 readiness evidence evaluation and health recovery model."""

from __future__ import annotations

import hashlib
from dataclasses import replace

from peerhub.core.protocol import canonical_json_bytes
from peerhub.health.contract import (
    AdmissionDecision,
    AdmissionSnapshot,
    AdmissionSnapshotEntry,
    AdmissionState,
    AutomaticClearanceResult,
    AvailabilityState,
    CircuitState,
    CooldownEvaluation,
    EvidenceSubject,
    HealthCircuitSnapshot,
    HealthFailureClassification,
    HealthPolicy,
    HealthProjectionSnapshot,
    HealthStage,
    HealthStageObservation,
    HealthStageStatus,
    OperationalFailureCategory,
    PolicyAction,
    PolicyReceipt,
    ProbeDisposition,
    ProbeResult,
    ProbeTransition,
    QuarantineAuthorityClass,
    ReadinessEvaluation,
    ReadinessGateState,
    ReadinessState,
    RecoveryProbeApplication,
    RecoveryProbeAuthorization,
    RecoveryProbeClaimResult,
    RecoveryProbeGrant,
    RecoveryProbeReceipt,
    RevalidationAction,
)
from peerhub.telemetry.contract import ReadinessObserved

_CANONICAL_STAGES = (
    HealthStage.RESOLVE_EXECUTABLE,
    HealthStage.VALIDATE_ENVIRONMENT,
    HealthStage.AUTHENTICATE,
    HealthStage.CONNECT_NETWORK,
    HealthStage.CALL_PROVIDER,
    HealthStage.CHECK_USAGE_ADMISSION,
)

_STAGE_TO_CATEGORY = {
    HealthStage.RESOLVE_EXECUTABLE: OperationalFailureCategory.EXECUTABLE_UNAVAILABLE,
    HealthStage.VALIDATE_ENVIRONMENT: OperationalFailureCategory.ENVIRONMENT_UNAVAILABLE,
    HealthStage.AUTHENTICATE: OperationalFailureCategory.AUTH_UNAVAILABLE,
    HealthStage.CONNECT_NETWORK: OperationalFailureCategory.NETWORK_UNAVAILABLE,
    HealthStage.CALL_PROVIDER: OperationalFailureCategory.PROVIDER_UNAVAILABLE,
}

_ADMISSION_STATE_PRECEDENCE = {
    AdmissionState.OPEN: 0,
    AdmissionState.PROBE_AUTHORIZED: 1,
    AdmissionState.RECOVERY_REQUIRED: 2,
    AdmissionState.COOLDOWN: 3,
    AdmissionState.QUARANTINED: 4,
}


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


def resolve_admission_state(
    readiness: ReadinessEvaluation,
    *,
    circuit_states: tuple[AdmissionState, ...] = (),
) -> AdmissionState:
    """Resolve the ratified Step 6B aggregate admission state.

    Readiness contributes only a baseline. Circuit-derived states are
    then folded using the precedence frozen by the 2026-08-01 Step 6B
    pre-service addendum:

    QUARANTINED > COOLDOWN > RECOVERY_REQUIRED
        > PROBE_AUTHORIZED > OPEN.

    Because PROBE_AUTHORIZED ranks below every other closed state, it
    can win only when it is the sole contributing non-OPEN state.
    """

    if not isinstance(readiness, ReadinessEvaluation):
        raise ValueError(
            "readiness must be ReadinessEvaluation"
        )

    baseline_by_readiness = {
        (
            ReadinessState.READY,
            ReadinessGateState.OPEN,
        ): AdmissionState.OPEN,
        (
            ReadinessState.PROBE_INCONCLUSIVE,
            ReadinessGateState.CLOSED,
        ): AdmissionState.RECOVERY_REQUIRED,
        (
            ReadinessState.READINESS_STALE,
            ReadinessGateState.CLOSED,
        ): AdmissionState.RECOVERY_REQUIRED,
    }
    try:
        baseline = baseline_by_readiness[
            (
                readiness.readiness_state,
                readiness.gate_state,
            )
        ]
    except KeyError:
        raise ValueError(
            "readiness state and gate state are inconsistent"
        ) from None

    normalized_circuit_states = tuple(circuit_states)
    if any(
        not isinstance(state, AdmissionState)
        for state in normalized_circuit_states
    ):
        raise ValueError(
            "circuit_states must contain only AdmissionState values"
        )

    return max(
        (baseline, *normalized_circuit_states),
        key=_ADMISSION_STATE_PRECEDENCE.__getitem__,
    )


def classify_health_failure(
    attempted_trace: tuple[HealthStageObservation, ...] | list[HealthStageObservation],
    *,
    usage_failure_reason: OperationalFailureCategory | None = None,
    http_status: int | None = None,
    verified_family_evidence: bool | None = None,
    admission_only: bool = False,
) -> HealthFailureClassification:
    """Derive operational failure classification and forbidden downstream trace audit."""
    trace_tuple = tuple(attempted_trace)
    failed_index = None
    failed_obs = None
    for index, obs in enumerate(trace_tuple):
        if obs.status is HealthStageStatus.FAILED:
            failed_index = index
            failed_obs = obs
            break

    if failed_obs is None or failed_index is None:
        raise ValueError("attempted_trace contains no failed stage")

    failed_stage = failed_obs.stage
    if failed_stage is HealthStage.CHECK_USAGE_ADMISSION:
        if usage_failure_reason is None:
            raise ValueError(
                "usage_failure_reason required for CHECK_USAGE_ADMISSION failure"
            )
        category = usage_failure_reason
    else:
        category = _STAGE_TO_CATEGORY[failed_stage]

    canonical_index = _CANONICAL_STAGES.index(failed_stage)
    forbidden_downstream = _CANONICAL_STAGES[canonical_index + 1 :]
    forbidden_present = tuple(
        obs.stage
        for obs in trace_tuple[failed_index + 1 :]
        if obs.stage in forbidden_downstream
    )

    return HealthFailureClassification(
        category=category,
        attempted_trace=trace_tuple,
        forbidden_downstream_stages=forbidden_downstream,
        forbidden_stages_present=forbidden_present,
        http_status=http_status,
        verified_family_evidence=verified_family_evidence,
        admission_only=admission_only,
    )


def derive_policy_action(
    classification: HealthFailureClassification,
    *,
    evidence_subject: EvidenceSubject | None,
    receipt: PolicyReceipt | None,
) -> PolicyAction | None:
    """Derive frozen policy action from health failure classification."""
    if classification.admission_only or evidence_subject is None or receipt is None:
        return None

    return PolicyAction(
        scope=evidence_subject.scope,
        subject=evidence_subject.subject,
        circuit_state=CircuitState.CIRCUIT_OPEN,
        quarantine_authority_class=QuarantineAuthorityClass.AUTOMATIC,
        receipt=receipt,
    )


def apply_policy_action(
    action: PolicyAction,
    circuit: HealthCircuitSnapshot | None,
    *,
    circuit_id: str | None = None,
    created_at: int,
    updated_at: int,
) -> HealthCircuitSnapshot:
    """Apply one policy action to its scoped health circuit.

    Pure reducers do not mint identifiers. ``circuit_id`` is therefore
    required when creating a circuit and optional when updating an
    existing one.

    A repeated action for the same incident preserves accumulated
    backoff and its existing cooldown boundary. A genuinely new incident
    resets both. Exact backoff jitter derivation remains deferred by
    Slice 4 decision 6 and is not performed here.
    """

    if circuit is None:
        if circuit_id is None:
            raise ValueError(
                "circuit_id is required for a new circuit"
            )
        return HealthCircuitSnapshot(
            circuit_id=circuit_id,
            scope=action.scope,
            subject=action.subject,
            state=action.circuit_state,
            quarantine_authority_class=(
                action.quarantine_authority_class
            ),
            receipt=action.receipt,
            backoff_count=0,
            cooldown_until=None,
            revision=1,
            created_at=created_at,
            updated_at=updated_at,
        )

    if (
        circuit.scope is not action.scope
        or circuit.subject != action.subject
    ):
        raise ValueError(
            "policy action and circuit subjects differ"
        )
    if (
        circuit_id is not None
        and circuit_id != circuit.circuit_id
    ):
        raise ValueError(
            "circuit_id does not match the existing circuit"
        )

    # The incident field identifies whether this action continues the
    # existing recovery lifecycle. The complete receipt remains the
    # identity fence used by clearance and recovery-probe reducers.
    same_incident = (
        circuit.receipt is not None
        and circuit.receipt.incident
        == action.receipt.incident
    )

    return replace(
        circuit,
        state=action.circuit_state,
        quarantine_authority_class=(
            action.quarantine_authority_class
        ),
        receipt=action.receipt,
        backoff_count=(
            circuit.backoff_count
            if same_incident
            else 0
        ),
        cooldown_until=(
            circuit.cooldown_until
            if same_incident
            else None
        ),
        revision=circuit.revision + 1,
        updated_at=updated_at,
    )


def apply_automatic_clearance(
    circuit: HealthCircuitSnapshot,
    *,
    clearance_receipt: PolicyReceipt | None,
    updated_at: int,
) -> AutomaticClearanceResult:
    """Apply receipt-fenced automatic clearance to a health circuit."""

    if (
        circuit.quarantine_authority_class
        is not QuarantineAuthorityClass.AUTOMATIC
    ):
        return AutomaticClearanceResult(
            circuit=circuit,
            clearance_applied=False,
            reason="QUARANTINE_AUTHORITY_INSUFFICIENT",
        )

    # PolicyReceipt dataclass equality compares the complete established
    # identity fence: incident, gate generation, timestamp, fingerprint.
    if (
        circuit.receipt is None
        or clearance_receipt != circuit.receipt
    ):
        return AutomaticClearanceResult(
            circuit=circuit,
            clearance_applied=False,
            reason="CLEARANCE_RECEIPT_MISMATCH",
        )

    cleared_circuit = replace(
        circuit,
        state=CircuitState.CIRCUIT_CLOSED,
        backoff_count=0,
        cooldown_until=None,
        revision=circuit.revision + 1,
        updated_at=updated_at,
    )
    return AutomaticClearanceResult(
        circuit=cleared_circuit,
        clearance_applied=True,
        reason="AUTOMATIC_CLEARANCE_APPLIED",
    )


def authorize_recovery_probe(
    projection: HealthProjectionSnapshot,
    circuit: HealthCircuitSnapshot,
    *,
    grant_id: str,
    authorized_by: str,
    authorized_at: int,
    policy: HealthPolicy,
) -> RecoveryProbeAuthorization:
    """Authorize a single recovery probe attempt without direct healthy write."""
    del policy  # Policy limit checked during administrative authorization call.

    if circuit.receipt is None:
        raise ValueError(
            "circuit must have a receipt to authorize probe"
        )

    updated_projection = replace(
        projection,
        admission_state=AdmissionState.PROBE_AUTHORIZED,
        revision=projection.revision + 1,
        updated_at=authorized_at,
    )
    grant = RecoveryProbeGrant(
        grant_id=grant_id,
        circuit_id=circuit.circuit_id,
        receipt=circuit.receipt,
        authorized_by=authorized_by,
        authorized_at=authorized_at,
        remaining_probes=1,
        consumed_at=None,
        consumed_by_attempt_id=None,
        revision=1,
    )
    return RecoveryProbeAuthorization(
        projection=updated_projection,
        circuit=circuit,
        grant=grant,
    )


def claim_recovery_probe(
    grant: RecoveryProbeGrant,
    *,
    attempt_id: str,
    claimed_at: int,
) -> RecoveryProbeClaimResult:
    """Attempt to claim a single-use recovery probe grant via CAS-style state transition."""
    if grant.remaining_probes == 1:
        updated_grant = replace(
            grant,
            remaining_probes=0,
            consumed_at=claimed_at,
            consumed_by_attempt_id=attempt_id,
            revision=grant.revision + 1,
        )
        return RecoveryProbeClaimResult(
            grant=updated_grant,
            attempt_id=attempt_id,
            disposition=ProbeDisposition.EXECUTED,
            reason=None,
        )

    return RecoveryProbeClaimResult(
        grant=grant,
        attempt_id=attempt_id,
        disposition=ProbeDisposition.REJECTED,
        reason="PROBE_GRANT_EXHAUSTED",
    )


def apply_recovery_probe_result(
    circuit: HealthCircuitSnapshot,
    receipt: RecoveryProbeReceipt,
    *,
    updated_at: int,
) -> RecoveryProbeApplication:
    """Apply identity-fenced probe result to circuit."""
    matches = (
        circuit.receipt is not None
        and receipt.reported_receipt == circuit.receipt
        and receipt.reported_revision == circuit.revision
    )
    if not matches:
        return RecoveryProbeApplication(
            circuit=circuit,
            reported_matches_current=False,
            transition=ProbeTransition.STALE_PROBE_NO_OP,
        )

    if receipt.result is ProbeResult.FAILURE:
        updated_circuit = replace(
            circuit,
            backoff_count=circuit.backoff_count + 1,
            revision=circuit.revision + 1,
            updated_at=updated_at,
        )
        return RecoveryProbeApplication(
            circuit=updated_circuit,
            reported_matches_current=True,
            transition=ProbeTransition.FAILURE_BACKOFF_INCREMENTED,
        )

    updated_circuit = replace(
        circuit,
        state=CircuitState.CIRCUIT_CLOSED,
        backoff_count=0,
        revision=circuit.revision + 1,
        updated_at=updated_at,
    )
    return RecoveryProbeApplication(
        circuit=updated_circuit,
        reported_matches_current=True,
        transition=ProbeTransition.SUCCESS_CIRCUIT_CLOSED,
    )


def evaluate_cooldown(
    circuit: HealthCircuitSnapshot,
    *,
    policy: HealthPolicy,
    now: int,
) -> CooldownEvaluation:
    """Evaluate the admission state at a circuit cooldown boundary.

    Deterministic jitter remains deferred by Slice 4 decision 6. When no
    explicit ``cooldown_until`` has been persisted, this reducer uses the
    unjittered backoff-ladder duration at ``backoff_count``, capped at the
    final ladder entry. ``retry_after`` is the absolute retry boundary.

    A persisted ``cooldown_until`` is treated as authoritative so a
    future ratified jitter implementation can supply its calculated
    boundary without changing this reducer's result contract.
    """

    if type(now) is not int or now < 0:
        raise ValueError(
            "now must be a nonnegative integer"
        )

    if circuit.state is CircuitState.CIRCUIT_CLOSED:
        return CooldownEvaluation(
            admission_state=AdmissionState.OPEN,
            retry_after=None,
            cooldown_ended=True,
        )

    if (
        circuit.quarantine_authority_class
        is not QuarantineAuthorityClass.AUTOMATIC
    ):
        return CooldownEvaluation(
            admission_state=AdmissionState.QUARANTINED,
            retry_after=None,
            cooldown_ended=False,
        )

    ladder_index = min(
        circuit.backoff_count,
        len(policy.recovery_backoff_seconds) - 1,
    )
    retry_at = circuit.cooldown_until
    if retry_at is None:
        retry_at = (
            circuit.updated_at
            + policy.recovery_backoff_seconds[
                ladder_index
            ]
        )

    if now < retry_at:
        return CooldownEvaluation(
            admission_state=AdmissionState.COOLDOWN,
            retry_after=retry_at,
            cooldown_ended=False,
        )

    return CooldownEvaluation(
        admission_state=AdmissionState.RECOVERY_REQUIRED,
        retry_after=None,
        cooldown_ended=True,
    )


def canonical_admission_snapshot_digest(
    entries: tuple[AdmissionSnapshotEntry, ...],
    *,
    configuration_revision: int,
    policy_id: str,
    policy_revision: int,
) -> str:
    """Hash the ratified semantic admission-snapshot projection."""
    ordered_entries = tuple(
        sorted(tuple(entries), key=lambda entry: (entry.instance_id, entry.profile_id))
    )
    projection = {
        "configuration_revision": configuration_revision,
        "policy_id": policy_id,
        "policy_revision": policy_revision,
        "entries": [
            {
                "instance_id": entry.instance_id,
                "profile_id": entry.profile_id,
                "health_projection_id": entry.health_projection_id,
                "health_projection_revision": entry.health_projection_revision,
                "availability_state": entry.availability_state.value,
                "admission_state": entry.admission_state.value,
                "evidence_refs": [str(ref) for ref in entry.evidence_refs],
            }
            for entry in ordered_entries
        ],
    }
    return hashlib.sha256(canonical_json_bytes(projection)).hexdigest()


def freeze_admission_snapshot(
    entries: tuple[AdmissionSnapshotEntry, ...],
    *,
    snapshot_id: str,
    digest: str,
    configuration_revision: int,
    policy_id: str,
    policy_revision: int,
    revision: int,
    created_at: int,
) -> AdmissionSnapshot:
    """Freeze already-derived admission entries and caller-supplied digest.

    Digest-byte canonicalization is deliberately outside this reducer
    until the separate service-layer digest contract is ratified.
    """

    return AdmissionSnapshot(
        snapshot_id=snapshot_id,
        revision=revision,
        digest=digest,
        configuration_revision=configuration_revision,
        policy_id=policy_id,
        policy_revision=policy_revision,
        entries=tuple(entries),
        created_at=created_at,
    )
