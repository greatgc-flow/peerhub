"""Slice 4 compatibility tests for frozen HR-01 through HR-06."""

from __future__ import annotations

import unittest
from dataclasses import replace

from peerhub.core.evidence import (
    EvidenceRef,
    EvidenceState,
    EvidenceValue,
)
from peerhub.core.execution import ExecutionCertainty
from peerhub.core.protocol import (
    AttemptTerminalObserved,
    OperationalFailureCategory,
)
from peerhub.health.contract import (
    AdmissionDecision,
    AdmissionState,
    AvailabilityState,
    CircuitState,
    EvidenceSubject,
    HealthCircuitSnapshot,
    HealthFailureClassification,
    HealthPolicy,
    HealthProjectionSnapshot,
    HealthStage,
    HealthStageObservation,
    HealthStageStatus,
    PolicyReceipt,
    PolicyScope,
    ProbeDisposition,
    ProbeResult,
    ProbeTransition,
    QuarantineAuthorityClass,
    ReadinessGateState,
    ReadinessState,
    RevalidationAction,
    RecoveryProbeReceipt,
)
from peerhub.telemetry.contract import (
    ReadinessMeasurement,
    ReadinessObserved,
)


def _policy() -> HealthPolicy:
    return HealthPolicy(
        policy_id="v1-health-default-r1",
        revision=1,
        readiness_freshness_seconds=7200,
        recovery_backoff_seconds=(
            30,
            60,
            120,
            240,
            480,
            900,
        ),
        recovery_jitter_fraction=0.2,
        readiness_observation_threshold=1,
        administrative_recovery_probe_limit=1,
    )


def _readiness(
    *,
    observation_id: str,
    observed_at: int,
    valid_until: int,
    integrity_verified: bool,
) -> ReadinessObserved:
    return ReadinessObserved(
        observation_id=observation_id,
        instance_id="ag",
        profile_id="ag.default",
        evidence=EvidenceValue(
            state=EvidenceState.MEASURED,
            source_tag="empirical_probe",
            provider_id="phase0-readiness",
            provider_version="1",
            observed_at=observed_at,
            captured_at=observed_at,
            freshness_ttl=7200,
            evidence_ref=EvidenceRef(
                f"sha256:{observation_id}"
            ),
            value=ReadinessMeasurement(
                runtime_revision="runtime-r17",
                issued_at=1000,
                valid_until=valid_until,
                integrity_verified=integrity_verified,
            ),
        ),
    )


def _receipt(
    *,
    fingerprint: str = "fingerprint-current",
) -> PolicyReceipt:
    return PolicyReceipt(
        incident="incident-current",
        gate_generation=7,
        timestamp=720,
        fingerprint=fingerprint,
    )


def _circuit(
    *,
    authority: QuarantineAuthorityClass = (
        QuarantineAuthorityClass.AUTOMATIC
    ),
    state: CircuitState = CircuitState.CIRCUIT_OPEN,
    fingerprint: str = "fingerprint-current",
    backoff_count: int = 2,
    revision: int = 12,
) -> HealthCircuitSnapshot:
    return HealthCircuitSnapshot(
        circuit_id="circuit-01",
        scope=PolicyScope.PROFILE,
        subject="ag.default",
        state=state,
        quarantine_authority_class=authority,
        receipt=_receipt(fingerprint=fingerprint),
        backoff_count=backoff_count,
        cooldown_until=None,
        revision=revision,
        created_at=700,
        updated_at=720,
    )


def _projection() -> HealthProjectionSnapshot:
    return HealthProjectionSnapshot(
        projection_id="health-projection-01",
        instance_id="ag",
        profile_id="ag.default",
        availability_state=AvailabilityState.UNAVAILABLE,
        admission_state=AdmissionState.RECOVERY_REQUIRED,
        readiness_observation_id="readiness-01",
        operational_projection_id="operational-01",
        operational_projection_revision=3,
        policy_id="v1-health-default-r1",
        policy_revision=1,
        cooldown_until=None,
        evidence_refs=(
            EvidenceRef("sha256:health-projection-01"),
        ),
        revision=4,
        created_at=700,
        updated_at=720,
    )


