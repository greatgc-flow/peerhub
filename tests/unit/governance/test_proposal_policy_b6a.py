"""Tests for B6a TDD RED phase of proposal policy."""

import pytest

from peerhub.governance.proposal_policy import (
    ApprovalSubmission,
    EffectRoutingError,
    ProposalDisposition,
    ProposalInputs,
    build_approval_submission,
    decide_proposal_disposition,
    route_effect_kind,
)
from peerhub.governance.invariant_requests import RATIFIED_INVARIANT_EFFECT_KIND


def _make_inputs(**kwargs) -> ProposalInputs:
    defaults = {
        "resolved_recovery": False,
        "existing_escalation": False,
        "eligible_count": 3,
        "gate_closed_any_eligible": False,
        "eligible_dissent": False,
        "all_required_agree": False,
        "independent_agreement": False,
        "proposer_only": False,
        "high_risk": False,
    }
    defaults.update(kwargs)
    return ProposalInputs(**defaults)


def test_b6a_precedence_1_resolved_recovery_wins_over_all():
    inputs = _make_inputs(
        resolved_recovery=True,
        existing_escalation=True,
        eligible_count=1,
        gate_closed_any_eligible=True,
        eligible_dissent=True,
        all_required_agree=True,
        independent_agreement=True,
        proposer_only=True,
        high_risk=True,
    )
    assert decide_proposal_disposition(inputs) == ProposalDisposition.APPROVED


def test_b6a_precedence_1_ignores_lower_level_existing_escalation():
    inputs = _make_inputs(resolved_recovery=True, existing_escalation=True)
    assert decide_proposal_disposition(inputs) == ProposalDisposition.APPROVED


def test_b6a_precedence_1_ignores_lower_level_too_few_voters():
    inputs = _make_inputs(resolved_recovery=True, eligible_count=1)
    assert decide_proposal_disposition(inputs) == ProposalDisposition.APPROVED


def test_b6a_precedence_1_ignores_lower_level_gate_closure():
    inputs = _make_inputs(resolved_recovery=True, gate_closed_any_eligible=True)
    assert decide_proposal_disposition(inputs) == ProposalDisposition.APPROVED


def test_b6a_precedence_1_ignores_lower_level_eligible_dissent():
    inputs = _make_inputs(resolved_recovery=True, eligible_dissent=True)
    assert decide_proposal_disposition(inputs) == ProposalDisposition.APPROVED


def test_b6a_precedence_1_ignores_lower_level_all_agree():
    inputs = _make_inputs(resolved_recovery=True, all_required_agree=True, independent_agreement=True)
    assert decide_proposal_disposition(inputs) == ProposalDisposition.APPROVED


def test_b6a_precedence_1_ignores_lower_level_proposer_only():
    inputs = _make_inputs(resolved_recovery=True, proposer_only=True)
    assert decide_proposal_disposition(inputs) == ProposalDisposition.APPROVED


def test_b6a_precedence_2_existing_escalation_wins_over_lower():
    inputs = _make_inputs(
        existing_escalation=True,
        eligible_count=1,
        gate_closed_any_eligible=True,
        eligible_dissent=True,
        all_required_agree=True,
        independent_agreement=True,
        proposer_only=True,
        high_risk=True,
    )
    assert decide_proposal_disposition(inputs) == ProposalDisposition.ESCALATED


def test_b6a_precedence_2_ignores_lower_level_too_few_voters():
    inputs = _make_inputs(existing_escalation=True, eligible_count=1)
    assert decide_proposal_disposition(inputs) == ProposalDisposition.ESCALATED


def test_b6a_precedence_2_ignores_lower_level_gate_closure():
    inputs = _make_inputs(existing_escalation=True, gate_closed_any_eligible=True)
    assert decide_proposal_disposition(inputs) == ProposalDisposition.ESCALATED


