"""Tests for peerhub.dispatch.completion.

Covers the ratified test matrix from SLICE5-KICKOFF-R1.md:
- DELIVERY_ONLY + clean response + not truncated -> VERIFIED.
- DELIVERY_ONLY + no response -> INCOMPLETE.
- DELIVERY_ONLY + suspected_truncation=True -> UNVERIFIED.
- Contract WITH requirements, all satisfied -> VERIFIED.
- Contract WITH requirements, one unsatisfied -> INCOMPLETE.
- Execution not terminal -> NOT_APPLICABLE (multiple sub-cases).
- is_promotion_eligible never returns True for non-VERIFIED states.
- is_promotion_eligible distinguishes contract kinds.
- contract_kind present and correct on every CompletionAssessment.
- Exhaustiveness: every CompletionContractKind has a branch.
"""

from __future__ import annotations

import unittest

from peerhub.adapters.contract import ProtocolAssessment
from peerhub.core.execution import ExecutionCertainty
from peerhub.core.protocol import ErrorCode
from peerhub.dispatch.completion import (
    RequirementEvaluation,
    RequirementVerdict,
    assess_completion,
    is_promotion_eligible,
    promotion_contract_kind,
)
from peerhub.dispatch.contract import (
    CompletionAssessment,
    CompletionAssessmentState,
    CompletionContract,
    CompletionContractKind,
    ExecutionOutcome,
)


# --- Helpers -----------------------------------------------------------


def _delivery_contract() -> CompletionContract:
    return CompletionContract(
        contract_id="contract-delivery",
        kind=CompletionContractKind.DELIVERY_ONLY,
        requirements=(),
        replay_safe=False,
    )


def _artifact_contract() -> CompletionContract:
    return CompletionContract(
        contract_id="contract-artifact",
        kind=CompletionContractKind.ARTIFACT_REQUIRED,
        requirements=(
            {"artifact": "output.json", "sha256": "abc"},
        ),
        replay_safe=False,
    )


def _field_contract(
    *, requirements: tuple[dict, ...] | None = None,
) -> CompletionContract:
    return CompletionContract(
        contract_id="contract-field",
        kind=CompletionContractKind.FIELD_REQUIRED,
        requirements=requirements
        or ({"field": "status", "expected": "ok"},),
        replay_safe=False,
    )


def _clean_execution() -> ExecutionOutcome:
    return ExecutionOutcome(
        started=True,
        exit_code=0,
        timed_out=False,
        cancelled=False,
        execution_certainty=ExecutionCertainty.TERMINAL,
    )


def _clean_protocol(
    *,
    response_present: bool = True,
    suspected_truncation: bool = False,
) -> ProtocolAssessment:
    return ProtocolAssessment(
        parsed=True,
        response_present=response_present,
        vendor_completion_marker=True,
        suspected_truncation=suspected_truncation,
        protocol_failure=None,
    )


# --- Test cases --------------------------------------------------------


class TestDeliveryOnlyCompletion(unittest.TestCase):
    """DELIVERY_ONLY contract scenarios."""

    def test_clean_response_not_truncated_is_verified(self) -> None:
        result = assess_completion(
            _delivery_contract(),
            _clean_execution(),
            _clean_protocol(),
        )
        self.assertIs(
            result.state, CompletionAssessmentState.VERIFIED
        )
        self.assertIs(
            result.contract_kind,
            CompletionContractKind.DELIVERY_ONLY,
        )

    def test_no_response_is_incomplete(self) -> None:
        result = assess_completion(
            _delivery_contract(),
            _clean_execution(),
            _clean_protocol(response_present=False),
        )
        self.assertIs(
            result.state, CompletionAssessmentState.INCOMPLETE
        )
        self.assertIs(
            result.contract_kind,
            CompletionContractKind.DELIVERY_ONLY,
        )
        self.assertIn(
            "response_present", result.failed_requirements
        )

    def test_suspected_truncation_is_unverified(self) -> None:
        result = assess_completion(
            _delivery_contract(),
            _clean_execution(),
            _clean_protocol(suspected_truncation=True),
        )
        self.assertIs(
            result.state, CompletionAssessmentState.UNVERIFIED
        )
        self.assertIs(
            result.contract_kind,
            CompletionContractKind.DELIVERY_ONLY,
        )


