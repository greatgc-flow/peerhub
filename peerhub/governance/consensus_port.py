"""Structural port satisfied by both the legacy ConsensusService and the V2 ConsensusFacade.

Proposal and arbiter coordinators depend on this port, never on a concrete engine, so the
same code runs before and after the (user-gated) V2 activation.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Protocol

from peerhub.core.protocol import JsonValue
from peerhub.governance.contract import EffectIntent, MutationSubmission, TargetState


class ConsensusPort(Protocol):
    def get_target(self, round_id: str) -> TargetState | None: ...

    def resolution_decision_hash(
        self, state: Mapping[str, JsonValue], outcome: str
    ) -> str: ...

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
    ) -> MutationSubmission: ...

    def cast_vote(
        self,
        round_id: str,
        *,
        actor_id: str,
        choice: str,
        reason: str | None = None,
        expected_revision: int | None = None,
        credential_id: str | None = None,
    ) -> MutationSubmission: ...

    def request_escalation(
        self,
        round_id: str,
        reason: str,
        requester_id: str,
        tier: int,
        required_authority: str,
        expected_revision: int | None = None,
    ) -> MutationSubmission: ...

    def reject_on_dissent(
        self,
        round_id: str,
        *,
        rejected_by: str,
        basis: str,
        expected_revision: int | None = None,
    ) -> MutationSubmission: ...

    def resolve(
        self,
        round_id: str,
        outcome: str,
        resolved_by: str,
        basis: str,
        expected_revision: int | None = None,
        effect_intent: EffectIntent | None = None,
    ) -> MutationSubmission: ...

    def record_arbiter_opinion(
        self,
        round_id: str,
        *,
        request_target_id: str,
        opinion_target_id: str,
        actor_id: str,
    ) -> MutationSubmission | None: ...