def test_b6a_precedence_2_ignores_lower_level_eligible_dissent():
    inputs = _make_inputs(existing_escalation=True, eligible_dissent=True)
    assert decide_proposal_disposition(inputs) == ProposalDisposition.ESCALATED


def test_b6a_precedence_2_ignores_lower_level_all_agree():
    inputs = _make_inputs(existing_escalation=True, all_required_agree=True, independent_agreement=True)
    assert decide_proposal_disposition(inputs) == ProposalDisposition.ESCALATED


def test_b6a_precedence_2_ignores_lower_level_proposer_only():
    inputs = _make_inputs(existing_escalation=True, proposer_only=True)
    assert decide_proposal_disposition(inputs) == ProposalDisposition.ESCALATED


def test_b6a_precedence_3_too_few_voters_wins_over_lower():
    inputs = _make_inputs(
        eligible_count=1,
        gate_closed_any_eligible=True,
        eligible_dissent=True,
        all_required_agree=True,
        independent_agreement=True,
        proposer_only=True,
        high_risk=True,
    )
    assert decide_proposal_disposition(inputs) == ProposalDisposition.ESCALATED


def test_b6a_precedence_3_ignores_lower_level_gate_closure():
    inputs = _make_inputs(eligible_count=1, gate_closed_any_eligible=True)
    assert decide_proposal_disposition(inputs) == ProposalDisposition.ESCALATED


def test_b6a_precedence_3_ignores_lower_level_eligible_dissent():
    inputs = _make_inputs(eligible_count=1, eligible_dissent=True)
    assert decide_proposal_disposition(inputs) == ProposalDisposition.ESCALATED


def test_b6a_precedence_3_ignores_lower_level_all_agree():
    inputs = _make_inputs(eligible_count=1, all_required_agree=True, independent_agreement=True)
    assert decide_proposal_disposition(inputs) == ProposalDisposition.ESCALATED


def test_b6a_precedence_3_ignores_lower_level_proposer_only():
    inputs = _make_inputs(eligible_count=1, proposer_only=True)
    assert decide_proposal_disposition(inputs) == ProposalDisposition.ESCALATED


def test_b6a_precedence_4_gate_closure_wins_over_lower():
    inputs = _make_inputs(
        gate_closed_any_eligible=True,
        eligible_dissent=True,
        all_required_agree=True,
        independent_agreement=True,
        proposer_only=True,
        high_risk=True,
    )
    assert decide_proposal_disposition(inputs) == ProposalDisposition.HOLD


def test_b6a_precedence_4_ignores_lower_level_eligible_dissent():
    inputs = _make_inputs(gate_closed_any_eligible=True, eligible_dissent=True)
    assert decide_proposal_disposition(inputs) == ProposalDisposition.HOLD


def test_b6a_precedence_4_ignores_lower_level_all_agree():
    inputs = _make_inputs(gate_closed_any_eligible=True, all_required_agree=True, independent_agreement=True)
    assert decide_proposal_disposition(inputs) == ProposalDisposition.HOLD


def test_b6a_precedence_4_ignores_lower_level_proposer_only():
    inputs = _make_inputs(gate_closed_any_eligible=True, proposer_only=True)
    assert decide_proposal_disposition(inputs) == ProposalDisposition.HOLD


def test_b6a_precedence_5_eligible_dissent_wins_over_lower():
    inputs = _make_inputs(
        eligible_dissent=True,
        all_required_agree=True,
        independent_agreement=True,
        proposer_only=True,
        high_risk=True,
    )
    assert decide_proposal_disposition(inputs) == ProposalDisposition.REJECTED


def test_b6a_precedence_5_ignores_lower_level_all_agree():
    inputs = _make_inputs(eligible_dissent=True, all_required_agree=True, independent_agreement=True)
    assert decide_proposal_disposition(inputs) == ProposalDisposition.REJECTED


def test_b6a_precedence_5_ignores_lower_level_proposer_only():
    inputs = _make_inputs(eligible_dissent=True, proposer_only=True)
    assert decide_proposal_disposition(inputs) == ProposalDisposition.REJECTED


