"""Policy-driven rules and routing for governance proposals.

This module provides pure functional rules for deciding proposal outcomes,
routing effect generation, and building atomic finalization boundaries.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict

from peerhub.governance.invariant_requests import RATIFIED_INVARIANT_EFFECT_KIND


class ProposalDisposition(Enum):
    APPROVED = "approved"
    REJECTED = "rejected"
    ESCALATED = "escalated"
    FINAL_CALL = "final_call"
    HOLD = "hold"
    PENDING = "pending"


@dataclass(frozen=True)
class ProposalInputs:
    resolved_recovery: bool
    existing_escalation: bool
    eligible_count: int
    gate_closed_any_eligible: bool
    eligible_dissent: bool
    all_required_agree: bool
    independent_agreement: bool
    proposer_only: bool
    high_risk: bool


class EffectRoutingError(Exception):
    """Raised when an effect cannot be correctly routed to a materializer."""
    def __init__(self, message: str, hold: bool = True):
        super().__init__(message)
        self.hold = hold


@dataclass(frozen=True)
class ApprovalSubmission:
    state_transition: Dict[str, Any]
    receipt_key: str
    effect_outbox_entry: Dict[str, Any]


class ProposalRule(Enum):
    """Which precedence rule of design 6.1 fired (the reason behind a disposition)."""

    RESOLVED_RECOVERY = "resolved_recovery"
    EXISTING_ESCALATION = "existing_escalation"
    TOO_FEW_VOTERS = "too_few_voters"
    GATE_CLOSED = "gate_closed"
    ELIGIBLE_DISSENT = "eligible_dissent"
    ALL_REQUIRED_AGREE = "all_required_agree"
    SELF_FINALIZATION = "self_finalization"
    NONE = "none"


_RULE_DISPOSITION_LOW_RISK = {
    ProposalRule.RESOLVED_RECOVERY: ProposalDisposition.APPROVED,
    ProposalRule.EXISTING_ESCALATION: ProposalDisposition.ESCALATED,
    ProposalRule.TOO_FEW_VOTERS: ProposalDisposition.ESCALATED,
    # Legacy reconciliation escalates on a mid-round gate closure; the design
    # preserves the current proposal reconciliation policy.
    ProposalRule.GATE_CLOSED: ProposalDisposition.ESCALATED,
    ProposalRule.ELIGIBLE_DISSENT: ProposalDisposition.REJECTED,
    ProposalRule.ALL_REQUIRED_AGREE: ProposalDisposition.APPROVED,
    ProposalRule.SELF_FINALIZATION: ProposalDisposition.ESCALATED,
    ProposalRule.NONE: ProposalDisposition.HOLD,
}


def decide_proposal_rule(inputs: ProposalInputs) -> ProposalRule:
    """Return the first matching rule of the 6.1 precedence chain."""
    if inputs.resolved_recovery:
        return ProposalRule.RESOLVED_RECOVERY
    if inputs.existing_escalation:
        return ProposalRule.EXISTING_ESCALATION
    if inputs.eligible_count < 2:
        return ProposalRule.TOO_FEW_VOTERS
    if inputs.gate_closed_any_eligible:
        return ProposalRule.GATE_CLOSED
    if inputs.eligible_dissent:
        return ProposalRule.ELIGIBLE_DISSENT
    if inputs.all_required_agree and inputs.independent_agreement:
        return ProposalRule.ALL_REQUIRED_AGREE
    if inputs.proposer_only:
        return ProposalRule.SELF_FINALIZATION
    return ProposalRule.NONE


def decide_proposal_disposition(inputs: ProposalInputs) -> ProposalDisposition:
    """
    Decide the proposal disposition based on strict policy precedence:
    1. Resolved recovery
    2. Existing escalation
    3. Eligible count < 2 (escalate/too_few)
    4. Gate closure (escalate, legacy-preserving)
    5. Eligible dissent -> reject (UNCONDITIONAL)
    6. All required agree + independent agreement -> final_call when high_risk else approve
    7. Proposer-only self-finalization -> escalate
    """
    rule = decide_proposal_rule(inputs)
    if rule is ProposalRule.ALL_REQUIRED_AGREE and inputs.high_risk:
        return ProposalDisposition.FINAL_CALL
    return _RULE_DISPOSITION_LOW_RISK[rule]


GENERIC_EFFECT_KINDS = frozenset(
    {"consensus.resolved", "consensus.abandoned", "consensus.noop"}
)


def route_effect_kind(effect_kind: str, materializer: str) -> None:
    """
    Route effect kind to the appropriate materializer.
    Raises EffectRoutingError for unauthorized claims, unknown kinds or
    unknown materializers (hold-and-error, fail closed).
    """
    if effect_kind == RATIFIED_INVARIANT_EFFECT_KIND:
        if materializer == "invariant":
            return
        raise EffectRoutingError(f"Materializer {materializer} cannot handle {effect_kind}", hold=True)
    if effect_kind in GENERIC_EFFECT_KINDS and materializer == "generic":
        return
    raise EffectRoutingError(
        f"Effect kind {effect_kind!r} cannot be claimed by materializer {materializer!r}", hold=True
    )


def build_approval_submission(approved_snapshot: Dict[str, Any], effect_intent: Dict[str, Any]) -> ApprovalSubmission:
    """
    Build a single atomic submission for an approved proposal.
    State transition, receipt key and effect outbox entry are bound in one
    object so they commit together. Rejects a missing or mismatching hash.
    """
    snapshot_hash = approved_snapshot.get("hash")
    if not snapshot_hash or snapshot_hash != effect_intent.get("snapshot_hash"):
        raise ValueError("snapshot hash mismatch")
    round_id = approved_snapshot.get("round_id", "")
    return ApprovalSubmission(
        state_transition={"round_id": round_id, "to": "approved", "snapshot_hash": snapshot_hash},
        receipt_key=f"approval:{round_id}:{snapshot_hash}",
        effect_outbox_entry=dict(effect_intent),
    )
