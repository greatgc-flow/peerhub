import dataclasses
"""TDD RED-state spec writing for B1 of the ratified consensus-engine-replacement design."""
import pytest

from peerhub.governance.consensus import (
    ConsensusStateMachine,
    EvalContext,
    VoteEvent,
    CorrectionEvent,
    TimeoutEvent,
    EscalationEvent,
    QuorumMetEvent,
    AckNackEvent,
    RetractionEvent,
    ArbiterAttachmentEvent,
    ResolutionEvent,
    ExceptionalResolutionEvent,
    AbandonEvent,
)
from peerhub.dispatch.orchestrator import InvalidMutationError


@pytest.fixture
def base_context():
    return EvalContext(
        current_timestamp=1000.0,
        caller_identity="actor_1",
        frozen_authority_set=frozenset(["actor_1", "actor_2", "actor_3"]),
    )


# 1. Vote/Dissent
def test_vote_dissent_agree_valid(base_context):
    sm = ConsensusStateMachine(state="proposed")
    event = VoteEvent(actor="actor_1", choice="agree", credential="valid_proof")
    result = sm.evaluate(base_context, event)
    assert result.new_phase == "proposed"
    assert result.dissent_obligation_added is False

def test_vote_dissent_block_creates_obligation(base_context):
    sm = ConsensusStateMachine(state="voting")
    event = VoteEvent(actor="actor_1", choice="block")
    result = sm.evaluate(base_context, event)
    assert result.new_phase == "voting"
    assert result.dissent_obligation_added is True

def test_vote_dissent_need_more_info_creates_obligation(base_context):
    sm = ConsensusStateMachine(state="voting")
    event = VoteEvent(actor="actor_1", choice="need_more_info")
    result = sm.evaluate(base_context, event)
    assert result.new_phase == "voting"
    assert result.dissent_obligation_added is True

def test_vote_dissent_ordinary_disagree_does_not_block(base_context):
    sm = ConsensusStateMachine(state="voting")
    event = VoteEvent(actor="actor_1", choice="disagree")
    result = sm.evaluate(base_context, event)
    assert result.new_phase == "voting"
    assert result.dissent_obligation_added is False

# Credential/proof verification is the AuthorizationGate's job (see
# test_consensus_authorization.py): the pure core never inspects credential strings.

def test_vote_dissent_invalid_phase(base_context):
    sm = ConsensusStateMachine(state="quorum_reached")
    event = VoteEvent(actor="actor_1", choice="agree")
    with pytest.raises(InvalidMutationError):
        sm.evaluate(base_context, event)


# 2. Correction
def test_correction_valid_phase(base_context):
    sm = ConsensusStateMachine(state="final_call")
    event = CorrectionEvent(actor="actor_1", choice="block")
    result = sm.evaluate(base_context, event)
    assert result.new_phase == "voting"
    assert result.candidate_invalidated is True
    assert result.acks_dropped is True
    assert result.fresh_votes_required is True

def test_correction_invalid_phase(base_context):
    sm = ConsensusStateMachine(state="voting")
    event = CorrectionEvent(actor="actor_1", choice="block")
    with pytest.raises(InvalidMutationError):
        sm.evaluate(base_context, event)


# 3. Timeout
def test_timeout_valid_phase_voting(base_context):
    sm = ConsensusStateMachine(state="voting")
    event = TimeoutEvent(requester="system:timeout", deadline=2000.0)
    result = sm.evaluate(base_context, event)
    assert result.new_phase == "voting"
    assert result.evidence_recorded is True
    assert result.policy_reevaluation_triggered is True

def test_timeout_valid_phase_final_call(base_context):
    sm = ConsensusStateMachine(state="final_call")
    event = TimeoutEvent(requester="system:timeout", deadline=2000.0)
    result = sm.evaluate(base_context, event)
    assert result.new_phase == "final_call"
    assert result.evidence_recorded is True
    assert result.policy_reevaluation_triggered is True

def test_timeout_invalid_phase(base_context):
    sm = ConsensusStateMachine(state="resolved")
    event = TimeoutEvent(requester="system:timeout", deadline=2000.0)
    with pytest.raises(InvalidMutationError):
        sm.evaluate(base_context, event)


# 4. Escalation/Reopen
def test_escalation_valid_phase(base_context):
    sm = ConsensusStateMachine(state="voting")
    event = EscalationEvent(source_phases=["voting"], replacement_deadline=3000.0)
    result = sm.evaluate(base_context, event)
    assert result.new_phase == "voting"
    assert result.replacement_deadline_recorded is True
    assert result.policy_reevaluation_triggered is True

def test_escalation_invalid_phase(base_context):
    sm = ConsensusStateMachine(state="approved")
    event = EscalationEvent(source_phases=["voting"], replacement_deadline=3000.0)
    with pytest.raises(InvalidMutationError):
        sm.evaluate(base_context, event)


# 5. Quorum Met
def test_quorum_met_normal(base_context):
    sm = ConsensusStateMachine(state="voting", mandatory_floors_hit=False)
    event = QuorumMetEvent(agreement_count=3)
    result = sm.evaluate(base_context, event)
    assert result.new_phase == "quorum_reached"

def test_quorum_met_mandatory_final_call(base_context):
    sm = ConsensusStateMachine(state="voting", mandatory_floors_hit=True)
    event = QuorumMetEvent(agreement_count=3)
    result = sm.evaluate(base_context, event)
    assert result.new_phase == "final_call"

def test_quorum_met_invalid_phase(base_context):
    sm = ConsensusStateMachine(state="proposed")
    event = QuorumMetEvent(agreement_count=1)
    with pytest.raises(InvalidMutationError):
        sm.evaluate(base_context, event)


