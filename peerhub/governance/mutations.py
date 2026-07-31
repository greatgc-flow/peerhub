"""Pure governed-mutation lifecycle reducers."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping

from peerhub.core.errors import (
    InvalidMutationError,
    StaleRevisionError,
)
from peerhub.core.protocol import JsonValue, canonical_json_bytes

from .contract import (
    EffectOutcome,
    EffectReceipt,
    MutationPlan,
    MutationRequest,
    OutboxEvent,
    OutboxState,
    TargetState,
    TransitionReceipt,
    TransitionStatus,
)


def mutation_payload(
    request: MutationRequest,
) -> Mapping[str, object]:
    """Return the semantic payload bound by command idempotency."""

    return {
        "actor_id": request.actor_id,
        "client_id": request.client_id,
        "command_type": request.command_type,
        "policy_revision": request.policy_revision,
        "target_id": request.target_id,
        "expected_revision": request.expected_revision,
        "operation": request.operation,
        "desired_state": request.desired_state,
        "effect_intent": {
            "kind": request.effect_intent.kind,
            "payload": request.effect_intent.payload,
        },
    }


def mutation_payload_digest(request: MutationRequest) -> str:
    """Return the canonical SHA-256 digest of mutation semantics."""

    return hashlib.sha256(
        canonical_json_bytes(mutation_payload(request))
    ).hexdigest()


def validate_expected_revision(
    request: MutationRequest,
    current: TargetState | None,
) -> int:
    """Validate CAS evidence and return the authoritative revision."""

    if current is not None and current.target_id != request.target_id:
        raise InvalidMutationError(
            "current target does not match the mutation request"
        )

    current_revision = 0 if current is None else current.revision
    if request.expected_revision != current_revision:
        raise StaleRevisionError(
            request.target_id,
            request.expected_revision,
            current_revision,
        )
    return current_revision


def plan_mutation(
    request: MutationRequest,
    current: TargetState | None,
    *,
    plan_id: str,
    planned_at: int,
) -> MutationPlan:
    """Reduce request and current state into a CAS-bound plan."""

    previous_revision = validate_expected_revision(
        request,
        current,
    )
    return MutationPlan(
        plan_id=plan_id,
        request_id=request.request_id,
        request_digest=mutation_payload_digest(request),
        target_id=request.target_id,
        previous_revision=previous_revision,
        next_revision=previous_revision + 1,
        next_state=request.desired_state,
        effect_intent=request.effect_intent,
        planned_at=planned_at,
    )


def apply_mutation_plan(
    current: TargetState | None,
    plan: MutationPlan,
    *,
    updated_at: int,
) -> TargetState:
    """Return the next target state without performing I/O."""

    current_revision = 0 if current is None else current.revision
    if current is not None and current.target_id != plan.target_id:
        raise InvalidMutationError(
            "plan target does not match current target"
        )
    if current_revision != plan.previous_revision:
        raise InvalidMutationError(
            "plan previous revision is no longer current"
        )

    return TargetState(
        target_id=plan.target_id,
        revision=plan.next_revision,
        state=plan.next_state,
        updated_at=updated_at,
    )


def build_transition_receipt(
    plan: MutationPlan,
    *,
    receipt_id: str,
    outbox_event_id: str,
    committed_at: int,
    evidence_refs: tuple[str, ...] = (),
) -> TransitionReceipt:
    """Return immutable evidence for a committed transition."""

    return TransitionReceipt(
        receipt_id=receipt_id,
        request_id=plan.request_id,
        plan_id=plan.plan_id,
        target_id=plan.target_id,
        previous_revision=plan.previous_revision,
        next_revision=plan.next_revision,
        status=TransitionStatus.COMMITTED_ENFORCEMENT_PENDING,
        committed_at=committed_at,
        outbox_event_id=outbox_event_id,
        evidence_refs=evidence_refs,
    )


def build_outbox_event(
    plan: MutationPlan,
    receipt: TransitionReceipt,
    *,
    event_id: str,
    created_at: int,
) -> OutboxEvent:
    """Return the durable effect intent for a committed plan."""

    if receipt.plan_id != plan.plan_id:
        raise InvalidMutationError(
            "receipt does not belong to the mutation plan"
        )
    if receipt.outbox_event_id != event_id:
        raise InvalidMutationError(
            "receipt does not name the outbox event"
        )

    payload: Mapping[str, JsonValue] = {
        "plan_id": plan.plan_id,
        "target_id": plan.target_id,
        "target_revision": plan.next_revision,
        "effect_kind": plan.effect_intent.kind,
        "effect_payload": plan.effect_intent.payload,
    }
    return OutboxEvent(
        event_id=event_id,
        request_id=plan.request_id,
        transition_receipt_id=receipt.receipt_id,
        topic="governance.effect.requested",
        payload=payload,
        state=OutboxState.PENDING,
        created_at=created_at,
    )


def build_effect_receipt(
    event: OutboxEvent,
    *,
    effect_receipt_id: str,
    attempt_id: str,
    owner_id: str,
    outcome: EffectOutcome,
    completed_at: int,
    evidence_refs: tuple[str, ...] = (),
) -> EffectReceipt:
    """Return one immutable terminal effect receipt."""

    if event.state is not OutboxState.CLAIMED:
        raise InvalidMutationError(
            "effect result requires a claimed outbox event"
        )
    if (
        event.claimed_by != owner_id
        or event.claim_attempt_id != attempt_id
    ):
        raise InvalidMutationError(
            "effect result does not match the durable claim"
        )

    return EffectReceipt(
        effect_receipt_id=effect_receipt_id,
        request_id=event.request_id,
        outbox_event_id=event.event_id,
        attempt_id=attempt_id,
        owner_id=owner_id,
        outcome=outcome,
        completed_at=completed_at,
        evidence_refs=evidence_refs,
    )
