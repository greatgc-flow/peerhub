"""Integration tests for the governance dual-write shadow-write."""

from __future__ import annotations

import sqlite3
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest

from peerhub.core.protocol import PROTOCOL_MAJOR, PROTOCOL_MINOR, SCHEMA_VERSION
from peerhub.governance.contract import (
    MutationRequest,
    MutationPlan,
    OutboxEvent,
    OutboxState,
    TransitionReceipt,
    TransitionStatus,
    EffectIntent,
)
from peerhub.persistence.sqlite import SqliteStateStore


@pytest.fixture
def store(tmp_path: Path) -> Iterator[SqliteStateStore]:
    state_store = SqliteStateStore(
        tmp_path / "dual_write.sqlite3",
        workspace_home_id="workspace-test",
    )
    state_store.initialize()
    try:
        yield state_store
    finally:
        state_store.close()


def _get_effect_delivery(
    store: SqliteStateStore, event_id: str
) -> sqlite3.Row | None:
    # We use a direct query because the table is currently shadow-only
    with store.unit_of_work() as unit:
        # pyright: ignore[reportPrivateUsage]
        return unit._db().execute(
            "SELECT * FROM effect_deliveries WHERE event_id = ?",
            (event_id,),
        ).fetchone()


def test_governance_event_dual_writes_to_effect_deliveries(
    store: SqliteStateStore,
) -> None:
    event_id = str(uuid.uuid4())
    request_id = "req-01"
    receipt_id = "receipt-01"
    topic = "governance.effect.requested"

    request = MutationRequest(
        request_id=request_id,
        command_id="cmd-01",
        correlation_id="corr-01",
        actor_id="actor-01",
        client_id="client-01",
        command_type="peer.ask",
        idempotency_key="idemp-01",
        policy_revision="1",
        target_id="target-01",
        expected_revision=0,
        operation="noop",
        desired_state={},
        effect_intent=EffectIntent(kind="foo", payload={}),
    )

    plan = MutationPlan(
        plan_id="plan-01",
        request_id=request_id,
        request_digest="digest1",
        target_id="target-01",
        previous_revision=0,
        next_revision=1,
        next_state={},
        effect_intent=EffectIntent(kind="foo", payload={}),
        planned_at=100,
    )

    receipt = TransitionReceipt(
        receipt_id=receipt_id,
        request_id=request_id,
        plan_id="plan-01",
        target_id="target-01",
        previous_revision=0,
        next_revision=1,
        status=TransitionStatus.COMMITTED_ENFORCEMENT_PENDING,
        committed_at=100,
        outbox_event_id=event_id,
    )

    event = OutboxEvent(
        event_id=event_id,
        protocol_major=PROTOCOL_MAJOR,
        protocol_minor=PROTOCOL_MINOR,
        schema_version=SCHEMA_VERSION,
        correlation_id="corr-01",
        occurred_at=100,
        event_kind="governance.effect.requested",
        payload={"dummy": "data"},
        state=OutboxState.PENDING,
        created_at=100,
        request_id=request_id,
        transition_receipt_id=receipt_id,
        topic=topic,
    )

    with store.unit_of_work() as unit:
        unit.governance.add_mutation_request(request, "digest1", 100)
        unit.governance.add_mutation_plan(plan)
        unit.governance.add_transition_receipt(receipt)
        unit.add_outbox_event(event)
        unit.commit()

    delivery = _get_effect_delivery(store, event_id)
    assert delivery is not None
    assert delivery["event_id"] == event_id
    assert delivery["request_id"] == request_id
    assert delivery["transition_receipt_id"] == receipt_id
    assert delivery["topic"] == topic
    assert delivery["claimed_by"] is None
    assert delivery["claim_attempt_id"] is None
    assert delivery["claimed_at"] is None


def test_governance_event_claim_mirrors_to_effect_deliveries(
    store: SqliteStateStore,
) -> None:
    event_id = str(uuid.uuid4())
    
    request_id = "req-02"
    receipt_id = "receipt-02"

    request = MutationRequest(
        request_id=request_id,
        command_id="cmd-02",
        correlation_id="corr-02",
        actor_id="actor-02",
        client_id="client-02",
        command_type="peer.ask",
        idempotency_key="idemp-02",
        policy_revision="1",
        target_id="target-02",
        expected_revision=0,
        operation="noop",
        desired_state={},
        effect_intent=EffectIntent(kind="foo", payload={}),
    )

    plan = MutationPlan(
        plan_id="plan-02",
        request_id=request_id,
        request_digest="digest2",
        target_id="target-02",
        previous_revision=0,
        next_revision=1,
        next_state={},
        effect_intent=EffectIntent(kind="foo", payload={}),
        planned_at=200,
    )

    receipt = TransitionReceipt(
        receipt_id=receipt_id,
        request_id=request_id,
        plan_id="plan-02",
        target_id="target-02",
        previous_revision=0,
        next_revision=1,
        status=TransitionStatus.COMMITTED_ENFORCEMENT_PENDING,
        committed_at=200,
        outbox_event_id=event_id,
    )

    event = OutboxEvent(
        event_id=event_id,
        protocol_major=PROTOCOL_MAJOR,
        protocol_minor=PROTOCOL_MINOR,
        schema_version=SCHEMA_VERSION,
        correlation_id="corr-02",
        occurred_at=200,
        event_kind="governance.effect.requested",
        payload={},
        state=OutboxState.PENDING,
        created_at=200,
        request_id=request_id,
        transition_receipt_id=receipt_id,
        topic="governance.effect.requested",
    )

    with store.unit_of_work() as unit:
        unit.governance.add_mutation_request(request, "digest2", 200)
        unit.governance.add_mutation_plan(plan)
        unit.governance.add_transition_receipt(receipt)
        unit.add_outbox_event(event)
        unit.commit()

    with store.unit_of_work() as unit:
        claimed = unit.claim_outbox_event(
            event_id,
            owner_id="owner-01",
            attempt_id="attempt-01",
            claimed_at=210,
        )
        assert claimed is not None
        unit.commit()

    delivery = _get_effect_delivery(store, event_id)
    assert delivery is not None
    assert delivery["claimed_by"] == "owner-01"
    assert delivery["claim_attempt_id"] == "attempt-01"
    assert delivery["claimed_at"] == 210


def test_dispatch_event_does_not_dual_write_to_effect_deliveries(
    store: SqliteStateStore,
) -> None:
    event_id = str(uuid.uuid4())
    
    # Missing transition_receipt_id (and topic)
    event = OutboxEvent(
        event_id=event_id,
        protocol_major=PROTOCOL_MAJOR,
        protocol_minor=PROTOCOL_MINOR,
        schema_version=SCHEMA_VERSION,
        correlation_id="corr-03",
        occurred_at=300,
        event_kind="dispatch.attempt.started",
        payload={},
        state=OutboxState.PENDING,
        created_at=300,
    )

    with store.unit_of_work() as unit:
        unit.add_outbox_event(event)
        unit.commit()

    delivery = _get_effect_delivery(store, event_id)
    assert delivery is None

    # Claiming a dispatch event should succeed without failing (the SQL UPDATE simply matches 0 rows)
    with store.unit_of_work() as unit:
        claimed = unit.claim_outbox_event(
            event_id,
            owner_id="owner-dispatch",
            attempt_id="attempt-dispatch",
            claimed_at=310,
        )
        assert claimed is not None
        unit.commit()
    
    # And there should still be no delivery row created
    delivery_after_claim = _get_effect_delivery(store, event_id)
    assert delivery_after_claim is None
