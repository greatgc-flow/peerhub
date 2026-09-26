"""R4 7.1 decision table (HC-01..HC-06)."""
import pytest

from peerhub.application.health_consequence import needs_review
from peerhub.core.execution import ExecutionCertainty
from peerhub.dispatch.contract import RequestState

F, T = RequestState.FAILED, ExecutionCertainty.TERMINAL


@pytest.mark.parametrize("mode", ["report_only", "review_then_quarantine"])
def test_definitive_failure_creates_a_review_in_both_review_modes(mode):
    assert needs_review(mode, request_state=F, execution_certainty=T, error_code=None)
    assert needs_review(mode, request_state=F, execution_certainty=T, error_code="X")


def test_evidence_circuit_only_reviews_only_without_typed_evidence():
    assert needs_review("evidence_circuit_only", request_state=F, execution_certainty=T, error_code=None)
    assert not needs_review("evidence_circuit_only", request_state=F, execution_certainty=T, error_code="TYPED")


@pytest.mark.parametrize("mode", ["report_only", "review_then_quarantine", "evidence_circuit_only"])
def test_success_and_uncertain_outcomes_never_create_a_consequence(mode):
    assert not needs_review(mode, request_state=RequestState.SUCCEEDED_VERIFIED, execution_certainty=T, error_code=None)
    for cert in (ExecutionCertainty.MAY_HAVE_STARTED, ExecutionCertainty.STARTED, ExecutionCertainty.NOT_STARTED):
        assert not needs_review(mode, request_state=F, execution_certainty=cert, error_code=None)
