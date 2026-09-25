"""TDD cases for DispatchOrchestrator Consultation (CD-* cases)."""
import pytest

from peerhub.dispatch.policy import ConsultationDepth
from peerhub.dispatch.orchestrator import DispatchOrchestrator, InvalidMutationError

# We assume some mocked or unimplemented data structures for the test
# Since this is TDD, these imports will fail.

def test_cd_n_01_depth_none():
    """CD-N-01: Simple dispatch with depth=NONE -> Direct dispatch."""
    orch = DispatchOrchestrator()
    # Mock policy with depth=NONE
    result = orch.evaluate(policy=None, action_name="test", risk="low", depth=ConsultationDepth.NONE)
    assert result == "direct_dispatch"

def test_cd_n_02_depth_none_overridden():
    """CD-N-02: depth=NONE overridden by per-command minimum to QUORUM -> Round created."""
    orch = DispatchOrchestrator()
    result = orch.evaluate(policy=None, action_name="consensus.propose", risk="low", depth=ConsultationDepth.NONE, min_depth=ConsultationDepth.QUORUM)
    assert result == "create_round"

def test_cd_nf_01_notify_delivered():
    """CD-NF-01: Notification target created, all peers read it -> delivered."""
    orch = DispatchOrchestrator()
    result = orch.process_notification(reachable_peers=2, read_peers=2)
    assert result == "delivered"

def test_cd_nf_02_notify_partially_delivered():
    """CD-NF-02: Notification target created, some peers unreachable -> partially-delivered."""
    orch = DispatchOrchestrator()
    result = orch.process_notification(reachable_peers=2, read_peers=1)
    assert result == "partially-delivered"

def test_cd_nf_03_notify_undelivered():
    """CD-NF-03: Notification target created, zero peers reachable -> undelivered (dispatch proceeds)."""
    orch = DispatchOrchestrator()
    result = orch.process_notification(reachable_peers=0, read_peers=0)
    assert result == "undelivered"

def test_cd_r_01_review_ack():
    """CD-R-01: Single reviewer ACKs within timeout -> Dispatch proceeds."""
    orch = DispatchOrchestrator()
    result = orch.process_review_round(acks=1, nacks=0, timed_out=False)
    assert result == "proceed"

def test_cd_r_02_review_nack():
    """CD-R-02: Single reviewer NACKs -> Dispatch blocked; escalation."""
    orch = DispatchOrchestrator()
    result = orch.process_review_round(acks=0, nacks=1, timed_out=False)
    assert result == "escalation"

def test_cd_r_03_review_timeout():
    """CD-R-03: Reviewer doesn't respond before timeout -> Dispatch proceeds."""
    orch = DispatchOrchestrator()
    result = orch.process_review_round(acks=0, nacks=0, timed_out=True)
    assert result == "proceed"

def test_cd_r_04_review_self():
    """CD-R-04: Reviewer is the same as the proposer -> InvalidMutationError."""
    orch = DispatchOrchestrator()
    with pytest.raises(InvalidMutationError):
        orch.process_review_round(acks=1, nacks=0, timed_out=False, reviewer_is_proposer=True)

def test_cd_r_05_review_no_eligible():
    """CD-R-05: No eligible reviewers (all quarantined) -> Escalation immediately."""
    orch = DispatchOrchestrator()
    result = orch.process_review_round(eligible_reviewers=0)
    assert result == "escalation"

def test_cd_q_01_quorum_all_agree():
    """CD-Q-01: All required agree, no disagree -> Quorum reached, check final_call."""
    orch = DispatchOrchestrator()
    result = orch.process_quorum_round(votes={"agree": 3, "disagree": 0}, formula="all_required", total_voters=3)
    assert result == "check_final_call"

def test_cd_q_02_quorum_majority_abstains():
    """CD-Q-02: Majority agrees, minority abstains -> Quorum reached if formula satisfied."""
    orch = DispatchOrchestrator()
    result = orch.process_quorum_round(votes={"agree": 2, "abstain": 1}, formula="majority", total_voters=3)
    assert result == "check_final_call"

def test_cd_q_03_quorum_ordinary_dissent():
    """CD-Q-03: Majority agrees, one disagrees (ordinary) -> Approved."""
    orch = DispatchOrchestrator()
    result = orch.process_quorum_round(votes={"agree": 2, "disagree": 1}, formula="majority", total_voters=3)
    assert result == "check_final_call"

def test_cd_q_04_quorum_timeout():
    """CD-Q-04: Timeout with insufficient votes -> Escalation."""
    orch = DispatchOrchestrator()
    result = orch.process_quorum_round(votes={"agree": 1}, formula="majority", total_voters=3, timed_out=True)
    assert result == "escalation"