class TestPhase0HrCompatibility(unittest.TestCase):
    def test_hr01_fresh_verified_readiness_is_admitted(
        self,
    ) -> None:
        from peerhub.health.model import (
            evaluate_readiness_evidence,
        )

        result = evaluate_readiness_evidence(
            _readiness(
                observation_id="readiness-HR-01",
                observed_at=1050,
                valid_until=1100,
                integrity_verified=True,
            ),
            sealed_runtime_revision="runtime-r17",
            policy=_policy(),
            adapter_declares_probe_safe=False,
        )

        self.assertEqual(
            result.readiness_state,
            ReadinessState.READY,
        )
        self.assertEqual(
            result.availability_state,
            AvailabilityState.HEALTHY,
        )
        self.assertEqual(
            result.gate_state,
            ReadinessGateState.OPEN,
        )
        self.assertEqual(
            result.admission_decision,
            AdmissionDecision.ADMITTED,
        )
        self.assertTrue(result.provider_effect_permitted)

    def test_hr01_unverified_probe_is_inconclusive(
        self,
    ) -> None:
        from peerhub.health.model import (
            evaluate_readiness_evidence,
        )

        result = evaluate_readiness_evidence(
            _readiness(
                observation_id="readiness-HR-01-unverified",
                observed_at=1050,
                valid_until=1100,
                integrity_verified=False,
            ),
            sealed_runtime_revision="runtime-r17",
            policy=_policy(),
            adapter_declares_probe_safe=False,
        )

        self.assertEqual(
            result.readiness_state,
            ReadinessState.PROBE_INCONCLUSIVE,
        )
        self.assertEqual(
            result.availability_state,
            AvailabilityState.UNKNOWN,
        )
        self.assertEqual(
            result.gate_state,
            ReadinessGateState.CLOSED,
        )
        self.assertEqual(
            result.admission_decision,
            AdmissionDecision.REJECTED,
        )
        self.assertFalse(result.provider_effect_permitted)

    def test_hr02_expired_unsafe_probe_requires_revalidation(
        self,
    ) -> None:
        from peerhub.health.model import (
            evaluate_readiness_evidence,
        )

        result = evaluate_readiness_evidence(
            _readiness(
                observation_id="readiness-HR-02-expired",
                observed_at=1200,
                valid_until=1100,
                integrity_verified=True,
            ),
            sealed_runtime_revision="runtime-r17",
            policy=_policy(),
            adapter_declares_probe_safe=False,
        )

        self.assertEqual(
            result.readiness_state,
            ReadinessState.READINESS_STALE,
        )
        self.assertEqual(
            result.availability_state,
            AvailabilityState.STALE,
        )
        self.assertEqual(
            result.gate_state,
            ReadinessGateState.CLOSED,
        )
        self.assertEqual(
            result.admission_decision,
            AdmissionDecision.REJECTED,
        )
        self.assertEqual(
            result.reason_code,
            "READINESS_STALE",
        )
        self.assertEqual(
            result.revalidation_action,
            RevalidationAction.REVALIDATION_REQUIRED,
        )
        self.assertTrue(result.zero_dispatch_calls)

    def test_hr03_failure_matrix_and_policy_actions(
        self,
    ) -> None:
        from peerhub.health.model import (
            classify_health_failure,
            derive_policy_action,
        )

        stages = (
            HealthStage.RESOLVE_EXECUTABLE,
            HealthStage.VALIDATE_ENVIRONMENT,
            HealthStage.AUTHENTICATE,
            HealthStage.CONNECT_NETWORK,
            HealthStage.CALL_PROVIDER,
            HealthStage.CHECK_USAGE_ADMISSION,
        )
        cases = (
            (
                0,
                OperationalFailureCategory.EXECUTABLE_UNAVAILABLE,
                PolicyScope.ROOT,
                "adapter-root",
                None,
                None,
            ),
            (
                1,
                OperationalFailureCategory.ENVIRONMENT_UNAVAILABLE,
                PolicyScope.ENVIRONMENT,
                "env-sandbox-v1",
                None,
                None,
            ),
            (
                2,
                OperationalFailureCategory.AUTH_UNAVAILABLE,
                PolicyScope.ROOT,
                "auth-root",
                None,
                None,
            ),
            (
                3,
                OperationalFailureCategory.NETWORK_UNAVAILABLE,
                PolicyScope.ROOT,
                "net-transport-main",
                None,
                None,
            ),
            (
                4,
                OperationalFailureCategory.PROVIDER_UNAVAILABLE,
                PolicyScope.PROFILE,
                "ag.gptoss",
                500,
                None,
            ),
            (
                5,
                OperationalFailureCategory.QUOTA_EXHAUSTED,
                PolicyScope.QUOTA_FAMILY,
                "family-gemini",
                None,
                True,
            ),
        )

        for (
            failed_index,
            expected_category,
            scope,
            subject,
            http_status,
            verified_family_evidence,
        ) in cases:
            trace = tuple(
                HealthStageObservation(
                    stage=stage,
                    status=(
                        HealthStageStatus.FAILED
                        if index == failed_index
                        else HealthStageStatus.OK
                    ),
                )
                for index, stage in enumerate(
                    stages[: failed_index + 1]
                )
            )
            classification = classify_health_failure(
                trace,
                usage_failure_reason=(
                    expected_category
                    if failed_index == 5
                    else None
                ),
                http_status=http_status,
                verified_family_evidence=(
                    verified_family_evidence
                ),
                admission_only=False,
            )

            self.assertIsInstance(
                classification,
                HealthFailureClassification,
            )
            self.assertEqual(
                classification.category,
                expected_category,
            )
            self.assertEqual(
                classification.forbidden_stages_present,
                (),
            )

            action = derive_policy_action(
                classification,
                evidence_subject=EvidenceSubject(
                    scope=scope,
                    subject=subject,
                ),
                receipt=PolicyReceipt(
                    incident=(
                        f"incident-{expected_category.value}"
                    ),
                    gate_generation=1,
                    timestamp=2720,
                    fingerprint=(
                        f"fingerprint-{expected_category.value}"
                    ),
                ),
            )
            self.assertIsNotNone(action)
            self.assertEqual(action.scope, scope)
            self.assertEqual(
                action.circuit_state,
                CircuitState.CIRCUIT_OPEN,
            )
            self.assertEqual(
                action.quarantine_authority_class,
                QuarantineAuthorityClass.AUTOMATIC,
            )

        rate_limited = classify_health_failure(
            tuple(
                HealthStageObservation(
                    stage=stage,
                    status=(
                        HealthStageStatus.FAILED
                        if stage
                        is HealthStage.CHECK_USAGE_ADMISSION
                        else HealthStageStatus.OK
                    ),
                )
                for stage in stages
            ),
            usage_failure_reason=(
                OperationalFailureCategory.RATE_LIMITED
            ),
            http_status=None,
            verified_family_evidence=None,
            admission_only=True,
        )
        self.assertIsNone(
            derive_policy_action(
                rate_limited,
                evidence_subject=None,
                receipt=None,
            )
        )

    def test_hr03_legacy_timeout_is_not_a_v1_category(
        self,
    ) -> None:
        with self.assertRaises(ValueError):
            OperationalFailureCategory(
                "operational_error:timeout"
            )

    def test_hr04_security_quarantine_is_not_auto_cleared(
        self,
    ) -> None:
        from peerhub.health.model import (
            apply_automatic_clearance,
        )

        current = _circuit(
            authority=QuarantineAuthorityClass.SECURITY,
        )
        result = apply_automatic_clearance(
            current,
            clearance_receipt=current.receipt,
            updated_at=721,
        )

        self.assertFalse(result.clearance_applied)
        self.assertEqual(
            result.circuit.state,
            CircuitState.CIRCUIT_OPEN,
        )
        self.assertEqual(
            result.reason,
            "QUARANTINE_AUTHORITY_INSUFFICIENT",
        )

    def test_hr05_probe_grant_is_single_use_and_does_not_heal(
        self,
    ) -> None:
        from peerhub.health.model import (
            authorize_recovery_probe,
            claim_recovery_probe,
        )

        current_projection = _projection()
        current_circuit = _circuit()
        authorization = authorize_recovery_probe(
            current_projection,
            current_circuit,
            grant_id="grant-HR-05",
            authorized_by="administrator",
            authorized_at=710,
            policy=_policy(),
        )

        self.assertEqual(
            authorization.projection.availability_state,
            current_projection.availability_state,
        )
        self.assertEqual(
            authorization.projection.admission_state,
            AdmissionState.PROBE_AUTHORIZED,
        )
        self.assertEqual(
            authorization.circuit.state,
            CircuitState.CIRCUIT_OPEN,
        )
        self.assertEqual(
            authorization.grant.remaining_probes,
            1,
        )

        first = claim_recovery_probe(
            authorization.grant,
            attempt_id="probe-attempt-1",
            claimed_at=711,
        )
        second = claim_recovery_probe(
            first.grant,
            attempt_id="probe-attempt-2",
            claimed_at=712,
        )

        self.assertEqual(
            first.disposition,
            ProbeDisposition.EXECUTED,
        )
        self.assertEqual(first.grant.remaining_probes, 0)
        self.assertEqual(
            second.disposition,
            ProbeDisposition.REJECTED,
        )
        self.assertEqual(
            second.reason,
            "PROBE_GRANT_EXHAUSTED",
        )
        self.assertEqual(second.grant, first.grant)

    def test_hr06_matching_failure_increments_backoff(
        self,
    ) -> None:
        from peerhub.health.model import (
            apply_recovery_probe_result,
        )

        current = _circuit(backoff_count=2, revision=12)
        result = apply_recovery_probe_result(
            current,
            RecoveryProbeReceipt(
                probe_receipt_id="probe-receipt-HR-06",
                grant_id="grant-HR-06",
                attempt_id="probe-attempt-HR-06",
                reported_revision=12,
                reported_receipt=current.receipt,
                result=ProbeResult.FAILURE,
                observed_at=722,
                evidence_refs=(
                    EvidenceRef("sha256:probe-HR-06"),
                ),
            ),
            updated_at=722,
        )

        self.assertTrue(result.reported_matches_current)
        self.assertEqual(
            result.transition,
            ProbeTransition.FAILURE_BACKOFF_INCREMENTED,
        )
        self.assertEqual(
            result.circuit.state,
            CircuitState.CIRCUIT_OPEN,
        )
        self.assertEqual(result.circuit.backoff_count, 3)

    def test_hr06_stale_success_is_no_op(self) -> None:
        from peerhub.health.model import (
            apply_recovery_probe_result,
        )

        current = _circuit(
            state=CircuitState.CIRCUIT_CLOSED,
            backoff_count=4,
            revision=12,
        )
        receipt = RecoveryProbeReceipt(
            probe_receipt_id="probe-receipt-HR-06-stale",
            grant_id="grant-HR-06-stale",
            attempt_id="probe-attempt-HR-06-stale",
            reported_revision=12,
            reported_receipt=_receipt(
                fingerprint="fingerprint-stale"
            ),
            result=ProbeResult.SUCCESS,
            observed_at=722,
            evidence_refs=(
                EvidenceRef("sha256:probe-HR-06-stale"),
            ),
        )

        result = apply_recovery_probe_result(
            current,
            receipt,
            updated_at=722,
        )
        self.assertFalse(result.reported_matches_current)
        self.assertEqual(
            result.transition,
            ProbeTransition.STALE_PROBE_NO_OP,
        )
        self.assertEqual(result.circuit, current)

    def test_stale_fingerprint_failed_probe_is_also_no_op(
        self,
    ) -> None:
        from peerhub.health.model import (
            apply_recovery_probe_result,
        )

        current = _circuit(backoff_count=4, revision=12)
        stale_failure = RecoveryProbeReceipt(
            probe_receipt_id="probe-receipt-stale-failure",
            grant_id="grant-stale-failure",
            attempt_id="probe-attempt-stale-failure",
            reported_revision=12,
            reported_receipt=replace(
                current.receipt,
                fingerprint="fingerprint-stale",
            ),
            result=ProbeResult.FAILURE,
            observed_at=723,
            evidence_refs=(
                EvidenceRef("sha256:stale-failure"),
            ),
        )

        result = apply_recovery_probe_result(
            current,
            stale_failure,
            updated_at=723,
        )

        self.assertFalse(result.reported_matches_current)
        self.assertEqual(
            result.transition,
            ProbeTransition.STALE_PROBE_NO_OP,
        )
        self.assertEqual(result.circuit, current)

    def test_terminal_event_is_operational_only(self) -> None:
        event = AttemptTerminalObserved(
            instance_id="ag",
            profile_id="ag.default",
            transport="pty",
            operational_failure_category=(
                OperationalFailureCategory.NETWORK_UNAVAILABLE
            ),
            execution_certainty=ExecutionCertainty.TERMINAL,
            process_integrity=True,
            started_at=100,
            terminal_at=125,
            latency=25,
            evidence_refs=("sha256:terminal-observation",),
        )
        self.assertFalse(hasattr(event, "completion"))
        self.assertFalse(
            hasattr(event, "completion_assessment")
        )


if __name__ == "__main__":
    unittest.main()
