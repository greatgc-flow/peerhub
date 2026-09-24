from peerhub.dispatch.policy import DispatchPolicy

class InvalidMutationError(Exception):
    pass

class DispatchOrchestrator:
    def __init__(self):
        pass

    def evaluate(self, policy: DispatchPolicy, action_name: str, risk: str, depth=None, min_depth=None) -> str:
        raise NotImplementedError("TDD RED state")

    def process_notification(self, reachable_peers: int, read_peers: int) -> str:
        raise NotImplementedError("TDD RED state")

    def process_review_round(self, acks: int = 0, nacks: int = 0, timed_out: bool = False, reviewer_is_proposer: bool = False, eligible_reviewers: int = 1) -> str:
        raise NotImplementedError("TDD RED state")

    def process_quorum_round(self, votes: dict, formula: str, total_voters: int, timed_out: bool = False, proposer_voted: bool = False, non_proposer_voted: bool = True, changed_vote: bool = False) -> str:
        raise NotImplementedError("TDD RED state")
