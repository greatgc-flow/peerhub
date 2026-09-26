"""Candidate binding and tracking (pure logic)."""

from dataclasses import dataclass
from typing import FrozenSet, Sequence, Dict, Set


@dataclass(frozen=True)
class Candidate:
    round_id: str
    expected_revision: int
    target_state_hash: str


@dataclass(frozen=True)
class RetractionOutcome:
    state: str
    reason: str


class AckLedger:
    ALLOWED_INVALIDATE_REASONS = frozenset({
        "correction/new dissent",
        "health-or-expiry failure",
        "authority revocation",
        "eligible-membership change"
    })

    def __init__(self) -> None:
        self._acks: Dict[Candidate, Set[str]] = {}

    def bind_ack(self, candidate: Candidate, participant: str) -> None:
        if candidate not in self._acks:
            self._acks[candidate] = set()
        self._acks[candidate].add(participant)

    def acks_for(self, candidate: Candidate) -> FrozenSet[str]:
        return frozenset(self._acks.get(candidate, set()))

    def invalidate(self, reason: str) -> None:
        if reason not in self.ALLOWED_INVALIDATE_REASONS:
            raise ValueError(f"Unknown invalidate reason: {reason}")
        self._acks.clear()

    def is_complete(self, candidate: Candidate, required_participants: Sequence[str]) -> bool:
        if not required_participants:
            return False
        acks = self.acks_for(candidate)
        return all(p in acks for p in required_participants)


def apply_retraction(ledger: AckLedger, candidate: Candidate, authorized: bool) -> RetractionOutcome:
    if not authorized:
        ledger.invalidate("correction/new dissent")
        return RetractionOutcome(state="voting", reason="unauthorized")
    return RetractionOutcome(state="RevocationRecorded", reason="authorized")
