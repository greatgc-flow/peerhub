"""Consensus, proposal, and arbiter command registration handlers."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, cast

from peerhub.application.arbiter_review import ArbiterReviewCoordinator
from peerhub.application.commands.consensus import (
    ConsensusAckCommand,
    ConsensusCheckCommand,
    ConsensusCorrectCommand,
    ConsensusNackCommand,
    ConsensusProposeCommand,
    ConsensusRetractCommand,
    ConsensusSweepCommand,
    ConsensusVoteCommand,
)
from peerhub.application.commands.consensus import (
    ArbiterReviewCommand,
    ProposalAddCommand,
    ProposalListCommand,
    ProposalVoteCommand,
)
from peerhub.application.proposals import (
    ProposalAddResult,
    ProposalCoordinator,
    ProposalVoteResult,
)
from peerhub.application.handlers._params import required_text as proposal_text
from peerhub.application.handlers._params import string_tuple
from peerhub.core.protocol import CommandEnvelope, JsonValue
from peerhub.governance.broker import GovernanceBroker
from peerhub.application.consensus_facade import ConsensusFacade


def register_consensus_handlers(
    *,
    api: Any,
    service: ConsensusFacade,
    broker: GovernanceBroker | None,
    arbiter: ArbiterReviewCoordinator | None,
    proposals: ProposalCoordinator | None,
) -> None:
    """Register the existing consensus-domain wire handlers unchanged."""

    from peerhub.application.api import (
        CommandAvailability,
        CommandDescriptor,
        IdempotencyPolicy,
        Mutability,
        ScopeKind,
    )

    register = api.register
    submission = api._submission
    receipt = api._receipt
    descriptor = CommandDescriptor
    mutating = Mutability.MUTATING
    read_only = Mutability.READ_ONLY
    any_scope = ScopeKind.ANY
    domain_atomic_required = IdempotencyPolicy.DOMAIN_ATOMIC_REQUIRED
    idempotency_read_only = IdempotencyPolicy.READ_ONLY
    available = CommandAvailability.AVAILABLE

    def _optional_revision(params: Mapping[str, Any]) -> int | None:
        revision = params.get("expected_revision")
        if revision is not None and (
            not isinstance(revision, int) or isinstance(revision, bool)
        ):
            raise ValueError("expected_revision must be an integer or null")
        return revision

    def decode_propose(envelope: CommandEnvelope) -> ConsensusProposeCommand:
        params = envelope.params
        return ConsensusProposeCommand(
            submission(envelope),
            str(params["round_id"]),
            str(params["title"]),
            str(params["question"]),
            str(params["body"]),
            str(params["proposer_id"]),
            string_tuple(params, "required_participants"),
            string_tuple(params, "eligible_participants"),
            str(params["risk"]),
            str(params["source_hash"]),
            bool(params["verified_required"]),
        )

    def decode_vote(envelope: CommandEnvelope) -> ConsensusVoteCommand:
        params = envelope.params
        return ConsensusVoteCommand(
            submission(envelope),
            str(params["round_id"]),
            str(params["actor_id"]),
            str(params["choice"]),
            credential_id=envelope.credential_id,
            expected_revision=_optional_revision(params),
        )

    def decode_check(envelope: CommandEnvelope) -> ConsensusCheckCommand:
        return ConsensusCheckCommand(
            submission(envelope), str(envelope.params["round_id"])
        )

    def encode_target(result: Any) -> Mapping[str, JsonValue]:
        return {
            "target_id": result.target_id,
            "revision": result.revision,
            "state": result.state,
        }

    register(descriptor(
        "consensus.round.propose",
        mutating,
        any_scope,
        domain_atomic_required,
        decode_propose,
        lambda command, _: service.propose(
            round_id=command.round_id,
            title=command.title,
            question=command.question,
            body=command.body,
            proposer_id=command.proposer_id,
            required_participants=command.required_participants,
            eligible_participants=command.eligible_participants,
            risk=command.risk,
            source_hash=command.source_hash,
            verified_required=command.verified_required,
            idempotency_key=command.submission.idempotency_key,
        ),
        receipt,
        available,
    ))
    register(descriptor(
        "consensus.vote.cast",
        mutating,
        any_scope,
        domain_atomic_required,
        decode_vote,
        lambda command, _: service.cast_vote(
            command.round_id,
            actor_id=command.actor_id,
            choice=command.choice,
            credential_id=command.credential_id,
            expected_revision=command.expected_revision,
            idempotency_key=command.submission.idempotency_key,
        ),
        receipt,
        available,
    ))
    register(descriptor(
        "consensus.round.read",
        read_only,
        any_scope,
        idempotency_read_only,
        decode_check,
        lambda command, _: service.get_target(command.round_id),
        encode_target,
        available,
    ))

    def decode_sweep(envelope: CommandEnvelope) -> ConsensusSweepCommand:
        params = envelope.params
        reason = params["reason"]
        revision = params["expected_revision"]
        if not isinstance(params["round_id"], str) or not isinstance(reason, str):
            raise ValueError("round_id and reason must be strings")
        if revision is not None and (
            not isinstance(revision, int) or isinstance(revision, bool)
        ):
            raise ValueError("expected_revision must be an integer or null")
        return ConsensusSweepCommand(
            submission(envelope), params["round_id"], reason, revision
        )

    register(descriptor(
        "consensus.round.sweep",
        mutating,
        any_scope,
        domain_atomic_required,
        decode_sweep,
        lambda command, _: service.mark_timeout(
            command.round_id,
            command.reason,
            command.expected_revision,
            idempotency_key=command.submission.idempotency_key,
        ),
        receipt,
        available,
    ))

    def _decode_revision(params: Mapping[str, Any]) -> int | None:
        revision = params.get("expected_revision")
        if revision is not None and (
            not isinstance(revision, int) or isinstance(revision, bool)
        ):
            raise ValueError("expected_revision must be an integer or null")
        return revision

    def decode_ack(envelope: CommandEnvelope) -> ConsensusAckCommand:
        params = envelope.params
        return ConsensusAckCommand(
            submission(envelope), str(params["round_id"]), str(params["actor_id"]),
            _decode_revision(params), credential_id=envelope.credential_id,
        )

    def decode_nack(envelope: CommandEnvelope) -> ConsensusNackCommand:
        params = envelope.params
        return ConsensusNackCommand(
            submission(envelope), str(params["round_id"]), str(params["actor_id"]),
            str(params.get("nack_type", "block")), _decode_revision(params),
            credential_id=envelope.credential_id,
        )

    def decode_correct(envelope: CommandEnvelope) -> ConsensusCorrectCommand:
        params = envelope.params
        return ConsensusCorrectCommand(
            submission(envelope), str(params["round_id"]), str(params["actor_id"]),
            _decode_revision(params), credential_id=envelope.credential_id,
        )

    def decode_retract(envelope: CommandEnvelope) -> ConsensusRetractCommand:
        params = envelope.params
        return ConsensusRetractCommand(
            submission(envelope), str(params["round_id"]), str(params["actor_id"]),
            _decode_revision(params), credential_id=envelope.credential_id,
        )

    register(descriptor(
        "consensus.final_call.ack", mutating, any_scope, domain_atomic_required, decode_ack,
        lambda command, _: service.final_call_ack(
            command.round_id, actor_id=command.actor_id, ack=True,
            expected_revision=command.expected_revision, credential_id=command.credential_id,
            idempotency_key=command.submission.idempotency_key,
        ),
        receipt, available,
    ))
    register(descriptor(
        "consensus.final_call.nack", mutating, any_scope, domain_atomic_required, decode_nack,
        lambda command, _: service.final_call_ack(
            command.round_id, actor_id=command.actor_id, ack=False,
            expected_revision=command.expected_revision, credential_id=command.credential_id,
            idempotency_key=command.submission.idempotency_key, nack_type=command.nack_type,
        ),
        receipt, available,
    ))
    register(descriptor(
        "consensus.round.correct", mutating, any_scope, domain_atomic_required, decode_correct,
        lambda command, _: service.correction(
            command.round_id, actor_id=command.actor_id,
            expected_revision=command.expected_revision, credential_id=command.credential_id,
            idempotency_key=command.submission.idempotency_key,
        ),
        receipt, available,
    ))
    register(descriptor(
        "consensus.round.retract", mutating, any_scope, domain_atomic_required, decode_retract,
        lambda command, _: service.retraction(
            command.round_id, actor_id=command.actor_id,
            expected_revision=command.expected_revision, credential_id=command.credential_id,
            idempotency_key=command.submission.idempotency_key,
        ),
        receipt, available,
    ))

    if broker is not None:
        def decode_proposal_list(
            envelope: CommandEnvelope,
        ) -> ProposalListCommand:
            return ProposalListCommand(submission(envelope))

        def encode_proposals(
            results: Sequence[Any],
        ) -> Mapping[str, JsonValue]:
            result_rows = [
                {
                    "target_id": result.target_id,
                    "revision": result.revision,
                    "state": result.state,
                }
                for result in results
            ]
            return {"proposals": cast(JsonValue, result_rows)}

        register(descriptor(
            "governance.proposal.list",
            read_only,
            any_scope,
            idempotency_read_only,
            decode_proposal_list,
            lambda _command, _context: broker.list_targets(
                "consensus-round", None
            ),
            encode_proposals,
            available,
        ))

    if proposals is not None:
        def decode_proposal_add(
            envelope: CommandEnvelope,
        ) -> ProposalAddCommand:
            return ProposalAddCommand(
                submission=submission(envelope),
                subject=proposal_text(envelope, "subject"),
                from_peer=proposal_text(envelope, "from_peer"),
                impact=proposal_text(envelope, "impact"),
                rationale=proposal_text(envelope, "rationale"),
                text=proposal_text(envelope, "text"),
                verified_required=bool(envelope.params["verified_required"]),
            )

        def decode_proposal_vote(
            envelope: CommandEnvelope,
        ) -> ProposalVoteCommand:
            return ProposalVoteCommand(
                submission=submission(envelope),
                proposal_id=proposal_text(envelope, "proposal_id"),
                voter=proposal_text(envelope, "voter"),
                vote=proposal_text(envelope, "vote"),
                reason=proposal_text(envelope, "reason"),
                credential_id=envelope.credential_id,
            )

        def encode_proposal_add(
            result: ProposalAddResult,
        ) -> Mapping[str, JsonValue]:
            return {
                "round_id": result.round_id,
                "from_peer": result.from_peer,
                "impact": result.impact,
                "eligible_participants": result.eligible_participants,
                "receipt_id": result.receipt_id,
                "revision": result.revision,
            }

        def encode_proposal_vote(
            result: ProposalVoteResult,
        ) -> Mapping[str, JsonValue]:
            return {
                "round_id": result.round_id,
                "voter": result.voter,
                "choice": result.choice,
                "outcome": result.outcome,
                "agreed": result.agreed,
                "disagreed": result.disagreed,
                "escalation_reason": result.escalation_reason,
                "invariant_request_target_id": result.invariant_request_target_id,
                "revision": result.revision,
            }

        register(descriptor(
            "governance.proposal.create",
            mutating,
            any_scope,
            domain_atomic_required,
            decode_proposal_add,
            lambda command, _: proposals.add_proposal(
                subject=command.subject,
                from_peer=command.from_peer,
                impact=command.impact,
                rationale=command.rationale,
                text=command.text,
                verified_required=command.verified_required,
            ),
            encode_proposal_add,
            available,
        ))
        register(descriptor(
            "governance.proposal.vote",
            mutating,
            any_scope,
            domain_atomic_required,
            decode_proposal_vote,
            lambda command, _: proposals.vote_proposal(
                command.proposal_id,
                voter=command.voter,
                vote=command.vote,
                reason=command.reason,
                credential_id=command.credential_id,
            ),
            encode_proposal_vote,
            available,
        ))

    if arbiter is not None:
        def decode_arbiter_review(
            envelope: CommandEnvelope,
        ) -> ArbiterReviewCommand:
            return ArbiterReviewCommand(
                submission(envelope), str(envelope.params["round_id"])
            )

        register(descriptor(
            "consensus.arbiter.review",
            mutating,
            any_scope,
            domain_atomic_required,
            decode_arbiter_review,
            lambda command, _: arbiter.review(command.round_id),
            lambda result: dict(result),
            available,
        ))
