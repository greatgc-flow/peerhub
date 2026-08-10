"""Slice 4 compatibility tests for frozen RT-04 through RT-06."""

from __future__ import annotations

import unittest

from peerhub.core.evidence import (
    EvidenceRef,
    EvidenceState,
    EvidenceValue,
)
from peerhub.core.protocol import ErrorCode
from peerhub.dispatch.capability import CapabilityTier
from peerhub.routing.contract import (
    ConfigurationSnapshot,
    RouteCandidateDecision,
    RouteCandidateInput,
    RouteDecision,
    RouteEligibility,
)


def _usage_absent(
    candidate_id: str,
) -> EvidenceValue[object]:
    return EvidenceValue(
        state=EvidenceState.ABSENT,
        source_tag="empirical_probe",
        provider_id="phase0-usage",
        provider_version="1",
        observed_at=None,
        captured_at=800,
        freshness_ttl=300,
        evidence_ref=EvidenceRef(
            f"sha256:usage-{candidate_id}"
        ),
        value=None,
    )


def _candidate(
    candidate_id: str,
    *,
    eligible: bool,
    reason: str | None,
) -> RouteCandidateInput:
    return RouteCandidateInput(
        candidate_id=candidate_id,
        instance_id=f"instance-{candidate_id}",
        representative_profile_id=(
            f"profile-{candidate_id}"
        ),
        eligible=eligible,
        exclusion_reason=reason,
        usage_evidence=_usage_absent(candidate_id),
        in_flight_reservations=0,
        evidence_refs=(
            EvidenceRef(
                f"sha256:routing-{candidate_id}"
            ),
        ),
    )


class TestPhase0RtCompatibility(unittest.TestCase):
    def test_rt04_exclusions_are_zero_weight(
        self,
    ) -> None:
        from peerhub.routing.model import (
            evaluate_route_candidates,
        )

        decisions = evaluate_route_candidates(
            (
                _candidate(
                    "candidate-terminal",
                    eligible=False,
                    reason="TERMINAL_TIER",
                ),
                _candidate(
                    "candidate-eligible",
                    eligible=True,
                    reason=None,
                ),
                _candidate(
                    "candidate-excluded",
                    eligible=False,
                    reason="EXCLUDED",
                ),
            )
        )

        self.assertEqual(
            tuple(
                (
                    decision.candidate_id,
                    decision.effective_weight,
                    decision.exclusion_reason,
                )
                for decision in decisions
            ),
            (
                (
                    "candidate-eligible",
                    1,
                    None,
                ),
                (
                    "candidate-excluded",
                    0,
                    "EXCLUDED",
                ),
                (
                    "candidate-terminal",
                    0,
                    "TERMINAL_TIER",
                ),
            ),
        )
        self.assertEqual(
            tuple(
                decision.candidate_id
                for decision in decisions
                if (
                    decision.eligibility
                    is RouteEligibility.ELIGIBLE
                )
            ),
            ("candidate-eligible",),
        )

    def test_rt05_equal_weight_selection_is_golden(
        self,
    ) -> None:
        from peerhub.routing.model import (
            select_equal_weight_candidate,
        )

        result = select_equal_weight_candidate(
            client_request_id="request-RT-05",
            snapshot_digest=(
                "abcdef0123456789abcdef0123456789"
                "abcdef0123456789abcdef0123456789"
            ),
            candidate_ids=(
                "candidate-b",
                "candidate-a",
            ),
        )

        self.assertEqual(
            result.audit_seed,
            "786f77a42116fda27d29a3f12bc8e854"
            "e405845f66818cd0f74104e381e500b7",
        )
        self.assertEqual(result.selection_index, 0)
        self.assertEqual(
            result.ordered_candidates,
            (
                "candidate-a",
                "candidate-b",
            ),
        )
        self.assertEqual(
            result.selected_candidate,
            "candidate-a",
        )

    def test_rt06_checks_configuration_revision_only(
        self,
    ) -> None:
        from peerhub.routing.model import (
            validate_route_for_dispatch,
        )

        candidate = RouteCandidateDecision(
            candidate_id="candidate-a",
            instance_id="instance-candidate-a",
            representative_profile_id="profile-candidate-a",
            eligibility=RouteEligibility.ELIGIBLE,
            effective_weight=1,
            exclusion_reason=None,
            evidence_refs=(
                EvidenceRef("sha256:candidate-a"),
            ),
        )
        decision = RouteDecision(
            decision_id="route-decision-RT-06",
            client_request_id="request-RT-06",
            configuration=ConfigurationSnapshot(
                revision=10,
                digest="a" * 64,
            ),
            admission_snapshot_id="admission-snapshot-10",
            admission_snapshot_revision=10,
            admission_snapshot_digest="b" * 64,
            routing_policy_id="equal-weight-r1",
            routing_policy_revision=1,
            required_capability_tier=CapabilityTier.READ_ONLY,
            candidates=(candidate,),
            audit_seed="c" * 64,
            selection_index=0,
            selected_candidate_id="candidate-a",
            created_at=820,
        )

        result = validate_route_for_dispatch(
            decision,
            current_configuration=ConfigurationSnapshot(
                revision=11,
                digest="d" * 64,
            ),
        )

        self.assertFalse(result.dispatch_permitted)
        self.assertEqual(
            result.error_code,
            ErrorCode.CONFIGURATION_STALE,
        )
        self.assertEqual(result.dispatch_count, 0)
        self.assertEqual(
            result.replanning_input_revision,
            11,
        )


if __name__ == "__main__":
    unittest.main()