class TestRequirementsCompletion(unittest.TestCase):
    """Contracts with requirements."""

    def test_all_satisfied_is_verified(self) -> None:
        contract = _field_contract()
        result = assess_completion(
            contract,
            _clean_execution(),
            _clean_protocol(),
            requirement_evaluations=(
                RequirementEvaluation(
                    requirement_index=0,
                    verdict=RequirementVerdict.SATISFIED,
                    evidence_ref="evidence-01",
                ),
            ),
        )
        self.assertIs(
            result.state, CompletionAssessmentState.VERIFIED
        )
        self.assertIs(
            result.contract_kind,
            CompletionContractKind.FIELD_REQUIRED,
        )
        self.assertEqual(
            result.evidence_refs, ("evidence-01",)
        )

    def test_one_unsatisfied_is_incomplete(self) -> None:
        contract = _field_contract(
            requirements=(
                {"field": "status"},
                {"field": "result"},
            )
        )
        result = assess_completion(
            contract,
            _clean_execution(),
            _clean_protocol(),
            requirement_evaluations=(
                RequirementEvaluation(
                    requirement_index=0,
                    verdict=RequirementVerdict.SATISFIED,
                    evidence_ref="evidence-01",
                ),
                RequirementEvaluation(
                    requirement_index=1,
                    verdict=RequirementVerdict.UNSATISFIED,
                ),
            ),
        )
        self.assertIs(
            result.state, CompletionAssessmentState.INCOMPLETE
        )
        self.assertIn("1", result.failed_requirements)

    def test_unverifiable_requirement_is_unverified(self) -> None:
        contract = _field_contract()
        result = assess_completion(
            contract,
            _clean_execution(),
            _clean_protocol(),
            requirement_evaluations=(
                RequirementEvaluation(
                    requirement_index=0,
                    verdict=RequirementVerdict.UNVERIFIABLE,
                ),
            ),
        )
        self.assertIs(
            result.state, CompletionAssessmentState.UNVERIFIED
        )

    def test_duplicate_requirement_index_raises(self) -> None:
        contract = _field_contract(
            requirements=(
                {"field": "a"},
                {"field": "b"},
            )
        )
        with self.assertRaises(ValueError):
            assess_completion(
                contract,
                _clean_execution(),
                _clean_protocol(),
                requirement_evaluations=(
                    RequirementEvaluation(
                        requirement_index=0,
                        verdict=RequirementVerdict.SATISFIED,
                    ),
                    RequirementEvaluation(
                        requirement_index=0,
                        verdict=RequirementVerdict.SATISFIED,
                    ),
                ),
            )

    def test_incomplete_coverage_raises(self) -> None:
        contract = _field_contract(
            requirements=(
                {"field": "a"},
                {"field": "b"},
            )
        )
        with self.assertRaises(ValueError):
            assess_completion(
                contract,
                _clean_execution(),
                _clean_protocol(),
                requirement_evaluations=(
                    RequirementEvaluation(
                        requirement_index=0,
                        verdict=RequirementVerdict.SATISFIED,
                    ),
                ),
            )

    def test_out_of_range_index_raises(self) -> None:
        contract = _field_contract()
        with self.assertRaises(ValueError):
            assess_completion(
                contract,
                _clean_execution(),
                _clean_protocol(),
                requirement_evaluations=(
                    RequirementEvaluation(
                        requirement_index=5,
                        verdict=RequirementVerdict.SATISFIED,
                    ),
                ),
            )