def test_b6a_precedence_6_all_agree_wins_over_lower_high_risk():
    inputs = _make_inputs(
        all_required_agree=True,
        independent_agreement=True,
        proposer_only=True,
        high_risk=True,
    )
    assert decide_proposal_disposition(inputs) == ProposalDisposition.FINAL_CALL


def test_b6a_precedence_6_all_agree_wins_over_lower_low_risk():
    inputs = _make_inputs(
        all_required_agree=True,
        independent_agreement=True,
        proposer_only=True,
        high_risk=False,
    )
    assert decide_proposal_disposition(inputs) == ProposalDisposition.APPROVED


def test_b6a_precedence_6_ignores_lower_level_proposer_only():
    inputs = _make_inputs(all_required_agree=True, independent_agreement=True, proposer_only=True)
    assert decide_proposal_disposition(inputs) == ProposalDisposition.APPROVED


def test_b6a_precedence_7_proposer_only_escalates():
    inputs = _make_inputs(proposer_only=True)
    assert decide_proposal_disposition(inputs) == ProposalDisposition.ESCALATED


def test_b6a_no_rule_fires_returns_pending_or_hold():
    inputs = _make_inputs()
    assert decide_proposal_disposition(inputs) == ProposalDisposition.HOLD


def test_b6a_route_effect_kind_generic_rejects_invariant():
    with pytest.raises(EffectRoutingError) as exc_info:
        route_effect_kind(RATIFIED_INVARIANT_EFFECT_KIND, "generic")
    
    assert exc_info.value.hold is True


def test_b6a_route_effect_kind_invariant_accepts_invariant():
    # In RED phase, this will raise NotImplementedError. When implemented, it should pass cleanly.
    route_effect_kind(RATIFIED_INVARIANT_EFFECT_KIND, "invariant")


def test_b6a_route_effect_kind_unknown_holds_for_generic():
    with pytest.raises(EffectRoutingError) as exc_info:
        route_effect_kind("unknown.effect", "generic")
    
    assert exc_info.value.hold is True


def test_b6a_route_effect_kind_unknown_holds_for_invariant():
    with pytest.raises(EffectRoutingError) as exc_info:
        route_effect_kind("unknown.effect", "invariant")
    
    assert exc_info.value.hold is True


def test_b6a_build_approval_submission_rejects_hash_mismatch():
    approved_snapshot = {"hash": "abc"}
    effect_intent = {"snapshot_hash": "def"}
    with pytest.raises(ValueError, match="snapshot hash"):
        build_approval_submission(approved_snapshot, effect_intent)


def test_b6a_build_approval_submission_binds_single_object():
    approved_snapshot = {"hash": "abc"}
    effect_intent = {"snapshot_hash": "abc"}
    
    submission = build_approval_submission(approved_snapshot, effect_intent)
    assert isinstance(submission, ApprovalSubmission)


def test_b6a_generic_materializer_claims_generic_kinds():
    from peerhub.governance.proposal_policy import route_effect_kind
    for kind in ("consensus.resolved", "consensus.abandoned", "consensus.noop"):
        route_effect_kind(kind, "generic")


def test_b6a_submission_rejects_missing_hash_on_both_sides():
    from peerhub.governance.proposal_policy import build_approval_submission
    with pytest.raises(ValueError):
        build_approval_submission({}, {})


def test_b6a_submission_binds_state_receipt_and_effect_together():
    from peerhub.governance.proposal_policy import build_approval_submission
    sub = build_approval_submission(
        {"hash": "h1", "round_id": "r1"}, {"snapshot_hash": "h1", "kind": "k"}
    )
    assert sub.state_transition["to"] == "approved"
    assert "r1" in sub.receipt_key and "h1" in sub.receipt_key
    assert sub.effect_outbox_entry["kind"] == "k"
