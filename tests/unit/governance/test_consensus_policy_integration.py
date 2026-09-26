"""TDD cases for New Consensus SM-* edge cases from GOVERNANCE-DISPATCH-R4."""
import pytest

# These imports will fail because the extended state machine logic is not implemented yet.
from peerhub.governance.consensus import ConsensusStateMachine, StaleRevisionError
from peerhub.dispatch.orchestrator import InvalidMutationError

def test_sm_13_final_call_always():
    """SM-13: quorum_reached -> final_call_rule="always" -> final_call."""
    sm = ConsensusStateMachine(state="quorum_reached", final_call_rule="always")
    next_state = sm.evaluate_final_call_transition()
    assert next_state == "final_call"

def test_sm_14_final_call_never_no_floors():
    """SM-14: quorum_reached -> final_call_rule="never" + no mandatory floors -> resolved."""
    sm = ConsensusStateMachine(state="quorum_reached", final_call_rule="never", mandatory_floors_hit=False)
    next_state = sm.evaluate_final_call_transition()
    assert next_state == "resolved"

def test_sm_15_final_call_never_with_floors():
    """SM-15: quorum_reached -> final_call_rule="never" + mandatory floor hit -> final_call."""
    sm = ConsensusStateMachine(state="quorum_reached", final_call_rule="never", mandatory_floors_hit=True)
    next_state = sm.evaluate_final_call_transition()
    assert next_state == "final_call"

def test_sm_16_final_call_tier0_with_floors():
    """SM-16: quorum_reached -> final_call_rule="tier_0_or_high_risk_or_dissent" + mandatory floor hit -> final_call."""
    sm = ConsensusStateMachine(state="quorum_reached", final_call_rule="tier_0_or_high_risk_or_dissent", mandatory_floors_hit=True)
    next_state = sm.evaluate_final_call_transition()
    assert next_state == "final_call"

def test_sm_17_final_call_tier0_no_floors():
    """SM-17: quorum_reached -> final_call_rule="tier_0_or_high_risk_or_dissent" + no mandatory floors hit -> resolved."""
    sm = ConsensusStateMachine(state="quorum_reached", final_call_rule="tier_0_or_high_risk_or_dissent", mandatory_floors_hit=False)
    next_state = sm.evaluate_final_call_transition()
    assert next_state == "resolved"

def test_sm_19_final_call_last_ack_commits():
    """SM-19: final_call -> Last ACK commits with live required authority, valid candidate, no concerns -> resolved."""
    sm = ConsensusStateMachine(state="final_call")
    outcome = sm.process_ack(is_last=True, live_authority=True, valid_candidate=True, has_concerns=False)
    assert sm.state == "resolved"
    assert outcome == "approved"

def test_sm_20_final_call_nack_with_concern():
    """SM-20: final_call -> NACK with valid qualifying concern -> final_call."""
    sm = ConsensusStateMachine(state="final_call")
    sm.process_nack(qualifying_concern=True)
    assert sm.state == "final_call"

def test_sm_21_final_call_timeout():
    """SM-21: final_call -> Timeout -> forced_escalation."""
    sm = ConsensusStateMachine(state="final_call")
    sm.process_timeout()
    assert sm.state == "forced_escalation"

def test_sm_22_final_call_ack_from_non_eligible():
    """SM-22: final_call -> ACK from non-eligible actor -> InvalidMutationError."""
    sm = ConsensusStateMachine(state="final_call")
    with pytest.raises(InvalidMutationError):
        sm.process_ack(eligible=False)

def test_sm_23_timeout_escalation_accepted():
    """SM-23: timeout -> Escalation accepted -> resolved."""
    sm = ConsensusStateMachine(state="timeout")
    outcome = sm.process_escalation_decision(decision="accepted")
    assert sm.state == "resolved"
    assert outcome == "approved"

def test_sm_24_timeout_escalation_reopened():
    """SM-24: timeout -> Escalation -> round reopened -> voting."""
    sm = ConsensusStateMachine(state="timeout")
    sm.process_escalation_decision(decision="reopened")
    assert sm.state == "voting"
    assert sm.deadline_extended is True

def test_sm_25_forced_escalation_resolves():
    """SM-25: forced_escalation -> Human/Arbiter resolves -> resolved."""
    sm = ConsensusStateMachine(state="forced_escalation")
    outcome = sm.process_escalation_decision(decision="accepted")
    assert sm.state == "resolved"
    assert outcome == "approved"

def test_sm_26_forced_escalation_reopens():
    """SM-26: forced_escalation -> Human/Arbiter reopens -> voting."""
    sm = ConsensusStateMachine(state="forced_escalation")
    sm.process_escalation_decision(decision="reopened")
    assert sm.state == "voting"
    assert sm.deadline_extended is True

def test_sm_29_final_call_pre_auth_retraction():
    """SM-29: final_call -> Pre-authorization ACK retraction -> final_call."""
    sm = ConsensusStateMachine(state="final_call")
    sm.process_retraction(post_authorization=False)
    assert sm.state == "final_call"

def test_sm_30_resolved_post_auth_retraction():
    """SM-30: resolved -> Post-authorization ACK retraction -> resolved."""
    sm = ConsensusStateMachine(state="resolved")
    sm.process_retraction(post_authorization=True)
    assert sm.state == "resolved"
    assert sm.revocation_record_created is True

# SM-31 (concurrent ACK/retraction) is enforced by the broker CAS + authority precondition:
# see test_consensus_txn_contracts_t1.py and test_consensus_audit_regressions.py.