class TestExecutionNotApplicable(unittest.TestCase):
    """NOT_APPLICABLE when execution is non-terminal."""

    def test_timeout_is_not_applicable(self) -> None:
        result = assess_completion(
            _delivery_contract(),
            ExecutionOutcome(
                started=True,
                exit_code=None,
                timed_out=True,
                cancelled=False,
                execution_certainty=ExecutionCertainty.TERMINAL,
            ),
            _clean_protocol(),
        )
        self.assertIs(
            result.state,
            CompletionAssessmentState.NOT_APPLICABLE,
        )
        self.assertIs(
            result.contract_kind,
            CompletionContractKind.DELIVERY_ONLY,
        )

    def test_cancelled_is_not_applicable(self) -> None:
        result = assess_completion(
            _delivery_contract(),
            ExecutionOutcome(
                started=True,
                exit_code=None,
                timed_out=False,
                cancelled=True,
                execution_certainty=ExecutionCertainty.TERMINAL,
            ),
            _clean_protocol(),
        )
        self.assertIs(
            result.state,
            CompletionAssessmentState.NOT_APPLICABLE,
        )

    def test_nonzero_exit_is_not_applicable(self) -> None:
        result = assess_completion(
            _delivery_contract(),
            ExecutionOutcome(
                started=True,
                exit_code=1,
                timed_out=False,
                cancelled=False,
                execution_certainty=ExecutionCertainty.TERMINAL,
            ),
            _clean_protocol(),
        )
        self.assertIs(
            result.state,
            CompletionAssessmentState.NOT_APPLICABLE,
        )

    def test_not_started_is_not_applicable(self) -> None:
        result = assess_completion(
            _delivery_contract(),
            ExecutionOutcome(
                started=False,
                exit_code=None,
                timed_out=False,
                cancelled=False,
                execution_certainty=(
                    ExecutionCertainty.NOT_STARTED
                ),
            ),
            _clean_protocol(),
        )
        self.assertIs(
            result.state,
            CompletionAssessmentState.NOT_APPLICABLE,
        )

    def test_protocol_failure_is_not_applicable(self) -> None:
        result = assess_completion(
            _delivery_contract(),
            ExecutionOutcome(
                started=True,
                exit_code=0,
                timed_out=False,
                cancelled=False,
                execution_certainty=ExecutionCertainty.TERMINAL,
            ),
            ProtocolAssessment(
                parsed=False,
                response_present=False,
                vendor_completion_marker=None,
                suspected_truncation=False,
                protocol_failure=ErrorCode.TRUNCATED_FRAME,
            ),
        )
        self.assertIs(
            result.state,
            CompletionAssessmentState.NOT_APPLICABLE,
        )

    def test_may_have_started_is_not_applicable(self) -> None:
        result = assess_completion(
            _delivery_contract(),
            ExecutionOutcome(
                started=True,
                exit_code=None,
                timed_out=False,
                cancelled=False,
                execution_certainty=(
                    ExecutionCertainty.MAY_HAVE_STARTED
                ),
            ),
            _clean_protocol(),
        )
        self.assertIs(
            result.state,
            CompletionAssessmentState.NOT_APPLICABLE,
        )


class TestIsPromotionEligible(unittest.TestCase):
    """Contract-aware predicate tests."""

    def test_not_verified_never_eligible(self) -> None:
        for state in CompletionAssessmentState:
            if state is CompletionAssessmentState.VERIFIED:
                continue
            assessment = CompletionAssessment(
                state=state,
                contract_kind=(
                    CompletionContractKind.DELIVERY_ONLY
                ),
            )
            self.assertFalse(
                is_promotion_eligible(assessment),
                f"state={state} should not be eligible",
            )

    def test_delivery_only_verified_is_eligible(self) -> None:
        assessment = CompletionAssessment(
            state=CompletionAssessmentState.VERIFIED,
            contract_kind=(
                CompletionContractKind.DELIVERY_ONLY
            ),
        )
        self.assertTrue(is_promotion_eligible(assessment))

    def test_artifact_required_verified_is_eligible(self) -> None:
        assessment = CompletionAssessment(
            state=CompletionAssessmentState.VERIFIED,
            contract_kind=(
                CompletionContractKind.ARTIFACT_REQUIRED
            ),
        )
        self.assertTrue(is_promotion_eligible(assessment))

    def test_promotion_contract_kind_returns_kind(self) -> None:
        assessment = CompletionAssessment(
            state=CompletionAssessmentState.VERIFIED,
            contract_kind=(
                CompletionContractKind.DELIVERY_ONLY
            ),
        )
        self.assertIs(
            promotion_contract_kind(assessment),
            CompletionContractKind.DELIVERY_ONLY,
        )

    def test_promotion_contract_kind_none_for_non_verified(
        self,
    ) -> None:
        assessment = CompletionAssessment(
            state=CompletionAssessmentState.INCOMPLETE,
            contract_kind=(
                CompletionContractKind.DELIVERY_ONLY
            ),
        )
        self.assertIsNone(promotion_contract_kind(assessment))

    def test_promotion_distinguishes_kinds(self) -> None:
        """Callers can distinguish DELIVERY_ONLY from ARTIFACT_REQUIRED."""
        delivery = CompletionAssessment(
            state=CompletionAssessmentState.VERIFIED,
            contract_kind=(
                CompletionContractKind.DELIVERY_ONLY
            ),
        )
        artifact = CompletionAssessment(
            state=CompletionAssessmentState.VERIFIED,
            contract_kind=(
                CompletionContractKind.ARTIFACT_REQUIRED
            ),
        )
        self.assertIs(
            promotion_contract_kind(delivery),
            CompletionContractKind.DELIVERY_ONLY,
        )
        self.assertIs(
            promotion_contract_kind(artifact),
            CompletionContractKind.ARTIFACT_REQUIRED,
        )


