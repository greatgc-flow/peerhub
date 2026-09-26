import math
from peerhub.dispatch.policy import DispatchPolicy, ConsultationDepth
from peerhub.core.errors import InvalidMutationError

class DispatchOrchestrator:
    def __init__(self):
        pass

    def evaluate(self, policy: DispatchPolicy, action_name: str, risk: str, depth: ConsultationDepth | None = None, min_depth: ConsultationDepth | None = None) -> str:
        ranks = {
            ConsultationDepth.NONE: 0,
            ConsultationDepth.NOTIFY: 1,
            ConsultationDepth.REVIEW: 2,
            ConsultationDepth.QUORUM: 3,
            ConsultationDepth.UNANIMOUS: 4,
        }
        
        effective_depth = depth
        if min_depth is not None and ranks.get(min_depth, -1) > (ranks.get(depth, -1) if depth is not None else -1):
            effective_depth = min_depth

        if effective_depth == ConsultationDepth.NONE:
            return "direct_dispatch"
        else:
            return "create_round"

    def process_notification(self, reachable_peers: int, read_peers: int) -> str:
        if read_peers == reachable_peers and reachable_peers > 0:
            return "delivered"
        elif reachable_peers == 0:
            return "undelivered"
        else:
            return "partially-delivered"

    def process_review_round(self, acks: int = 0, nacks: int = 0, timed_out: bool = False, reviewer_is_proposer: bool = False, eligible_reviewers: int = 1) -> str:
        if reviewer_is_proposer:
            raise InvalidMutationError("Reviewer cannot be the proposer.")
        if eligible_reviewers == 0:
            return "escalation"
        
        if nacks > 0:
            return "escalation"
        elif acks > 0 or timed_out:
            return "proceed"
            
        return "pending"

    def process_quorum_round(self, votes: dict[str, int], formula: str, total_voters: int, timed_out: bool = False, proposer_voted: bool = False, non_proposer_voted: bool = True, changed_vote: bool = False) -> str:
        if changed_vote:
            raise InvalidMutationError("Vote cannot be changed at this layer.")
        
        if proposer_voted and not non_proposer_voted:
            return "blocked_non_proposer_required"
            
        agree_count = votes.get("agree", 0)
        
        quorum_reached = False
        if formula == "majority":
            quorum_reached = agree_count > total_voters / 2
        elif formula == "supermajority":
            quorum_reached = agree_count >= math.ceil(2 * total_voters / 3)
        elif formula == "all_required":
            quorum_reached = agree_count == total_voters
        elif formula == "unanimous":
            # total_voters always excludes the proposer; the proposer's own
            # agreement is unconditionally required (design correction B3,
            # docs/design/CONSENSUS-REPLACEMENT-R1-ag-deepthink-2026-09-25.md
            # section 3.1.1 -- abstaining/unreachable/timed-out proposers must
            # never silently lower the requirement).
            required_agrees = max(total_voters + 1, 2)
            quorum_reached = agree_count == required_agrees
        
        if quorum_reached:
            return "check_final_call"
        
        if timed_out and not quorum_reached:
            return "escalation"
            
        return "pending"
