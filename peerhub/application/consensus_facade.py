"""One consensus entry point for API, proposals and arbiter callers.

Before activation every call goes to the legacy ``ConsensusService`` exactly as
before. After the atomic V2 activation legacy consensus writes are fenced at the
storage layer, so new rounds are created through ``ConsensusShell`` and calls on
V2 rounds are routed to it; calls on legacy rounds keep using the legacy service
(reads, recovery). Operations the shell does not implement for V2 rounds fail
closed with ``UnsupportedV2Operation`` instead of touching fenced storage.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Callable, Sequence

from peerhub.core.errors import InvalidMutationError, RecordNotFoundError
from peerhub.core.protocol import JsonValue
from peerhub.governance.consensus import ConsensusService
from peerhub.governance.contract import EffectIntent, EffectReceipt, MutationSubmission, TargetState
from peerhub.governance.consensus_shell import ConsensusShell

V2_SCHEMA = "peerhub.consensus-round.v2"
PROPOSAL_ACTION = "governance.proposal.create"
TIMEOUT_SWEEP_PRINCIPAL = "system:consensus-sweep"


class UnsupportedV2Operation(InvalidMutationError):
    """The operation has no V2 implementation yet (fail closed)."""


class ConsensusFacade:
    def __init__(
        self,
        legacy: ConsensusService,
        shell: ConsensusShell,
        *,
        is_v2_active: Callable[[], bool],
        policy_provider: Any,
    ) -> None:
        self._legacy = legacy
        self._shell = shell
        self._is_v2_active = is_v2_active
        # ConsensusPolicyProvider (or any object with round_config(...)): the
        # frozen round policy comes from resolved configuration, never literals.
        self._policy_provider = policy_provider

    # -- reads / pure helpers -------------------------------------------------
    def get_target(self, round_id: str) -> TargetState | None:
        return self._legacy.get_target(round_id)

    def resolution_decision_hash(self, state: Mapping[str, JsonValue], outcome: str) -> str:
        return self._legacy.resolution_decision_hash(state, outcome)

    def _is_v2_round(self, round_id: str) -> bool:
        """True for V2 rounds; a legacy round mutated after activation is held.

        After activation legacy consensus state is fenced at the storage layer
        (design 7.2: a V1 round is never guessed/upcast implicitly), so a
        mutation on one fails closed here with an operator-facing message.
        """
        target = self._legacy.get_target(round_id)
        if target is None:
            raise RecordNotFoundError("consensus-round", round_id)
        if target.state.get("schema") == V2_SCHEMA:
            return True
        if self._is_v2_active():
            raise UnsupportedV2Operation(
                f"consensus round {round_id!r} is a legacy V1 round held after V2 "
                "activation; it needs an explicit administrator migration"
            )
        return False

    # -- creation ---------------------------------------------------------------
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
        verified_required: bool = False,
        origin: str | None = None,
        action: str | None = None,
        idempotency_key: str | None = None,
    ) -> MutationSubmission:
        if not self._is_v2_active():
            return self._legacy.propose(
                round_id=round_id, title=title, question=question, body=body,
                proposer_id=proposer_id, required_participants=required_participants,
                eligible_participants=eligible_participants, risk=risk,
                source_hash=source_hash, verified_required=verified_required,
            )
        resolved_action = action or "consensus.round.propose"
        config = self._policy_provider.round_config(
            resolved_action, risk, len(tuple(required_participants)), len(tuple(eligible_participants))
        )
        return self._shell.propose_v2(
            round_id, title, question, body, proposer_id, required_participants,
            eligible_participants, risk, source_hash, config,
            origin=origin, action=action, verified_required=verified_required,
            idempotency_key=idempotency_key,
        )

    # -- routed mutations ---------------------------------------------------------
    def cast_vote(self, round_id: str, *, actor_id: str, choice: str, reason: str | None = None,
                  expected_revision: int | None = None, credential_id: str | None = None,
                  idempotency_key: str | None = None) -> MutationSubmission:
        if self._is_v2_round(round_id):
            return self._shell.cast_vote(
                round_id, actor_id, choice, expected_revision, credential_id,
                idempotency_key, reason=reason,
            )
        return self._legacy.cast_vote(round_id, actor_id=actor_id, choice=choice, reason=reason,
                                      expected_revision=expected_revision, credential_id=credential_id)

    def final_call_ack(self, round_id: str, *, actor_id: str, ack: bool,
                       expected_revision: int | None = None, credential_id: str | None = None,
                       idempotency_key: str | None = None, nack_type: str = "block") -> MutationSubmission:
        if self._is_v2_round(round_id):
            if ack:
                return self._shell.final_call_ack(
                    round_id, actor_id, expected_revision, credential_id, idempotency_key
                )
            return self._shell.final_call_nack(
                round_id, actor_id, nack_type, credential_id, idempotency_key,
                expected_revision=expected_revision,
            )
        return self._legacy.final_call_ack(round_id, actor_id=actor_id, ack=ack,
                                           expected_revision=expected_revision, credential_id=credential_id)

    def request_escalation(self, round_id: str, reason: str, requester_id: str, tier: int,
                           required_authority: str, expected_revision: int | None = None,
                           credential_id: str | None = None, idempotency_key: str | None = None) -> MutationSubmission:
        if self._is_v2_round(round_id):
            return self._shell.request_escalation(
                round_id, reason, requester_id, tier, required_authority,
                idempotency_key=idempotency_key, credential_id=credential_id,
                expected_revision=expected_revision,
            )
        return self._legacy.request_escalation(round_id, reason, requester_id, tier,
                                               required_authority, expected_revision)

    def reject_on_dissent(self, round_id: str, *, rejected_by: str, basis: str,
                          expected_revision: int | None = None, credential_id: str | None = None,
                          idempotency_key: str | None = None) -> MutationSubmission:
        if self._is_v2_round(round_id):
            return self._shell.reject_on_dissent(
                round_id, rejected_by, basis, idempotency_key=idempotency_key,
                credential_id=credential_id, expected_revision=expected_revision,
            )
        return self._legacy.reject_on_dissent(round_id, rejected_by=rejected_by, basis=basis,
                                              expected_revision=expected_revision)

    def resolve(self, round_id: str, outcome: str, resolved_by: str, basis: str,
                expected_revision: int | None = None, effect_intent: EffectIntent | None = None,
                credential_id: str | None = None, idempotency_key: str | None = None) -> MutationSubmission:
        if self._is_v2_round(round_id):
            if effect_intent is not None:
                raise UnsupportedV2Operation(
                    "V2 rounds build their own approval effect; effect_intent must be None"
                )
            return self._shell.resolve(
                round_id, outcome, resolved_by, basis, idempotency_key=idempotency_key,
                credential_id=credential_id, expected_revision=expected_revision,
            )
        return self._legacy.resolve(round_id, outcome, resolved_by, basis, expected_revision, effect_intent)

    def abandon(self, round_id: str, reason_code: str, reason: str, abandoned_by: str,
                expected_revision: int | None = None, credential_id: str | None = None,
                idempotency_key: str | None = None) -> MutationSubmission:
        if self._is_v2_round(round_id):
            return self._shell.abandon(
                round_id, abandoned_by, idempotency_key=idempotency_key,
                credential_id=credential_id, expected_revision=expected_revision,
            )
        return self._legacy.abandon(round_id, reason_code, reason, abandoned_by, expected_revision)

    def mark_timeout(self, round_id: str, reason: str, expected_revision: int | None = None,
                     idempotency_key: str | None = None) -> MutationSubmission:
        if self._is_v2_round(round_id):
            # Timeouts are system-driven (sweep); the sweep principal is allowlisted.
            return self._shell.mark_timeout(
                round_id, TIMEOUT_SWEEP_PRINCIPAL, idempotency_key=idempotency_key,
                expected_revision=expected_revision,
            )
        return self._legacy.mark_timeout(round_id, reason, expected_revision)

    def correction(self, round_id: str, *, actor_id: str, expected_revision: int | None = None,
                   credential_id: str | None = None, idempotency_key: str | None = None) -> MutationSubmission:
        if self._is_v2_round(round_id):
            return self._shell.correction(
                round_id, actor_id, idempotency_key=idempotency_key,
                credential_id=credential_id, expected_revision=expected_revision,
            )
        raise UnsupportedV2Operation("correction exists only for V2 rounds")

    def retraction(self, round_id: str, *, actor_id: str, expected_revision: int | None = None,
                   credential_id: str | None = None, idempotency_key: str | None = None) -> MutationSubmission:
        if self._is_v2_round(round_id):
            return self._shell.retraction(
                round_id, actor_id, idempotency_key=idempotency_key,
                credential_id=credential_id, expected_revision=expected_revision,
            )
        raise UnsupportedV2Operation("retraction exists only for V2 rounds")

    def record_arbiter_opinion(self, round_id: str, *, request_target_id: str,
                               opinion_target_id: str, actor_id: str,
                               idempotency_key: str | None = None) -> MutationSubmission | None:
        if self._is_v2_round(round_id):
            return self._shell.record_arbiter_opinion(
                round_id, request_target_id=request_target_id,
                opinion_target_id=opinion_target_id, actor_id=actor_id,
                idempotency_key=idempotency_key,
            )
        return self._legacy.record_arbiter_opinion(
            round_id, request_target_id=request_target_id,
            opinion_target_id=opinion_target_id, actor_id=actor_id,
        )

    def process_consensus_effects(self, round_id: str, *, owner_id: str | None = None) -> Sequence[EffectReceipt]:
        if self._is_v2_round(round_id):
            return self._shell.process_consensus_effects(round_id, owner_id=owner_id)
        return self._legacy.process_consensus_effects(round_id, owner_id=owner_id)
