"""Candidate binding and tracking (pure logic)."""

from dataclasses import dataclass
from typing import FrozenSet, Sequence


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
    def __init__(self) -> None:
        raise NotImplementedError("RED phase")

    def bind_ack(self, candidate: Candidate, participant: str) -> None:
        raise NotImplementedError("RED phase")

    def acks_for(self, candidate: Candidate) -> FrozenSet[str]:
        raise NotImplementedError("RED phase")

    def invalidate(self, reason: str) -> None:
        raise NotImplementedError("RED phase")

    def is_complete(self, candidate: Candidate, required_participants: Sequence[str]) -> bool:
        raise NotImplementedError("RED phase")


def apply_retraction(ledger: AckLedger, candidate: Candidate, authorized: bool) -> RetractionOutcome:
    raise NotImplementedError("RED phase")
