"""Unit tests for pure governance reducers."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

import peerhub.governance.mutations as mutations
from peerhub.core.errors import StaleRevisionError
from peerhub.core.protocol import CommandID
from peerhub.governance.contract import (
    EffectIntent,
    EffectOutcome,
    MutationRequest,
    OutboxState,
    TargetState,
)
from peerhub.governance.mutations import (
    apply_mutation_plan,
    build_effect_receipt,
    build_outbox_event,
    build_transition_receipt,
    mutation_payload_digest,
    plan_mutation,
)


def _request(
    *,
    expected_revision: int = 0,
    desired_state: dict[str, object] | None = None,
) -> MutationRequest:
    return MutationRequest(
        request_id="request-unit",
        command_id=CommandID("command-unit"),
        correlation_id="correlation-unit",
        client_id="client-unit",
        command_type="governance.mutate",
        idempotency_key="key-unit",
        actor_id="actor-unit",
        policy_revision="policy-r1",
        target_id="target-unit",
        expected_revision=expected_revision,
        operation="SET",
        desired_state=desired_state or {"a": 1, "b": 2},
        effect_intent=EffectIntent(
            kind="TEST_EFFECT",
            payload={"enabled": True},
        ),
    )


def test_payload_digest_is_canonical_across_key_order() -> None:
    first = _request(desired_state={"a": 1, "b": 2})
    second = _request(desired_state={"b": 2, "a": 1})

    assert mutation_payload_digest(first) == (
        mutation_payload_digest(second)
    )


def test_plan_and_apply_create_first_revision() -> None:
    request = _request()

    plan = plan_mutation(
        request,
        None,
        plan_id="plan-unit",
        planned_at=10,
    )
    target = apply_mutation_plan(
        None,
        plan,
        updated_at=10,
    )

    assert plan.previous_revision == 0
    assert plan.next_revision == 1
    assert target.revision == 1
    assert dict(target.state) == {"a": 1, "b": 2}


def test_plan_rejects_stale_expected_revision() -> None:
    request = _request(expected_revision=1)
    current = TargetState(
        target_id="target-unit",
        revision=2,
        state={"a": 0},
        updated_at=5,
    )

    with pytest.raises(StaleRevisionError) as raised:
        plan_mutation(
            request,
            current,
            plan_id="plan-stale",
            planned_at=10,
        )

    assert raised.value.expected_revision == 1
    assert raised.value.current_revision == 2


def test_receipt_outbox_and_effect_receipt_are_derived() -> None:
    plan = plan_mutation(
        _request(),
        None,
        plan_id="plan-derived",
        planned_at=10,
    )
    transition = build_transition_receipt(
        plan,
        receipt_id="receipt-derived",
        outbox_event_id="event-derived",
        committed_at=10,
    )
    event = build_outbox_event(
        plan,
        transition,
        event_id="event-derived",
        created_at=10,
    )

    assert event.state is OutboxState.PENDING
    assert "next_state" not in event.payload
    assert event.payload["target_revision"] == 1

    claimed = type(event)(
        event_id=event.event_id,
        request_id=event.request_id,
        transition_receipt_id=(
            event.transition_receipt_id
        ),
        topic=event.topic,
        payload=event.payload,
        state=OutboxState.CLAIMED,
        created_at=event.created_at,
        claimed_by="owner-unit",
        claim_attempt_id="attempt-unit",
        claimed_at=11,
    )
    effect = build_effect_receipt(
        claimed,
        effect_receipt_id="effect-receipt-unit",
        attempt_id="attempt-unit",
        owner_id="owner-unit",
        outcome=EffectOutcome.EFFECT_SUCCEEDED,
        completed_at=12,
    )

    assert effect.outbox_event_id == event.event_id
    assert effect.outcome is EffectOutcome.EFFECT_SUCCEEDED


def test_mutation_reducer_has_no_sqlite_or_persistence_import() -> None:
    module_path = Path(str(mutations.__file__))
    tree = ast.parse(module_path.read_text(encoding="utf-8"))

    imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imports.append(node.module or "")

    assert "sqlite3" not in imports
    assert not any(
        name.startswith("peerhub.persistence")
        for name in imports
    )
