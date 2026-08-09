"""Integration tests for the guarded effect-delivery completion primitive."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from peerhub.governance.broker import GovernanceBroker
from peerhub.governance.contract import (
    EffectIntent,
    EffectOutcome,
    EffectReceipt,
    MutationRequest,
    OutboxState,
)
from peerhub.persistence.sqlite import SqliteStateStore
from tests.fakes import FakeClock, FakeIdSource


@pytest.fixture
def store(tmp_path: Path) -> Iterator[SqliteStateStore]:
    state_store = SqliteStateStore(
        tmp_path / "effect_delivery_completion.sqlite3",
        workspace_home_id="workspace-effect-delivery-completion",
    )
    state_store.initialize()
    try:
        yield state_store
    finally:
        state_store.close()


def _request() -> MutationRequest:
    return MutationRequest(
        request_id="request-complete-01",
        command_id="command-complete-01",
        correlation_id="correlation-complete-01",
        client_id="client-complete-01",
        command_type="governance.complete.test",
        idempotency_key="idempotency-complete-01",
        actor_id="actor-complete-01",
        policy_revision="policy-complete-01",
        target_id="target-complete-01",
        expected_revision=0,
        operation="set",
        desired_state={"enabled": True},
        effect_intent=EffectIntent(
            kind="test.effect",
            payload={"enabled": True},
        ),
    )


def _seed_delivery(store: SqliteStateStore) -> tuple[str, str]:
    broker = GovernanceBroker(
        store,
        clock=FakeClock([100]),
        ids=FakeIdSource(
            (
                "plan-complete-01",
                "receipt-complete-01",
                "event-complete-01",
            )
        ),
    )
    submission = broker.submit(_request())
    return (
        submission.receipt.outbox_event_id,
        submission.receipt.request_id,
    )


def _receipt(
    event_id: str,
    request_id: str,
    *,
    receipt_id: str = "effect-receipt-complete-01",
    owner_id: str = "owner-one",
    attempt_id: str = "attempt-one",
    outcome: EffectOutcome = EffectOutcome.EFFECT_SUCCEEDED,
) -> EffectReceipt:
    return EffectReceipt(
        effect_receipt_id=receipt_id,
        request_id=request_id,
        outbox_event_id=event_id,
        attempt_id=attempt_id,
        owner_id=owner_id,
        outcome=outcome,
        completed_at=300,
        evidence_refs=("evidence-complete-01",),
    )


def _claim(
    store: SqliteStateStore,
    event_id: str,
    *,
    owner_id: str = "owner-one",
    attempt_id: str = "attempt-one",
) -> None:
    with store.unit_of_work() as unit:
        claimed = unit.claim_effect_delivery(
            event_id,
            owner_id,
            attempt_id,
            200,
        )
        assert claimed is not None
        unit.commit()


def test_complete_effect_delivery_succeeds(
    store: SqliteStateStore,
) -> None:
    event_id, request_id = _seed_delivery(store)
    _claim(store, event_id)
    receipt = _receipt(event_id, request_id)

    with store.unit_of_work() as unit:
        completed = unit.complete_effect_delivery(receipt)
        unit.commit()

    assert completed is True
    with store.unit_of_work() as unit:
        persisted_receipt = unit.get_effect_receipt(event_id)
        delivery = unit.get_effect_delivery(event_id)
        legacy = unit.get_outbox_event(event_id)
    assert persisted_receipt == receipt
    assert delivery is not None
    assert delivery.state is OutboxState.CONSUMED
    assert legacy is not None
    assert legacy.state is OutboxState.CONSUMED


def test_complete_effect_delivery_rejects_wrong_owner(
    store: SqliteStateStore,
) -> None:
    event_id, request_id = _seed_delivery(store)
    _claim(store, event_id)
    wrong_owner_receipt = _receipt(
        event_id,
        request_id,
        owner_id="owner-two",
    )

    with store.unit_of_work() as unit:
        completed = unit.complete_effect_delivery(wrong_owner_receipt)
        unit.commit()

    assert completed is False
    with store.unit_of_work() as unit:
        persisted_receipt = unit.get_effect_receipt(event_id)
        delivery = unit.get_effect_delivery(event_id)
    assert persisted_receipt is None
    assert delivery is not None
    assert delivery.state is OutboxState.CLAIMED
    assert delivery.claimed_by == "owner-one"
    assert delivery.claim_attempt_id == "attempt-one"


def test_complete_effect_delivery_retry_is_idempotent(
    store: SqliteStateStore,
) -> None:
    event_id, request_id = _seed_delivery(store)
    _claim(store, event_id)
    receipt = _receipt(event_id, request_id)

    with store.unit_of_work() as unit:
        first = unit.complete_effect_delivery(receipt)
        unit.commit()
    with store.unit_of_work() as unit:
        second = unit.complete_effect_delivery(receipt)
        unit.commit()

    assert first is True
    assert second is True
    with store.unit_of_work() as unit:
        # pyright: ignore[reportPrivateUsage]
        receipt_count = unit._db().execute(
            "SELECT COUNT(*) FROM effect_receipts WHERE outbox_event_id = ?",
            (event_id,),
        ).fetchone()[0]
    assert receipt_count == 1


def test_complete_effect_delivery_rejects_unclaimed_delivery(
    store: SqliteStateStore,
) -> None:
    event_id, request_id = _seed_delivery(store)
    receipt = _receipt(event_id, request_id)

    with store.unit_of_work() as unit:
        completed = unit.complete_effect_delivery(receipt)
        unit.commit()

    assert completed is False
    with store.unit_of_work() as unit:
        persisted_receipt = unit.get_effect_receipt(event_id)
        delivery = unit.get_effect_delivery(event_id)
    assert persisted_receipt is None
    assert delivery is not None
    assert delivery.state is OutboxState.PENDING


def test_complete_effect_delivery_rejects_different_existing_receipt(
    store: SqliteStateStore,
) -> None:
    event_id, request_id = _seed_delivery(store)
    _claim(store, event_id)
    existing = _receipt(event_id, request_id)
    conflicting = _receipt(
        event_id,
        request_id,
        receipt_id="effect-receipt-complete-02",
        owner_id="owner-two",
        attempt_id="attempt-two",
        outcome=EffectOutcome.EFFECT_FAILED,
    )

    with store.unit_of_work() as unit:
        assert unit.complete_effect_delivery(existing) is True
        unit.commit()
    with store.unit_of_work() as unit:
        completed = unit.complete_effect_delivery(conflicting)
        unit.commit()

    assert completed is False
    with store.unit_of_work() as unit:
        persisted_receipt = unit.get_effect_receipt(event_id)
    assert persisted_receipt == existing


def test_complete_effect_delivery_independent_of_legacy_outbox(
    store: SqliteStateStore,
) -> None:
    event_id, request_id = _seed_delivery(store)
    receipt = _receipt(event_id, request_id)

    with store.unit_of_work() as unit:
        # Delivery is claimed
        # pyright: ignore[reportPrivateUsage]
        cursor = unit._db().execute(
            """
            UPDATE effect_deliveries
            SET claimed_by = ?, claim_attempt_id = ?, claimed_at = ?
            WHERE event_id = ?
            """,
            ("owner-one", "attempt-one", 200, event_id),
        )
        assert cursor.rowcount == 1
        unit.commit()

    with store.unit_of_work() as unit:
        completed = unit.complete_effect_delivery(receipt)
        assert completed is True
        unit.commit()

    with store.unit_of_work() as unit:
        persisted_receipt = unit.get_effect_receipt(event_id)
        delivery = unit.get_effect_delivery(event_id)
    assert persisted_receipt is not None
    assert persisted_receipt.outcome is EffectOutcome.EFFECT_SUCCEEDED
    assert delivery is not None
    assert delivery.state is OutboxState.CONSUMED
