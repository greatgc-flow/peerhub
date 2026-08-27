"""Consensus-round domain logic layered over the generic governance broker."""

from __future__ import annotations

from collections.abc import Sequence
import hashlib
import json
from typing import cast

from peerhub.core.context import Clock, IdSource
from peerhub.core.errors import InvalidMutationError, RecordNotFoundError
from peerhub.core.protocol import CommandID, JsonValue

from .broker import GovernanceBroker
from .contract import EffectIntent, MutationRequest, MutationSubmission, TargetState


class ConsensusService:
    """Create and mutate consensus-round TargetStates."""

    def __init__(self, broker: GovernanceBroker, *, clock: Clock, ids: IdSource) -> None:
        self._broker = broker
        self._clock = clock
        self._ids = ids

    def get_target(self, round_id: str) -> TargetState | None:
        """Read a consensus round without exposing the broker internals."""

        return self._broker.get_target(round_id)

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
            "timeout_evidence": None,
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

    def _open_state(self, round_id: str, allowed: set[str]) -> tuple[TargetState, dict[str, JsonValue], dict[str, JsonValue], dict[str, JsonValue]]:
        target = self._broker.get_target(round_id)
        if target is None:
            raise RecordNotFoundError("consensus-round", round_id)
        state = dict(target.state)
        phase = state.get("phase")
        if phase not in allowed:
            raise InvalidMutationError("operation is not valid in the current phase")
        if state.get("status") != "open":
            raise InvalidMutationError("round is terminal")
        return target, state, dict(cast(dict[str, JsonValue], state["audit"])), dict(cast(dict[str, JsonValue], state["participants"]))

    def _finish(self, state: dict[str, JsonValue], audit: dict[str, JsonValue], operation: str, actor_id: str) -> None:
        count = audit.get("operation_count")
        if not isinstance(count, int) or isinstance(count, bool):
            raise InvalidMutationError("invalid audit.operation_count")
        state["audit"] = {**audit, "last_operation": operation, "last_actor_id": actor_id, "operation_count": count + 1}

    def final_call_ack(
        self,
        round_id: str,
        *,
        actor_id: str,
        ack: bool,
        expected_revision: int | None = None,
    ) -> MutationSubmission:
        target, state, audit, participants = self._open_state(
            round_id, {"quorum_reached", "final_call"}
        )
        eligible = participants.get("eligible")
        if not isinstance(eligible, (list, tuple)) or actor_id not in eligible:
            raise InvalidMutationError("actor is not an eligible final-call acknowledger")

        final_raw = state.get("final_call")
        if final_raw is None:
            final: dict[str, JsonValue] = {
                "required": True,
                "opened_at": self._clock.now(),
                "opened_by": actor_id,
                "question": cast(dict[str, JsonValue], state["proposal"])["question"],
                "acks": {},
                "required_acks": tuple(eligible),
                "ack_count": 0,
                "complete": False,
            }
        else:
            final = dict(cast(dict[str, JsonValue], final_raw))

        acks = dict(cast(dict[str, JsonValue], final["acks"]))
        acks[actor_id] = {
            "ack": ack,
            "actor_id": actor_id,
            "acked_at": self._clock.now(),
            "mutation_id": self._ids.new_id("consensus-ack"),
        }
        final["acks"] = acks
        final["ack_count"] = len(acks)
        final["complete"] = len(acks) == len(eligible) and all(
            cast(dict[str, JsonValue], vote)["ack"] is True for vote in acks.values()
        )
        state["final_call"] = final
        state["phase"] = "resolved" if final["complete"] else "final_call"
        self._finish(state, audit, "final_call_ack", actor_id)
        if final["complete"]:
            self._set_resolution(
                state, "approved", actor_id, "all final-call acknowledgements agree"
            )
        return self._submit(
            target_id=round_id,
            expected_revision=target.revision if expected_revision is None else expected_revision,
            actor_id=actor_id,
            operation="consensus.final_call_ack",
            desired_state=state,
        )

    def mark_timeout(
        self, round_id: str, reason: str, expected_revision: int | None = None
    ) -> MutationSubmission:
        target, state, audit, _ = self._open_state(
            round_id, {"proposed", "voting", "quorum_reached", "final_call"}
        )
        phase = cast(str, state["phase"])
        now = self._clock.now()
        state["timeout_evidence"] = {
            "marked_at": now,
            "reason": reason,
            "phase_at_timeout": phase,
        }
        # 1800s (30min) matches this session's own ratified legacy timeout
        # value (HUB-REPLACEMENT-GAP2-CONSENSUS-2026-08-24.md), not a guess.
        state["escalation"] = {
            "reason": reason,
            "requester": "system:timeout",
            "tier": 0,
            "deadline": now + 1800,
            "required_authority": "human-tier-0",
        }
        self._finish(state, audit, "mark_timeout", "system:timeout")
        return self._submit(
            target_id=round_id,
            expected_revision=target.revision if expected_revision is None else expected_revision,
            actor_id="system:timeout",
            operation="consensus.mark_timeout",
            desired_state=state,
        )

    def request_escalation(
        self,
        round_id: str,
        reason: str,
        requester_id: str,
        tier: int,
        required_authority: str,
        expected_revision: int | None = None,
    ) -> MutationSubmission:
        target, state, audit, _ = self._open_state(
            round_id, {"proposed", "voting", "quorum_reached", "final_call"}
        )
        now = self._clock.now()
        state["escalation"] = {
            "reason": reason,
            "requester": requester_id,
            "tier": tier,
            "deadline": now + 1800,
            "required_authority": required_authority,
        }
        self._finish(state, audit, "request_escalation", requester_id)
        return self._submit(
            target_id=round_id,
            expected_revision=target.revision if expected_revision is None else expected_revision,
            actor_id=requester_id,
            operation="consensus.request_escalation",
            desired_state=state,
        )

    def _set_resolution(
        self, state: dict[str, JsonValue], outcome: str, resolved_by: str, basis: str
    ) -> None:
        digest = hashlib.sha256(
            json.dumps(
                {"votes": state["votes"], "outcome": outcome}, sort_keys=True, default=str
            ).encode()
        ).hexdigest()
        state["resolution"] = {
            "outcome": outcome,
            "resolved_at": self._clock.now(),
            "resolved_by": resolved_by,
            "basis": basis,
            "decision_hash": digest,
            "effective_state": outcome,
        }
        state["status"] = "resolved"

    def resolve(
        self,
        round_id: str,
        outcome: str,
        resolved_by: str,
        basis: str,
        expected_revision: int | None = None,
    ) -> MutationSubmission:
        target = self._broker.get_target(round_id)
        if target is None:
            raise RecordNotFoundError("consensus-round", round_id)
        state = dict(target.state)
        phase = state.get("phase")
        if state.get("status") != "open" or (
            phase not in {"quorum_reached", "final_call"} and state.get("escalation") is None
        ):
            raise InvalidMutationError("resolution prerequisites are not satisfied")
        audit = dict(cast(dict[str, JsonValue], state["audit"]))
        if (
            phase == "final_call"
            and not cast(dict[str, JsonValue], state.get("final_call"))["complete"]
            and state.get("escalation") is None
        ):
            raise InvalidMutationError("final call is incomplete")
        self._set_resolution(state, outcome, resolved_by, basis)
        state["phase"] = "resolved"
        self._finish(state, audit, "resolve", resolved_by)
        return self._submit(
            target_id=round_id,
            expected_revision=target.revision if expected_revision is None else expected_revision,
            actor_id=resolved_by,
            operation="consensus.resolve",
            desired_state=state,
        )

    def abandon(
        self,
        round_id: str,
        reason_code: str,
        reason: str,
        abandoned_by: str,
        expected_revision: int | None = None,
    ) -> MutationSubmission:
        target, state, audit, _ = self._open_state(
            round_id, {"proposed", "voting", "quorum_reached", "final_call"}
        )
        state["abandonment"] = {
            "reason_code": reason_code,
            "reason": reason,
            "abandoned_at": self._clock.now(),
            "abandoned_by": abandoned_by,
            "preceded_by": state["phase"],
        }
        state["phase"] = "abandoned"; state["status"] = "abandoned"; self._finish(state, audit, "abandon", abandoned_by)
        return self._submit(target_id=round_id, expected_revision=target.revision if expected_revision is None else expected_revision, actor_id=abandoned_by, operation="consensus.abandon", desired_state=state)

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
