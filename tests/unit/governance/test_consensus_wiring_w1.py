import pytest
from peerhub.governance.consensus import (
    ConsensusStateMachine,
    EvalContext,
    AckNackEvent,
    ArbiterAttachmentEvent,
    ExceptionalResolutionEvent,
)
from peerhub.core.errors import InvalidMutationError

@pytest.fixture
def base_context():
    return EvalContext(
        current_timestamp=1000.0,
        caller_identity="actor_1",
        frozen_authority_set=frozenset(["actor_1", "actor_2", "actor_3"]),
    )

def test_ack_incomplete_stays_in_final_call():
    sm = ConsensusStateMachine(state="final_call")
    # Context requires actor_1 and actor_2 to ack. Currently no one has acked.
    ctx = EvalContext(
        current_timestamp=1000.0,
        caller_identity="actor_1",
        frozen_authority_set=frozenset(["actor_1", "actor_2", "actor_3"]),
        required_participants=frozenset(["actor_1", "actor_2"]),
        bound_ack_participants=frozenset()
    )
    event = AckNackEvent(candidate_id="c1", actor="actor_1", proof="valid", nack_type=None)
    result = sm.evaluate(ctx, event)
    
    assert result.new_phase == "final_call"
    assert result.ack_recorded is True

def test_ack_completing_transitions_to_approved():
    sm = ConsensusStateMachine(state="final_call")
    # actor_2 has already acked, actor_1 is acking now.
    ctx = EvalContext(
        current_timestamp=1000.0,
        caller_identity="actor_1",
        frozen_authority_set=frozenset(["actor_1", "actor_2", "actor_3"]),
        required_participants=frozenset(["actor_1", "actor_2"]),
        bound_ack_participants=frozenset(["actor_2"])
    )
    event = AckNackEvent(candidate_id="c1", actor="actor_1", proof="valid", nack_type=None)
    result = sm.evaluate(ctx, event)
    
    assert result.new_phase == "approved"

def test_arbiter_attachment_rejects_second_attachment(base_context):
    sm = ConsensusStateMachine(state="approved")
    ctx = EvalContext(
        current_timestamp=1000.0,
        caller_identity="actor_1",
        frozen_authority_set=frozenset(["actor_1", "actor_2", "actor_3"]),
        arbiter_attachment_recorded=True
    )
    event = ArbiterAttachmentEvent(verdict="APPROVE", profile="tier-1", dispatch="SUCCEEDED")
    with pytest.raises(InvalidMutationError):
        sm.evaluate(ctx, event)

def test_exceptional_resolution_explicit_outcome_rejected(base_context):
    sm = ConsensusStateMachine(state="voting")
    event = ExceptionalResolutionEvent(admin_proof="valid_token", bypass_reason="emergency", outcome="rejected")
    result = sm.evaluate(base_context, event)
    assert result.new_phase == "rejected"


def test_exceptional_resolution_invalid_outcome_raises(base_context):
    sm = ConsensusStateMachine(state="voting")
    event = ExceptionalResolutionEvent(admin_proof="valid_token", bypass_reason="emergency", outcome="maybe")
    with pytest.raises(InvalidMutationError):
        sm.evaluate(base_context, event)


def test_ack_with_empty_required_set_fails_closed():
    sm = ConsensusStateMachine(state="final_call")
    ctx = EvalContext(
        current_timestamp=1000.0,
        caller_identity="actor_1",
        frozen_authority_set=frozenset(["actor_1"]),
    )
    event = AckNackEvent(candidate_id="c1", actor="actor_1", proof="valid", nack_type=None)
    result = sm.evaluate(ctx, event)
    assert result.new_phase == "final_call"


from peerhub.governance.consensus import ResolutionEvent


@pytest.mark.parametrize("phase", ["proposed", "voting", "quorum_reached", "final_call", "escalated"])
def test_dissent_basis_rejects_an_open_round_from_any_open_phase(phase, base_context):
    result = ConsensusStateMachine(state=phase).evaluate(
        base_context, ResolutionEvent(outcome="rejected", basis="eligible_dissent")
    )
    assert result.new_phase == "rejected"


@pytest.mark.parametrize("phase", ["voting", "proposed", "final_call"])
def test_plain_resolution_is_still_refused_from_voting_phases(phase, base_context):
    with pytest.raises(InvalidMutationError):
        ConsensusStateMachine(state=phase).evaluate(base_context, ResolutionEvent(outcome="rejected"))


def test_dissent_basis_cannot_approve(base_context):
    with pytest.raises(InvalidMutationError):
        ConsensusStateMachine(state="voting").evaluate(
            base_context, ResolutionEvent(outcome="approved", basis="eligible_dissent")
        )


@pytest.mark.parametrize("phase", ["approved", "rejected", "abandoned", "resolved"])
def test_dissent_basis_is_refused_from_terminal_phases(phase, base_context):
    with pytest.raises(InvalidMutationError):
        ConsensusStateMachine(state=phase).evaluate(
            base_context, ResolutionEvent(outcome="rejected", basis="eligible_dissent")
        )
