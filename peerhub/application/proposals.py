"""Application orchestration for legacy-shaped proposal add/vote flows."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from typing import cast

from peerhub.application.peer_registry import PeerRegistryService
from peerhub.core.context import Clock, IdSource
from peerhub.core.errors import (
    InvalidMutationError,
    RecordNotFoundError,
    StaleRevisionError,
)
from peerhub.core.protocol import JsonValue, require_text
from peerhub.governance.broker import GovernanceBroker
from peerhub.governance.consensus import ConsensusService
from peerhub.governance.contract import EffectIntent, TargetState
from peerhub.governance.invariant_requests import (
    RATIFIED_INVARIANT_EFFECT_KIND,
    RatifiedInvariantRequestProjector,
)
from peerhub.health.contract import AdmissionState, AvailabilityState
from peerhub.health.service import HealthService


ESCALATION_TOO_FEW_VOTERS = "N < 2 (human_gate)"
ESCALATION_MID_ROUND_GATE = "mid-round gate closure (human_gate)"
ESCALATION_SELF_FINALIZATION = (
    "proposer self-finalization blocked (human_gate)"
)
_PROPOSAL_CONFIG_RELATIVE = Path(".peerhub") / "proposals.json"
_CHANGES_MARKER = "\n\nChanges:\n"


@dataclass(frozen=True, slots=True)
class ProposalAddResult:
    round_id: str
    from_peer: str
    impact: str
    eligible_participants: tuple[str, ...]
    receipt_id: str
    revision: int


@dataclass(frozen=True, slots=True)
class ProposalVoteResult:
    round_id: str
    voter: str
    choice: str
    outcome: str | None
    agreed: tuple[str, ...]
    disagreed: tuple[str, ...]
    escalation_reason: str | None
    invariant_request_target_id: str | None
    revision: int


def load_proposal_voters(workspace_root: Path) -> tuple[str, ...]:
    """Load the ordered proposal electorate from one workspace config value."""

    config_path = workspace_root / _PROPOSAL_CONFIG_RELATIVE
    if not config_path.exists():
        return ()
    with config_path.open("r", encoding="utf-8") as stream:
        raw: object = json.load(stream)
    if not isinstance(raw, Mapping):
        raise ValueError(".peerhub/proposals.json must contain an object")
    raw_mapping = cast(Mapping[object, object], raw)
    voters = raw_mapping.get("voters")
    if not isinstance(voters, (list, tuple)):
        raise ValueError(".peerhub/proposals.json voters must be an array")
    voter_values = cast(list[object] | tuple[object, ...], voters)
    return _validate_voter_policy(tuple(voter_values))


def _validate_voter_policy(values: tuple[object, ...]) -> tuple[str, ...]:
    voters: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("proposal voters must be non-empty strings")
        if value in seen:
            raise ValueError(f"proposal voter {value!r} is duplicated")
        voters.append(value)
        seen.add(value)
    return tuple(voters)


def legacy_proposal_slug(subject: str) -> str:
    """Match the legacy proposal allocator's topic slug expression."""

    return re.sub(r"[^\w-]", "-", subject.lower())[:40]


