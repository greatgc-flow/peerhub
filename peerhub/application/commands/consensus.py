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
    verified_required: bool = False

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
            "verified_required": self.verified_required,
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
    credential_id: str | None = None
    expected_revision: int | None = None
    """Not encoded into wire params -- carried on CommandEnvelope.credential_id
    instead (R4/P4b gateway verification), matching how the gateway's
    GovernanceAuthorizer reads it. decode_vote reads it back from the
    envelope, not from params, for the same reason."""

    def encode_params(self) -> Mapping[str, JsonValue]:
        params: dict[str, JsonValue] = {
            "round_id": self.round_id,
            "actor_id": self.actor_id,
            "choice": self.choice,
        }
        if self.expected_revision is not None:  # wire shape unchanged unless supplied
            params["expected_revision"] = self.expected_revision
        return params

    @classmethod
    def decode_result(cls, value: Mapping[str, JsonValue]) -> Any:
        return value


@dataclass(frozen=True, slots=True)
class ConsensusAckCommand(Command[Any]):
    """ACK the current Final Call candidate."""

    method: ClassVar[str] = "consensus.final_call.ack"
    submission: SubmissionMetadata
    round_id: str
    actor_id: str
    expected_revision: int | None = None
    credential_id: str | None = None

    def encode_params(self) -> Mapping[str, JsonValue]:
        return {
            "round_id": self.round_id,
            "actor_id": self.actor_id,
            "expected_revision": self.expected_revision,
        }

    @classmethod
    def decode_result(cls, value: Mapping[str, JsonValue]) -> Any:
        return value


@dataclass(frozen=True, slots=True)
class ConsensusNackCommand(Command[Any]):
    """NACK the current Final Call candidate (block | cosmetic | terminal_rejection)."""

    method: ClassVar[str] = "consensus.final_call.nack"
    submission: SubmissionMetadata
    round_id: str
    actor_id: str
    nack_type: str = "block"
    expected_revision: int | None = None
    credential_id: str | None = None

    def encode_params(self) -> Mapping[str, JsonValue]:
        return {
            "round_id": self.round_id,
            "actor_id": self.actor_id,
            "nack_type": self.nack_type,
            "expected_revision": self.expected_revision,
        }

    @classmethod
    def decode_result(cls, value: Mapping[str, JsonValue]) -> Any:
        return value


@dataclass(frozen=True, slots=True)
class ConsensusCorrectCommand(Command[Any]):
    """Correct a Final Call candidate: every vote/ACK/concern is voided."""

    method: ClassVar[str] = "consensus.round.correct"
    submission: SubmissionMetadata
    round_id: str
    actor_id: str
    expected_revision: int | None = None
    credential_id: str | None = None

    def encode_params(self) -> Mapping[str, JsonValue]:
        return {
            "round_id": self.round_id,
            "actor_id": self.actor_id,
            "expected_revision": self.expected_revision,
        }

    @classmethod
    def decode_result(cls, value: Mapping[str, JsonValue]) -> Any:
        return value


@dataclass(frozen=True, slots=True)
class ConsensusRetractCommand(Command[Any]):
    """Retract an ACK (pre-authorization) or revoke an approval (post-authorization)."""

    method: ClassVar[str] = "consensus.round.retract"
    submission: SubmissionMetadata
    round_id: str
    actor_id: str
    expected_revision: int | None = None
    credential_id: str | None = None

    def encode_params(self) -> Mapping[str, JsonValue]:
        return {
            "round_id": self.round_id,
            "actor_id": self.actor_id,
            "expected_revision": self.expected_revision,
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


@dataclass(frozen=True, slots=True)
class ProposalAddCommand(Command[Any]):
    method: ClassVar[str] = "governance.proposal.create"
    submission: SubmissionMetadata
    subject: str
    from_peer: str
    impact: str
    rationale: str
    text: str
    verified_required: bool = False

    def encode_params(self) -> Mapping[str, JsonValue]:
        return {
            "subject": self.subject,
            "from_peer": self.from_peer,
            "impact": self.impact,
            "rationale": self.rationale,
            "text": self.text,
            "verified_required": self.verified_required,
        }

    @classmethod
    def decode_result(cls, value: Mapping[str, JsonValue]) -> Any:
        return value


@dataclass(frozen=True, slots=True)
class ProposalVoteCommand(Command[Any]):
    method: ClassVar[str] = "governance.proposal.vote"
    submission: SubmissionMetadata
    proposal_id: str
    voter: str
    vote: str
    reason: str
    credential_id: str | None = None
    """Not encoded into wire params -- carried on CommandEnvelope.credential_id
    instead (R4/P4b gateway verification), matching ConsensusVoteCommand."""

    def encode_params(self) -> Mapping[str, JsonValue]:
        return {
            "proposal_id": self.proposal_id,
            "voter": self.voter,
            "vote": self.vote,
            "reason": self.reason,
        }

    @classmethod
    def decode_result(cls, value: Mapping[str, JsonValue]) -> Any:
        return value


@dataclass(frozen=True, slots=True)
class ProposalListCommand(Command[Any]):
    method: ClassVar[str] = "governance.proposal.list"
    submission: SubmissionMetadata

    def encode_params(self) -> Mapping[str, JsonValue]:
        return {}

    @classmethod
    def decode_result(cls, value: Mapping[str, JsonValue]) -> Any:
        return value


@dataclass(frozen=True, slots=True)
class ArbiterReviewCommand(Command[Any]):
    method: ClassVar[str] = "consensus.arbiter.review"
    submission: SubmissionMetadata
    round_id: str

    def encode_params(self) -> Mapping[str, JsonValue]:
        return {"round_id": self.round_id}

    @classmethod
    def decode_result(cls, value: Mapping[str, JsonValue]) -> Any:
        return value
