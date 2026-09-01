"""Consensus-round domain logic layered over the generic governance broker."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import hashlib
import re
from typing import cast

from peerhub.core.context import Clock, IdSource
from peerhub.core.errors import (
    InvalidMutationError,
    RecordNotFoundError,
    StaleRevisionError,
)
from peerhub.core.protocol import CommandID, JsonValue
from peerhub.core.protocol import canonical_json_bytes

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
                "recorded_votes": 0,
                "decisive_votes": 0,
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
        digest = self.resolution_decision_hash(state, outcome)
        state["resolution"] = {
            "outcome": outcome,
            "resolved_at": self._clock.now(),
            "resolved_by": resolved_by,
            "basis": basis,
            "decision_hash": digest,
            "effective_state": outcome,
        }
        state["status"] = "resolved"

    @staticmethod
    def resolution_decision_hash(
        state: Mapping[str, JsonValue],
        outcome: str,
    ) -> str:
        """Hash the frozen vote snapshot and outcome deterministically."""

        return hashlib.sha256(
            canonical_json_bytes(
                {"votes": state["votes"], "outcome": outcome}
            )
        ).hexdigest()

    def resolve(
        self,
        round_id: str,
        outcome: str,
        resolved_by: str,
        basis: str,
        expected_revision: int | None = None,
        effect_intent: EffectIntent | None = None,
    ) -> MutationSubmission:
        target = self._broker.get_target(round_id)
        if target is None:
            raise RecordNotFoundError("consensus-round", round_id)
        state = dict(target.state)
        phase = state.get("phase")
        dissent_rejection = (
            outcome.lower() == "rejected"
            and self._has_eligible_dissent(state)
        )
        if state.get("status") != "open" or (
            phase not in {"quorum_reached", "final_call"}
            and state.get("escalation") is None
            and not dissent_rejection
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
            effect_intent=effect_intent,
        )

    @staticmethod
    def _has_eligible_dissent(state: Mapping[str, JsonValue]) -> bool:
        participants = state.get("participants")
        votes = state.get("votes")
        if not isinstance(participants, Mapping):
            raise InvalidMutationError("participants must be an object")
        if not isinstance(votes, Mapping):
            raise InvalidMutationError("votes must be an object")
        eligible = participants.get("eligible")
        if not isinstance(eligible, (list, tuple)):
            raise InvalidMutationError("participants.eligible must be an array")
        return any(
            isinstance(vote, Mapping)
            and voter_id in eligible
            and vote.get("choice") == "disagree"
            for voter_id, vote in votes.items()
        )

    def reject_on_dissent(
        self,
        round_id: str,
        *,
        rejected_by: str,
        basis: str,
        expected_revision: int | None = None,
    ) -> MutationSubmission:
        """Reject an open round after verifying a stored eligible dissent."""

        target, state, audit, participants = self._open_state(
            round_id,
            {"proposed", "voting", "quorum_reached", "final_call"},
        )
        del participants
        if not self._has_eligible_dissent(state):
            raise InvalidMutationError("rejection requires an eligible disagree vote")

        self._set_resolution(state, "rejected", rejected_by, basis)
        state["phase"] = "resolved"
        self._finish(state, audit, "reject_on_dissent", rejected_by)
        return self._submit(
            target_id=round_id,
            expected_revision=(
                target.revision
                if expected_revision is None
                else expected_revision
            ),
            actor_id=rejected_by,
            operation="consensus.reject_on_dissent",
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
        reason: str | None = None,
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
        if choice not in {"agree", "disagree", "abstain", "need_more_info"}:
            raise InvalidMutationError(
                "choice must be agree, disagree, abstain, or need_more_info"
            )
        if reason is not None and not isinstance(reason, str):  # pyright: ignore[reportUnnecessaryIsInstance]
            raise InvalidMutationError("reason must be a string or null")
        votes = dict(cast(dict[str, JsonValue], state["votes"]))
        timestamp = self._clock.now()
        votes[actor_id] = {
            "choice": choice,
            "actor_id": actor_id,
            "cast_at": timestamp,
            "mutation_id": self._ids.new_id("consensus-vote"),
            "reason": reason,
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
        required_participants = participants.get("required")
        if not isinstance(required_participants, (tuple, list)):
            raise InvalidMutationError("participants.required must be an array")
        required_set = set(required_participants)
        counted = sum(
            1
            for voter_id, vote in votes.items()
            if voter_id in required_set
            and isinstance(vote, Mapping)
            and vote.get("choice") == "agree"
        )
        decisive = sum(
            1
            for vote in votes.values()
            if isinstance(vote, Mapping)
            and vote.get("choice") in {"agree", "disagree"}
        )
        has_dissent = any(
            isinstance(vote, Mapping) and vote.get("choice") == "disagree"
            for vote in votes.values()
        )
        reached = counted >= required_votes and not has_dissent
        state["votes"] = votes
        state["quorum"] = {
            **quorum,
            "reached": reached,
            "reached_at": quorum["reached_at"] if quorum["reached_at"] is not None else (timestamp if reached else None),
            "counted_votes": counted,
            "recorded_votes": len(votes),
            "decisive_votes": decisive,
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

    def record_arbiter_opinion(
        self,
        round_id: str,
        *,
        request_target_id: str,
        opinion_target_id: str,
        actor_id: str,
    ) -> MutationSubmission | None:
        """Attach the first valid arbiter opinion without changing resolution.

        The application layer owns creation of the immutable request and
        opinion targets.  Consensus owns only validation of those records and
        the canonical reference on the round.  A repeated call for the same
        opinion is idempotent; a different valid opinion never replaces the
        first one.
        """

        request_target = self._broker.get_target(request_target_id)
        if request_target is None:
            raise RecordNotFoundError("arbiter-review", request_target_id)
        opinion_target = self._broker.get_target(opinion_target_id)
        if opinion_target is None:
            raise RecordNotFoundError("arbiter-opinion", opinion_target_id)

        request_state = request_target.state
        opinion_state = opinion_target.state
        review_id = _required_text(request_state, "review_id")
        expected_request_id = f"arbiter-review:{round_id}:{review_id}"
        expected_opinion_id = f"arbiter-opinion:{round_id}:{review_id}"
        if (
            request_target_id != expected_request_id
            or opinion_target_id != expected_opinion_id
            or request_state.get("kind") != "arbiter-review"
            or opinion_state.get("kind") != "arbiter-opinion"
            or request_state.get("round_id") != round_id
            or opinion_state.get("round_id") != round_id
            or opinion_state.get("review_id") != review_id
            or opinion_state.get("request_target_id") != request_target_id
        ):
            raise InvalidMutationError(
                "arbiter request and opinion target identities do not match"
            )

        candidate = _required_mapping(request_state, "candidate")
        returned_by = _required_mapping(opinion_state, "returned_by")
        candidate_peer = _required_text(candidate, "peer_name")
        candidate_profile = _required_text(candidate, "profile_id")
        if (
            _required_text(returned_by, "peer_name") != candidate_peer
            or _required_text(returned_by, "profile_id")
            != candidate_profile
        ):
            raise InvalidMutationError(
                "arbiter opinion peer/profile does not match the frozen request"
            )

        dispatch = _required_mapping(opinion_state, "dispatch")
        if dispatch.get("state") != "SUCCEEDED_VERIFIED":
            raise InvalidMutationError(
                "arbiter opinion dispatch is not SUCCEEDED_VERIFIED"
            )
        response_text = opinion_state.get("response_text")
        if not isinstance(response_text, str):
            raise InvalidMutationError("arbiter opinion response_text is invalid")
        parsed_verdict = _strict_arbiter_verdict(response_text)
        if (
            parsed_verdict is None
            or opinion_state.get("parsed_verdict") != parsed_verdict
        ):
            raise InvalidMutationError(
                "arbiter opinion does not contain a syntactically valid verdict"
            )

        for _ in range(8):
            target = self._broker.get_target(round_id)
            if target is None:
                raise RecordNotFoundError("consensus-round", round_id)
            state = dict(target.state)
            if state.get("status") != "resolved":
                raise InvalidMutationError(
                    "arbiter opinions require a resolved consensus round"
                )
            current = state.get("arbiter_opinion")
            if current is not None:
                if not isinstance(current, Mapping):
                    raise InvalidMutationError(
                        "consensus round arbiter_opinion is invalid"
                    )
                # First valid canonical opinion wins.  Later valid immutable
                # opinions remain evidence but never replace that reference.
                return None

            audit = dict(_required_mapping(state, "audit"))
            state["arbiter_opinion"] = {
                "request_target_id": request_target_id,
                "opinion_target_id": opinion_target_id,
                "review_id": review_id,
                "verdict": parsed_verdict,
                "peer_name": candidate_peer,
                "profile_id": candidate_profile,
                "recorded_at": _required_nonnegative_int(
                    opinion_state,
                    "recorded_at",
                ),
            }
            self._finish(
                state,
                audit,
                "record_arbiter_opinion",
                actor_id,
            )
            try:
                return self._submit(
                    target_id=round_id,
                    expected_revision=target.revision,
                    actor_id=actor_id,
                    operation="consensus.record_arbiter_opinion",
                    desired_state=state,
                )
            except StaleRevisionError:
                continue
        raise InvalidMutationError(
            "consensus round changed repeatedly while recording arbiter opinion"
        )

    def _submit(
        self,
        *,
        target_id: str,
        expected_revision: int,
        actor_id: str,
        operation: str,
        desired_state: dict[str, JsonValue],
        effect_intent: EffectIntent | None = None,
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
                effect_intent=(
                    effect_intent
                    if effect_intent is not None
                    else EffectIntent(kind="consensus.noop", payload={})
                ),
            )
        )


def _required_mapping(
    value: Mapping[str, JsonValue],
    field: str,
) -> Mapping[str, JsonValue]:
    result = value.get(field)
    if not isinstance(result, Mapping):
        raise InvalidMutationError(f"{field} must be an object")
    return result


def _required_text(value: Mapping[str, JsonValue], field: str) -> str:
    result = value.get(field)
    if not isinstance(result, str) or not result.strip():
        raise InvalidMutationError(f"{field} must be a non-empty string")
    return result


def _required_nonnegative_int(
    value: Mapping[str, JsonValue],
    field: str,
) -> int:
    result = value.get(field)
    if type(result) is not int or result < 0:
        raise InvalidMutationError(f"{field} must be a nonnegative integer")
    return result


_ARBITER_VERDICT = re.compile(
    r"VERDICT:\s*(APPROVE|REJECT)",
    re.IGNORECASE,
)


def _strict_arbiter_verdict(response_text: str) -> str | None:
    for line in response_text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        match = _ARBITER_VERDICT.fullmatch(stripped)
        return match.group(1).upper() if match is not None else None
    return None