class TestContractKindPresence(unittest.TestCase):
    """contract_kind is present and correct on every assessment."""

    def test_contract_kind_required(self) -> None:
        """CompletionAssessment without contract_kind raises."""
        with self.assertRaises(TypeError):
            CompletionAssessment(
                state=CompletionAssessmentState.VERIFIED,
            )

    def test_contract_kind_non_nullable(self) -> None:
        """contract_kind=None raises."""
        with self.assertRaises(ValueError):
            CompletionAssessment(
                state=CompletionAssessmentState.VERIFIED,
                contract_kind=None,
            )

    def test_contract_kind_on_every_assess_completion_result(
        self,
    ) -> None:
        """Every assess_completion path sets contract_kind."""
        # DELIVERY_ONLY path
        r1 = assess_completion(
            _delivery_contract(),
            _clean_execution(),
            _clean_protocol(),
        )
        self.assertIs(
            r1.contract_kind,
            CompletionContractKind.DELIVERY_ONLY,
        )

        # NOT_APPLICABLE path
        r2 = assess_completion(
            _field_contract(),
            ExecutionOutcome(
                started=True,
                exit_code=None,
                timed_out=True,
                cancelled=False,
                execution_certainty=ExecutionCertainty.TERMINAL,
            ),
            _clean_protocol(),
        )
        self.assertIs(
            r2.contract_kind,
            CompletionContractKind.FIELD_REQUIRED,
        )

        # Requirements path
        r3 = assess_completion(
            _field_contract(),
            _clean_execution(),
            _clean_protocol(),
            requirement_evaluations=(
                RequirementEvaluation(
                    requirement_index=0,
                    verdict=RequirementVerdict.SATISFIED,
                ),
            ),
        )
        self.assertIs(
            r3.contract_kind,
            CompletionContractKind.FIELD_REQUIRED,
        )


class TestExhaustivenessGuard(unittest.TestCase):
    """Exhaustiveness: every CompletionContractKind has a branch."""

    def test_every_kind_handled_by_is_promotion_eligible(
        self,
    ) -> None:
        """Asserts is_promotion_eligible covers every enum member.

        If a new member is added to CompletionContractKind without
        a corresponding branch, is_promotion_eligible will raise
        ValueError, causing this test to fail.
        """
        for kind in CompletionContractKind:
            assessment = CompletionAssessment(
                state=CompletionAssessmentState.VERIFIED,
                contract_kind=kind,
            )
            # Must not raise -- if it does, a new kind is unhandled.
            result = is_promotion_eligible(assessment)
            self.assertIsInstance(result, bool)


class TestMultipleRequirements(unittest.TestCase):
    """Edge cases with multiple requirements."""

    def test_mixed_satisfied_and_unsatisfied(self) -> None:
        contract = _field_contract(
            requirements=(
                {"field": "a"},
                {"field": "b"},
                {"field": "c"},
            )
        )
        result = assess_completion(
            contract,
            _clean_execution(),
            _clean_protocol(),
            requirement_evaluations=(
                RequirementEvaluation(
                    requirement_index=0,
                    verdict=RequirementVerdict.SATISFIED,
                    evidence_ref="ev-a",
                ),
                RequirementEvaluation(
                    requirement_index=1,
                    verdict=RequirementVerdict.UNSATISFIED,
                ),
                RequirementEvaluation(
                    requirement_index=2,
                    verdict=RequirementVerdict.SATISFIED,
                    evidence_ref="ev-c",
                ),
            ),
        )
        self.assertIs(
            result.state, CompletionAssessmentState.INCOMPLETE
        )
        self.assertIn("1", result.failed_requirements)
        # Satisfied evidence still collected.
        self.assertIn("ev-a", result.evidence_refs)

    def test_artifact_contract_all_satisfied_verified(
        self,
    ) -> None:
        contract = _artifact_contract()
        result = assess_completion(
            contract,
            _clean_execution(),
            _clean_protocol(),
            requirement_evaluations=(
                RequirementEvaluation(
                    requirement_index=0,
                    verdict=RequirementVerdict.SATISFIED,
                    evidence_ref="digest-verified",
                ),
            ),
        )
        self.assertIs(
            result.state, CompletionAssessmentState.VERIFIED
        )
        self.assertIs(
            result.contract_kind,
            CompletionContractKind.ARTIFACT_REQUIRED,
        )


if __name__ == "__main__":
    unittest.main()