# 6. ACK/NACK
def test_ack_completing(base_context):
    sm = ConsensusStateMachine(state="final_call")
    event = AckNackEvent(candidate_id="c1", actor="actor_1", proof="valid", nack_type=None)
    # ACK completes only when all required participants are acked (W1).
    ctx = dataclasses.replace(base_context, required_participants=frozenset(["actor_1"]))
    result = sm.evaluate(ctx, event)
    assert result.new_phase == "approved"

def test_nack_qualifying_concern_holds_barrier(base_context):
    sm = ConsensusStateMachine(state="final_call")
    event = AckNackEvent(candidate_id="c1", actor="actor_1", proof="valid", nack_type="block")
    result = sm.evaluate(base_context, event)
    assert result.new_phase == "final_call"
    assert result.barrier_held is True

def test_nack_cosmetic_logs_only(base_context):
    sm = ConsensusStateMachine(state="final_call")
    event = AckNackEvent(candidate_id="c1", actor="actor_1", proof="valid", nack_type="cosmetic")
    result = sm.evaluate(base_context, event)
    assert result.new_phase == "final_call"
    assert result.cosmetic_logged is True

def test_nack_terminal_rejection(base_context):
    sm = ConsensusStateMachine(state="final_call")
    event = AckNackEvent(candidate_id="c1", actor="actor_1", proof="valid", nack_type="terminal_rejection")
    result = sm.evaluate(base_context, event)
    assert result.new_phase == "rejected"
    assert result.terminal_rejection is True

def test_ack_invalid_phase(base_context):
    sm = ConsensusStateMachine(state="voting")
    event = AckNackEvent(candidate_id="c1", actor="actor_1", proof="valid", nack_type=None)
    with pytest.raises(InvalidMutationError):
        sm.evaluate(base_context, event)


# 7. Retraction
def test_retraction_valid_phase(base_context):
    sm = ConsensusStateMachine(state="final_call")
    event = RetractionEvent(candidate_id="c1", actor="actor_1", proof="valid")
    result = sm.evaluate(base_context, event)
    assert result.new_phase == "voting"
    assert result.candidate_invalidated is True
    assert result.acks_dropped is True

def test_retraction_invalid_phase(base_context):
    sm = ConsensusStateMachine(state="voting")
    event = RetractionEvent(candidate_id="c1", actor="actor_1", proof="valid")
    with pytest.raises(InvalidMutationError):
        sm.evaluate(base_context, event)


# 8. Arbiter Attachment
def test_arbiter_attachment_valid_phase(base_context):
    sm = ConsensusStateMachine(state="approved")
    event = ArbiterAttachmentEvent(verdict="APPROVE", profile="tier-1", dispatch="SUCCEEDED")
    result = sm.evaluate(base_context, event)
    assert result.new_phase == "approved"
    assert result.evidence_recorded is True

def test_arbiter_attachment_invalid_phase(base_context):
    sm = ConsensusStateMachine(state="voting")
    event = ArbiterAttachmentEvent(verdict="APPROVE", profile="tier-1", dispatch="SUCCEEDED")
    with pytest.raises(InvalidMutationError):
        sm.evaluate(base_context, event)

def test_arbiter_attachment_invalid_dispatch(base_context):
    sm = ConsensusStateMachine(state="approved")
    event = ArbiterAttachmentEvent(verdict="APPROVE", profile="tier-1", dispatch="FAILED")
    with pytest.raises(InvalidMutationError):
        sm.evaluate(base_context, event)


# 9. Resolution
def test_resolution_valid_phase_quorum_reached(base_context):
    sm = ConsensusStateMachine(state="quorum_reached")
    event = ResolutionEvent(outcome="approved")
    result = sm.evaluate(base_context, event)
    assert result.new_phase == "approved"

def test_resolution_valid_phase_escalated(base_context):
    sm = ConsensusStateMachine(state="escalated")
    event = ResolutionEvent(outcome="rejected")
    result = sm.evaluate(base_context, event)
    assert result.new_phase == "rejected"

def test_resolution_invalid_phase(base_context):
    sm = ConsensusStateMachine(state="voting")
    event = ResolutionEvent(outcome="approved")
    with pytest.raises(InvalidMutationError):
        sm.evaluate(base_context, event)


# 10. Exceptional Resolution
def test_exceptional_resolution_valid_phase(base_context):
    sm = ConsensusStateMachine(state="voting")
    event = ExceptionalResolutionEvent(admin_proof="admin_token", bypass_reason="emergency")
    result = sm.evaluate(base_context, event)
    assert result.new_phase == "approved"

def test_exceptional_resolution_invalid_phase(base_context):
    sm = ConsensusStateMachine(state="approved")
    event = ExceptionalResolutionEvent(admin_proof="admin_token", bypass_reason="emergency")
    with pytest.raises(InvalidMutationError):
        sm.evaluate(base_context, event)


# 11. Abandon
def test_abandon_valid_phase_voting(base_context):
    sm = ConsensusStateMachine(state="voting")
    event = AbandonEvent(reason="obsolete", requesting_actor="actor_1")
    result = sm.evaluate(base_context, event)
    assert result.new_phase == "abandoned"

def test_abandon_valid_phase_final_call(base_context):
    sm = ConsensusStateMachine(state="final_call")
    event = AbandonEvent(reason="obsolete", requesting_actor="actor_1")
    result = sm.evaluate(base_context, event)
    assert result.new_phase == "abandoned"

def test_abandon_invalid_phase(base_context):
    sm = ConsensusStateMachine(state="approved")
    event = AbandonEvent(reason="obsolete", requesting_actor="actor_1")
    with pytest.raises(InvalidMutationError):
        sm.evaluate(base_context, event)
