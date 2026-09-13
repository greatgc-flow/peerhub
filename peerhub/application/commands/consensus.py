"""Native consensus commands."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, ClassVar

from peerhub.application.commands import Command, SubmissionMetadata
from peerhub.core.protocol import JsonValue


@dataclass(frozen=True, slots=True)
class ConsensusProposeCommand(Command[Any]):
    """Wire command only; Client sends it to the application boundary."""

    method: ClassVar[str] = "consensus.round.propose"
    submission: SubmissionMetadata
    round_id: str
    title: str
    question: str
    body: str
    proposer_id: str
    required_participants: tuple[str, ...]
    eligible_participants: tuple[str, ...]
    risk: str
    source_hash: str

    def encode_params(self) -> Mapping[str, JsonValue]:
        return {
            "round_id": self.round_id,
            "title": self.title,
            "question": self.question,
            "body": self.body,
            "proposer_id": self.proposer_id,
            "required_participants": self.required_participants,
            "eligible_participants": self.eligible_participants,
            "risk": self.risk,
            "source_hash": self.source_hash,
        }

    @classmethod
    def decode_result(cls, value: Mapping[str, JsonValue]) -> Any:
        return value


@dataclass(frozen=True, slots=True)
class ConsensusVoteCommand(Command[Any]):
    method: ClassVar[str] = "consensus.vote.cast"
    submission: SubmissionMetadata
    round_id: str
    actor_id: str
    choice: str

    def encode_params(self) -> Mapping[str, JsonValue]:
        return {
            "round_id": self.round_id,
            "actor_id": self.actor_id,
            "choice": self.choice,
        }

    @classmethod
    def decode_result(cls, value: Mapping[str, JsonValue]) -> Any:
        return value


@dataclass(frozen=True, slots=True)
class ConsensusCheckCommand(Command[Any]):
    method: ClassVar[str] = "consensus.round.read"
    submission: SubmissionMetadata
    round_id: str

    def encode_params(self) -> Mapping[str, JsonValue]:
        return {"round_id": self.round_id}

    @classmethod
    def decode_result(cls, value: Mapping[str, JsonValue]) -> Any:
        return value


@dataclass(frozen=True, slots=True)
class ConsensusSweepCommand(Command[Any]):
    method: ClassVar[str] = "consensus.round.sweep"
    submission: SubmissionMetadata
    round_id: str
    reason: str
    expected_revision: int | None

    def encode_params(self) -> Mapping[str, JsonValue]:
        return {
            "round_id": self.round_id,
            "reason": self.reason,
            "expected_revision": self.expected_revision,
        }

    @classmethod
    def decode_result(cls, value: Mapping[str, JsonValue]) -> Any:
        return value