def test_cd_q_05_quorum_silent():
    """CD-Q-05: Exactly quorum_required agree, rest silent -> Quorum reached."""
    orch = DispatchOrchestrator()
    result = orch.process_quorum_round(votes={"agree": 2}, formula="majority", total_voters=3)
    assert result == "check_final_call"

def test_cd_q_06_quorum_majority_3_voters():
    """CD-Q-06: quorum_formula=majority, 3 voters, 2 agree -> Quorum reached."""
    orch = DispatchOrchestrator()
    result = orch.process_quorum_round(votes={"agree": 2}, formula="majority", total_voters=3)
    assert result == "check_final_call"

def test_cd_q_07_quorum_majority_4_voters():
    """CD-Q-07: quorum_formula=majority, 4 voters, 2 agree -> Quorum NOT reached."""
    orch = DispatchOrchestrator()
    result = orch.process_quorum_round(votes={"agree": 2}, formula="majority", total_voters=4)
    assert result == "pending"

def test_cd_q_08_quorum_supermajority_3_voters():
    """CD-Q-08: quorum_formula=supermajority, 3 voters, 2 agree -> Quorum reached."""
    orch = DispatchOrchestrator()
    result = orch.process_quorum_round(votes={"agree": 2}, formula="supermajority", total_voters=3)
    assert result == "check_final_call"

def test_cd_q_09_quorum_supermajority_6_voters():
    """CD-Q-09: quorum_formula=supermajority, 6 voters, 3 agree -> Quorum NOT reached."""
    orch = DispatchOrchestrator()
    result = orch.process_quorum_round(votes={"agree": 3}, formula="supermajority", total_voters=6)
    assert result == "pending"

def test_cd_q_10_quorum_proposer_votes():
    """CD-Q-10: Proposer votes (not the sole voter) -> Vote counted normally."""
    orch = DispatchOrchestrator()
    result = orch.process_quorum_round(votes={"agree": 2}, proposer_voted=True, total_voters=3, formula="majority")
    assert result == "check_final_call"

def test_cd_q_11_quorum_only_proposer():
    """CD-Q-11: Only proposer votes, no non-proposer -> Blocked."""
    orch = DispatchOrchestrator()
    result = orch.process_quorum_round(votes={"agree": 1}, proposer_voted=True, non_proposer_voted=False, formula="all_required", total_voters=1)
    assert result == "blocked_non_proposer_required"

def test_cd_u_01_unanimous_all_agree():
    """CD-U-01: All required explicitly agree (including the proposer) -> Quorum reached."""
    orch = DispatchOrchestrator()
    result = orch.process_quorum_round(votes={"agree": 3}, formula="unanimous", total_voters=2, proposer_voted=True)
    assert result == "check_final_call"

def test_cd_u_02_unanimous_abstain():
    """CD-U-02: Proposer abstains, both other required voters agree -> Quorum NOT reached (B3 correction: proposer's own agreement is unconditionally required)."""
    orch = DispatchOrchestrator()
    result = orch.process_quorum_round(votes={"agree": 2}, formula="unanimous", total_voters=2, proposer_voted=False)
    assert result == "pending"

def test_cd_u_03_unanimous_unreachable():
    """CD-U-03: Proposer unreachable, other required voters agree -> Blocked until timeout, then escalation."""
    orch = DispatchOrchestrator()
    result = orch.process_quorum_round(votes={"agree": 2}, formula="unanimous", total_voters=2, proposer_voted=False, timed_out=True)
    assert result == "escalation"

def test_cd_u_04_unanimous_disagree_after_agree():
    """CD-U-04: All agree but one then disagrees after initial agree -> Vote immutable, reopens."""
    orch = DispatchOrchestrator()
    with pytest.raises(InvalidMutationError):
        orch.process_quorum_round(votes={"agree": 3}, formula="unanimous", total_voters=2, proposer_voted=True, changed_vote=True)

def test_cd_u_05_unanimous_1_person():
    """CD-U-05: Required voter set is 1 person (+ proposer) -> Both must agree (min floor 2)."""
    orch = DispatchOrchestrator()
    result = orch.process_quorum_round(votes={"agree": 1}, proposer_voted=True, non_proposer_voted=True, formula="unanimous", total_voters=1)
    # The math means 2 total votes needed because floor=2. Wait, total_voters=2 effectively.
    assert result == "pending" # Because we only provided 1 agree in this test call context, wait, we passed 'agree': 1.
