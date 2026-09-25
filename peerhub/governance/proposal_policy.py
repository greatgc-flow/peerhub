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


def decide_proposal_disposition(inputs: ProposalInputs) -> ProposalDisposition:
    """
    Decide the proposal disposition based on strict policy precedence:
    1. Resolved recovery
    2. Existing escalation
    3. Eligible count < 2 (escalate/too_few)
    4. Gate closure
    5. Eligible dissent -> reject (UNCONDITIONAL)
    6. All required agree + independent agreement -> final_call when high_risk else approve
    7. Proposer-only self-finalization -> escalate
    """
    raise NotImplementedError("RED phase")


def route_effect_kind(effect_kind: str, materializer: str) -> None:
    """
    Route effect kind to the appropriate materializer.
    Raises EffectRoutingError for unauthorized claims or unknown kinds.
    """
    raise NotImplementedError("RED phase")


def build_approval_submission(approved_snapshot: Dict[str, Any], effect_intent: Dict[str, Any]) -> ApprovalSubmission:
    """
    Build a single atomic submission for an approved proposal.
    Rejects if effect_intent's snapshot hash does not match the approved snapshot hash.
    """
    raise NotImplementedError("RED phase")