class ProposalCoordinator:
    """Join configured voter policy, health, consensus, and request effects."""

    def __init__(
        self,
        broker: GovernanceBroker,
        consensus: ConsensusService,
        *,
        peer_registry: PeerRegistryService,
        health: HealthService,
        voter_node_ids: tuple[str, ...],
        clock: Clock,
        ids: IdSource,
        invariant_projector: RatifiedInvariantRequestProjector | None = None,
    ) -> None:
        self._broker = broker
        self._consensus = consensus
        self._peer_registry = peer_registry
        self._health = health
        self._voter_node_ids = _validate_voter_policy(
            cast(tuple[object, ...], voter_node_ids)
        )
        self._clock = clock
        self._ids = ids
        self._invariant_projector = (
            invariant_projector
            if invariant_projector is not None
            else RatifiedInvariantRequestProjector(
                broker,
                clock=clock,
                ids=ids,
            )
        )

    @staticmethod
    def _node_identity(target: TargetState) -> tuple[str, str]:
        peer_kind = target.state.get("peer_kind")
        profile_id = target.state.get("profile_id")
        if not isinstance(peer_kind, str) or not peer_kind:
            raise InvalidMutationError("peer node has malformed peer_kind")
        if not isinstance(profile_id, str) or not profile_id:
            raise InvalidMutationError("peer node has malformed profile_id")
        return peer_kind, profile_id

    def _voter_gate_is_open(self, voter_id: str, evaluated_at: int) -> bool:
        try:
            node = self._peer_registry.get_node(voter_id)
        except RecordNotFoundError:
            return False
        peer_kind, profile_id = self._node_identity(node)
        projection = self._health.read_health_projection(
            peer_kind,
            profile_id,
            evaluated_at=evaluated_at,
        )
        return (
            projection is not None
            and projection.effective_availability_state
            not in {AvailabilityState.UNAVAILABLE, AvailabilityState.STALE}
            and projection.effective_admission_state is AdmissionState.OPEN
            and not projection.profile_gate_backed_off
        )

    def _eligible_voters(self, evaluated_at: int) -> tuple[str, ...]:
        return tuple(
            voter_id
            for voter_id in self._voter_node_ids
            if self._voter_gate_is_open(voter_id, evaluated_at)
        )

    @staticmethod
    def _date_token(timestamp: int) -> str:
        return datetime.fromtimestamp(timestamp, tz=timezone.utc).strftime(
            "%Y%m%d"
        )

    def _next_round_id(
        self,
        date_token: str,
        slug: str,
        *,
        sequence_floor: int = 1,
    ) -> tuple[str, int]:
        pattern = re.compile(
            rf"^{re.escape(date_token)}-{re.escape(slug)}-(\d{{3,}})$"
        )
        highest = sequence_floor - 1
        for target in self._broker.list_targets("consensus-round", None):
            match = pattern.fullmatch(target.target_id)
            if match is not None:
                highest = max(highest, int(match.group(1)))
        sequence = highest + 1
        return f"{date_token}-{slug}-{sequence:03d}", sequence

    @staticmethod
    def _risk(impact: str) -> str:
        normalized = impact.lower()
        return "normal" if normalized in {"med", "medium"} else normalized

    def add_proposal(
        self,
        *,
        subject: str,
        from_peer: str = "cc",
        impact: str = "med",
        rationale: str = "",
        text: str = "",
    ) -> ProposalAddResult:
        normalized_subject = require_text(subject, "subject")
        normalized_from_peer = require_text(from_peer, "from_peer")
        if not isinstance(impact, str):  # pyright: ignore[reportUnnecessaryIsInstance]
            raise ValueError("impact must be a string")
        if not isinstance(rationale, str):  # pyright: ignore[reportUnnecessaryIsInstance]
            raise ValueError("rationale must be a string")
        if not isinstance(text, str):  # pyright: ignore[reportUnnecessaryIsInstance]
            raise ValueError("text must be a string")

        captured_at = self._clock.now()
        eligible = self._eligible_voters(captured_at)
        if not eligible:
            raise InvalidMutationError(
                "proposal has zero eligible voters after health filtering"
            )

        body = (
            f"Impact: {impact}\n\nRationale:\n{rationale}"
            f"\n\nChanges:\n{text}"
        )
        source_hash = "sha256:" + hashlib.sha256(
            body.encode("utf-8")
        ).hexdigest()
        date_token = self._date_token(captured_at)
        slug = legacy_proposal_slug(normalized_subject)
        sequence_floor = 1

        for _ in range(16):
            round_id, sequence = self._next_round_id(
                date_token,
                slug,
                sequence_floor=sequence_floor,
            )
            try:
                submission = self._consensus.propose(
                    round_id=round_id,
                    title=normalized_subject,
                    question=(
                        "Should PeerHub ratify the proposal: "
                        f"{normalized_subject}?"
                    ),
                    body=body,
                    proposer_id=normalized_from_peer,
                    required_participants=eligible,
                    eligible_participants=eligible,
                    risk=self._risk(impact),
                    source_hash=source_hash,
                )
            except StaleRevisionError:
                sequence_floor = sequence + 1
                continue
            return ProposalAddResult(
                round_id=round_id,
                from_peer=normalized_from_peer,
                impact=impact,
                eligible_participants=eligible,
                receipt_id=submission.receipt.receipt_id,
                revision=submission.receipt.next_revision,
            )
        raise InvalidMutationError(
            "proposal round ID changed repeatedly during allocation"
        )

    @staticmethod
    def _mapping(
        value: Mapping[str, JsonValue], field: str
    ) -> Mapping[str, JsonValue]:
        result = value.get(field)
        if not isinstance(result, Mapping):
            raise InvalidMutationError(f"{field} must be an object")
        return result

    @staticmethod
    def _participant_tuple(
        participants: Mapping[str, JsonValue], field: str
    ) -> tuple[str, ...]:
        raw = participants.get(field)
        if not isinstance(raw, (tuple, list)) or not all(
            isinstance(value, str) for value in raw
        ):
            raise InvalidMutationError(
                f"participants.{field} must be an array of strings"
            )
        return tuple(cast(str, value) for value in raw)

    @staticmethod
    def _choice(votes: Mapping[str, JsonValue], voter_id: str) -> str | None:
        vote = votes.get(voter_id)
        if vote is None:
            return None
        if not isinstance(vote, Mapping):
            raise InvalidMutationError("consensus vote must be an object")
        choice = vote.get("choice")
        if not isinstance(choice, str):
            raise InvalidMutationError("consensus vote choice must be a string")
        return choice

    def _approval_effect(
        self,
        target: TargetState,
    ) -> tuple[EffectIntent, str]:
        state = target.state
        proposal = self._mapping(state, "proposal")
        participants = self._mapping(state, "participants")
        votes = self._mapping(state, "votes")
        proposer_id = require_text(
            cast(str, proposal.get("proposer_id")),
            "proposal.proposer_id",
        )
        title = require_text(
            cast(str, proposal.get("title")), "proposal.title"
        )
        question = require_text(
            cast(str, proposal.get("question")), "proposal.question"
        )
        body = require_text(
            cast(str, proposal.get("body")), "proposal.body"
        )
        source_hash = require_text(
            cast(str, proposal.get("source_hash")), "proposal.source_hash"
        )
        marker_index = body.find(_CHANGES_MARKER)
        if marker_index < 0:
            raise InvalidMutationError(
                "proposal body lacks the canonical Changes section"
            )
        proposed_invariant_text = body[
            marker_index + len(_CHANGES_MARKER) :
        ]
        decision_hash = self._consensus.resolution_decision_hash(
            state,
            "approved",
        )
        request_id = (
            "ratified-invariant-write-request:"
            f"{target.target_id}:{decision_hash}"
        )
        payload: dict[str, JsonValue] = {
            "request_id": request_id,
            "round_id": target.target_id,
            "approved_revision": target.revision + 1,
            "decision_hash": decision_hash,
            "proposer_id": proposer_id,
            "title": title,
            "question": question,
            "body": body,
            "source_hash": source_hash,
            "participants": participants,
            "votes": votes,
            "proposed_invariant_text": proposed_invariant_text,
            "target_doc_hint": "10-invariants.md",
            "requested_at": self._clock.now(),
        }
        return (
            EffectIntent(
                kind=RATIFIED_INVARIANT_EFFECT_KIND,
                payload=payload,
            ),
            request_id,
        )

    def _project_approved_request(
        self,
        target: TargetState,
        *,
        preferred_event_id: str | None = None,
    ) -> str:
        resolution = self._mapping(target.state, "resolution")
        decision_hash = require_text(
            cast(str, resolution.get("decision_hash")),
            "resolution.decision_hash",
        )
        request_id = (
            "ratified-invariant-write-request:"
            f"{target.target_id}:{decision_hash}"
        )
        existing = self._broker.get_target(request_id)

        event_ids: list[str] = []
        if preferred_event_id is not None:
            event_ids.append(preferred_event_id)
        for pending in self._broker.recover_pending_effects(limit=1000):
            event = pending.event
            if (
                event.event_id not in event_ids
                and event.payload.get("target_id") == target.target_id
                and event.payload.get("effect_kind")
                == RATIFIED_INVARIANT_EFFECT_KIND
            ):
                event_ids.append(event.event_id)
        for event_id in event_ids:
            projected = self._invariant_projector.project_event(event_id)
            if projected.target_id == request_id:
                return request_id
        if existing is not None:
            self._validate_existing_request(target, existing)
            return existing.target_id
        raise InvalidMutationError(
            "approved proposal lacks its ratified invariant request effect"
        )

    def _validate_existing_request(
        self,
        round_target: TargetState,
        request_target: TargetState,
    ) -> None:
        proposal = self._mapping(round_target.state, "proposal")
        participants = self._mapping(round_target.state, "participants")
        votes = self._mapping(round_target.state, "votes")
        resolution = self._mapping(round_target.state, "resolution")
        body = proposal.get("body")
        if not isinstance(body, str) or _CHANGES_MARKER not in body:
            raise InvalidMutationError(
                "approved proposal body lacks the canonical Changes section"
            )
        state = request_target.state
        expected = {
            "kind": "ratified-invariant-write-request",
            "scope": round_target.target_id,
            "schema_version": 1,
            "status": "REQUESTED",
            "request_id": request_target.target_id,
            "round_id": round_target.target_id,
            "approved_revision": round_target.revision,
            "decision_hash": resolution.get("decision_hash"),
            "proposer_id": proposal.get("proposer_id"),
            "title": proposal.get("title"),
            "question": proposal.get("question"),
            "body": body,
            "source_hash": proposal.get("source_hash"),
            "participants": participants,
            "votes": votes,
            "proposed_invariant_text": body.split(_CHANGES_MARKER, 1)[1],
            "target_doc_hint": "10-invariants.md",
        }
        if any(state.get(field) != value for field, value in expected.items()):
            raise InvalidMutationError(
                "existing ratified invariant write request does not match "
                "the approved proposal snapshot"
            )
        requested_at = state.get("requested_at")
        if type(requested_at) is not int or requested_at < 0:
            raise InvalidMutationError(
                "existing ratified invariant request has invalid requested_at"
            )

    def _is_proposal_round(self, target: TargetState) -> bool:
        proposal = target.state.get("proposal")
        if not isinstance(proposal, Mapping):
            return False
        title = proposal.get("title")
        question = proposal.get("question")
        body = proposal.get("body")
        return (
            isinstance(title, str)
            and question
            == f"Should PeerHub ratify the proposal: {title}?"
            and isinstance(body, str)
            and _CHANGES_MARKER in body
        )

    def _result(
        self,
        target: TargetState,
        *,
        voter: str,
        choice: str,
        invariant_request_target_id: str | None = None,
    ) -> ProposalVoteResult:
        participants = self._mapping(target.state, "participants")
        eligible = self._participant_tuple(participants, "eligible")
        votes = self._mapping(target.state, "votes")
        agreed = tuple(
            voter_id
            for voter_id in eligible
            if self._choice(votes, voter_id) == "agree"
        )
        disagreed = tuple(
            voter_id
            for voter_id in eligible
            if self._choice(votes, voter_id) == "disagree"
        )
        resolution_raw = target.state.get("resolution")
        escalation_raw = target.state.get("escalation")
        outcome: str | None = None
        escalation_reason: str | None = None
        if isinstance(resolution_raw, Mapping):
            native_outcome = resolution_raw.get("outcome")
            if native_outcome == "approved":
                outcome = "CONSENSUS_OK"
            elif native_outcome == "rejected":
                outcome = "NACK"
        elif isinstance(escalation_raw, Mapping):
            outcome = "ESCALATED"
            reason = escalation_raw.get("reason")
            escalation_reason = reason if isinstance(reason, str) else None
        return ProposalVoteResult(
            round_id=target.target_id,
            voter=voter,
            choice=choice,
            outcome=outcome,
            agreed=agreed,
            disagreed=disagreed,
            escalation_reason=escalation_reason,
            invariant_request_target_id=invariant_request_target_id,
            revision=target.revision,
        )

    def reconcile_outcome(
        self,
        round_id: str,
        *,
        requester_id: str = "system:proposal-recovery",
        voter: str = "system:proposal-recovery",
        choice: str = "",
    ) -> ProposalVoteResult:
        """Idempotently advance one decisive round and project approval intent."""

        for _ in range(16):
            target = self._consensus.get_target(round_id)
            if target is None:
                raise RecordNotFoundError("consensus-round", round_id)

            if target.state.get("status") == "resolved":
                resolution = self._mapping(target.state, "resolution")
                request_target_id = None
                if resolution.get("outcome") == "approved":
                    request_target_id = self._project_approved_request(target)
                return self._result(
                    target,
                    voter=voter,
                    choice=choice,
                    invariant_request_target_id=request_target_id,
                )

            if target.state.get("escalation") is not None:
                return self._result(target, voter=voter, choice=choice)

            participants = self._mapping(target.state, "participants")
            required = self._participant_tuple(participants, "required")
            eligible = self._participant_tuple(participants, "eligible")
            votes = self._mapping(target.state, "votes")
            proposal = self._mapping(target.state, "proposal")
            proposer_id = require_text(
                cast(str, proposal.get("proposer_id")),
                "proposal.proposer_id",
            )
            agreed = tuple(
                voter_id
                for voter_id in eligible
                if self._choice(votes, voter_id) == "agree"
            )
            disagreed = tuple(
                voter_id
                for voter_id in eligible
                if self._choice(votes, voter_id) == "disagree"
            )
            gate_evaluated_at = self._clock.now()

            try:
                if len(eligible) < 2:
                    self._consensus.request_escalation(
                        round_id,
                        ESCALATION_TOO_FEW_VOTERS,
                        requester_id,
                        0,
                        "human-tier-0",
                        target.revision,
                    )
                elif any(
                    not self._voter_gate_is_open(
                        voter_id,
                        gate_evaluated_at,
                    )
                    for voter_id in eligible
                ):
                    self._consensus.request_escalation(
                        round_id,
                        ESCALATION_MID_ROUND_GATE,
                        requester_id,
                        0,
                        "human-tier-0",
                        target.revision,
                    )
                elif disagreed:
                    self._consensus.reject_on_dissent(
                        round_id,
                        rejected_by=requester_id,
                        basis="legacy proposal eligible voter dissent",
                        expected_revision=target.revision,
                    )
                elif all(
                    self._choice(votes, voter_id) == "agree"
                    for voter_id in required
                ) and any(voter_id != proposer_id for voter_id in agreed):
                    effect_intent, _ = self._approval_effect(target)
                    submission = self._consensus.resolve(
                        round_id,
                        "approved",
                        requester_id,
                        "legacy proposal unanimous agree",
                        target.revision,
                        effect_intent,
                    )
                    resolved = self._consensus.get_target(round_id)
                    if resolved is None:
                        raise RecordNotFoundError("consensus-round", round_id)
                    request_target_id = self._project_approved_request(
                        resolved,
                        preferred_event_id=(
                            submission.receipt.outbox_event_id
                        ),
                    )
                    return self._result(
                        resolved,
                        voter=voter,
                        choice=choice,
                        invariant_request_target_id=request_target_id,
                    )
                elif (
                    eligible
                    and all(voter_id in votes for voter_id in eligible)
                    and agreed == (proposer_id,)
                ):
                    self._consensus.request_escalation(
                        round_id,
                        ESCALATION_SELF_FINALIZATION,
                        requester_id,
                        0,
                        "human-tier-0",
                        target.revision,
                    )
                else:
                    return self._result(target, voter=voter, choice=choice)
            except StaleRevisionError:
                continue

            updated = self._consensus.get_target(round_id)
            if updated is None:
                raise RecordNotFoundError("consensus-round", round_id)
            return self._result(updated, voter=voter, choice=choice)

        raise InvalidMutationError(
            "proposal round changed repeatedly during outcome reconciliation"
        )

    def reconcile_pending_rounds(self) -> tuple[ProposalVoteResult, ...]:
        """Recover all open or approved proposal rounds in stable order."""

        results: list[ProposalVoteResult] = []
        for target in self._broker.list_targets("consensus-round", None):
            if not self._is_proposal_round(target):
                continue
            if target.state.get("status") == "open" or (
                isinstance(target.state.get("resolution"), Mapping)
                and cast(Mapping[str, JsonValue], target.state["resolution"]).get(
                    "outcome"
                )
                == "approved"
            ):
                results.append(self.reconcile_outcome(target.target_id))
        return tuple(results)

    def vote_proposal(
        self,
        proposal_id: str,
        *,
        voter: str = "cc",
        vote: str,
        reason: str = "",
    ) -> ProposalVoteResult:
        normalized_round_id = require_text(proposal_id, "proposal_id")
        normalized_voter = require_text(voter, "voter")
        normalized_choice = require_text(vote, "vote").lower()
        if not isinstance(reason, str):  # pyright: ignore[reportUnnecessaryIsInstance]
            raise ValueError("reason must be a string")
        self._consensus.cast_vote(
            normalized_round_id,
            actor_id=normalized_voter,
            choice=normalized_choice,
            reason=reason,
        )
        return self.reconcile_outcome(
            normalized_round_id,
            requester_id=normalized_voter,
            voter=normalized_voter,
            choice=normalized_choice,
        )


__all__ = [
    "ESCALATION_MID_ROUND_GATE",
    "ESCALATION_SELF_FINALIZATION",
    "ESCALATION_TOO_FEW_VOTERS",
    "ProposalAddResult",
    "ProposalCoordinator",
    "ProposalVoteResult",
    "legacy_proposal_slug",
    "load_proposal_voters",
]
