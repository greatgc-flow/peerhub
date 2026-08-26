"""Consensus-round domain logic layered over the generic governance broker."""

from __future__ import annotations

from collections.abc import Sequence
from typing import cast

from peerhub.core.context import Clock, IdSource
from peerhub.core.errors import InvalidMutationError, RecordNotFoundError
from peerhub.core.protocol import CommandID, JsonValue

from .broker import GovernanceBroker
from .contract import EffectIntent, MutationRequest, MutationSubmission


class ConsensusService:
    """Create and mutate consensus-round TargetStates."""

    def __init__(self, broker: GovernanceBroker, *, clock: Clock, ids: IdSource) -> None:
        self._broker = broker
        self._clock = clock
        self._ids = ids

    @staticmethod
    def _quorum_required(participant_count: int, risk: str) -> int:
        if participant_count < 1:
            raise ValueError("at least one required participant is needed")
        # The ratified v1 policy leaves f undefined above N=3 and defaults to N.
        return max(2, participant_count)

    def propose(
        self,
        *,
        round_id: str,
        title: str,
        question: str,
        body: str,
        proposer_id: str,
        required_participants: Sequence[str],
        eligible_participants: Sequence[str],
        risk: str,
        source_hash: str,
    ) -> MutationSubmission:
        timestamp = self._clock.now()
        required = tuple(required_participants)
        eligible = tuple(eligible_participants)
        quorum_required = self._quorum_required(len(required), risk)
        state: dict[str, JsonValue] = {
            "schema": "peerhub.consensus-round.v1",
            "round_id": round_id,
            "phase": "proposed",
            "status": "open",
            "kind": "consensus-round",
            "scope": None,
            "proposal": {
                "title": title,
                "question": question,
                "body": body,
                "proposer_id": proposer_id,
                "proposed_at": timestamp,
                "source_hash": source_hash,
            },
            "participants": {
                "required": required,
                "eligible": eligible,
                "quorum": {
                    "formula": "max(2, f(N, risk))",
                    "required": quorum_required,
                    "risk": risk,
                    "basis": "protocol-v2",
                },
            },
            "votes": {},
            "quorum": {
                "reached": False,
                "reached_at": None,
                "counted_votes": 0,
                "required_votes": quorum_required,
            },
            "final_call": None,
            "escalation": None,
            "resolution": None,
            "abandonment": None,
            "audit": {
                "last_operation": "propose",
                "last_actor_id": proposer_id,
                "operation_count": 1,
            },
        }
        return self._submit(
            target_id=round_id,
            expected_revision=0,
            actor_id=proposer_id,
            operation="consensus.propose",
            desired_state=state,
        )

    def cast_vote(
        self,
        round_id: str,
        *,
        actor_id: str,
        choice: str,
        expected_revision: int | None = None,
    ) -> MutationSubmission:
        target = self._broker.get_target(round_id)
        if target is None:
            raise RecordNotFoundError("consensus-round", round_id)
        state = dict(target.state)
        phase = state.get("phase")
        if phase not in {"proposed", "voting"}:
            raise InvalidMutationError("votes are closed for this round")
        participants = cast(dict[str, JsonValue], state["participants"])
        eligible = participants["eligible"]
        if not isinstance(eligible, (tuple, list)) or actor_id not in eligible:
            raise InvalidMutationError("actor is not an eligible voter")
        if choice not in {"agree", "disagree"}:
            raise InvalidMutationError("choice must be agree or disagree")
        votes = dict(cast(dict[str, JsonValue], state["votes"]))
        timestamp = self._clock.now()
        votes[actor_id] = {
            "choice": choice,
            "actor_id": actor_id,
            "cast_at": timestamp,
            "mutation_id": self._ids.new_id("consensus-vote"),
        }
        quorum = dict(cast(dict[str, JsonValue], state["quorum"]))
        audit = dict(cast(dict[str, JsonValue], state["audit"]))
        required_raw = quorum["required_votes"]
        operation_count_raw = audit["operation_count"]
        if not isinstance(required_raw, int) or isinstance(required_raw, bool):
            raise InvalidMutationError("invalid quorum.required_votes")
        if not isinstance(operation_count_raw, int) or isinstance(operation_count_raw, bool):
            raise InvalidMutationError("invalid audit.operation_count")
        required_votes = required_raw
        counted = len(votes)
        reached = counted >= required_votes
        state["votes"] = votes
        state["quorum"] = {
            **quorum,
            "reached": reached,
            "reached_at": quorum["reached_at"] if quorum["reached_at"] is not None else (timestamp if reached else None),
            "counted_votes": counted,
        }
        state["phase"] = "quorum_reached" if reached else "voting"
        state["audit"] = {
            **audit,
            "last_operation": "cast_vote",
            "last_actor_id": actor_id,
            "operation_count": operation_count_raw + 1,
        }
        return self._submit(
            target_id=round_id,
            expected_revision=target.revision if expected_revision is None else expected_revision,
            actor_id=actor_id,
            operation="consensus.cast_vote",
            desired_state=state,
        )

    def _submit(
        self,
        *,
        target_id: str,
        expected_revision: int,
        actor_id: str,
        operation: str,
        desired_state: dict[str, JsonValue],
    ) -> MutationSubmission:
        request_id = self._ids.new_id("consensus-request")
        return self._broker.submit(
            MutationRequest(
                request_id=request_id,
                command_id=CommandID(self._ids.new_id("consensus-command")),
                correlation_id=self._ids.new_id("consensus-correlation"),
                client_id="peerhub.consensus",
                command_type=operation,
                idempotency_key=request_id,
                actor_id=actor_id,
                policy_revision="protocol-v2",
                target_id=target_id,
                expected_revision=expected_revision,
                operation=operation,
                desired_state=desired_state,
                effect_intent=EffectIntent(kind="consensus.noop", payload={}),
            )
        )
