"""Integration coverage for the effect-receipt delivery foreign key."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from peerhub.governance.broker import GovernanceBroker
from peerhub.governance.contract import (
    EffectIntent,
    EffectOutcome,
    EffectReceipt,
    MutationRequest,
)
from peerhub.persistence.sqlite import SqliteStateStore
from tests.fakes import FakeClock, FakeIdSource


@pytest.fixture
def store(tmp_path: Path) -> Iterator[SqliteStateStore]:
    state_store = SqliteStateStore(
        tmp_path / "effect_receipt_delivery_fk.sqlite3",
        workspace_home_id="workspace-effect-receipt-delivery-fk",
    )
    state_store.initialize()
    try:
        yield state_store
    finally:
        state_store.close()


def _request(suffix: str) -> MutationRequest:
    return MutationRequest(
        request_id=f"request-{suffix}",
        command_id=f"command-{suffix}",
        correlation_id=f"correlation-{suffix}",
        client_id=f"client-{suffix}",
        command_type="governance.effect-receipt-fk.test",
        idempotency_key=f"idempotency-{suffix}",
        actor_id=f"actor-{suffix}",
        policy_revision=f"policy-{suffix}",
        target_id=f"target-{suffix}",
        expected_revision=0,
        operation="set",
        desired_state={"enabled": True},
        effect_intent=EffectIntent(
            kind="test.effect",
            payload={"enabled": True},
        ),
    )


def _broker(
    store: SqliteStateStore,
    suffix: str,
) -> GovernanceBroker:
    return GovernanceBroker(
        store,
        clock=FakeClock([100, 200, 300]),
        ids=FakeIdSource(
            (
                f"plan-{suffix}",
                f"transition-receipt-{suffix}",
                f"event-{suffix}",
                f"effect-receipt-{suffix}",
            )
        ),
    )


def test_migration_rejects_receipt_without_effect_delivery(
    store: SqliteStateStore,
) -> None:
    broker = _broker(store, "missing-delivery")
    submission = broker.submit(_request("missing-delivery"))
    event_id = submission.receipt.outbox_event_id

    with store.unit_of_work() as unit:
        migration = unit._db().execute(  # pyright: ignore[reportPrivateUsage]
            "SELECT name FROM schema_migrations WHERE version = 15"
        ).fetchone()
        foreign_keys = unit._db().execute(  # pyright: ignore[reportPrivateUsage]
            "PRAGMA foreign_key_list(effect_receipts)"
        ).fetchall()
        unit._db().execute(  # pyright: ignore[reportPrivateUsage]
            "DELETE FROM effect_deliveries WHERE event_id = ?",
            (event_id,),
        )
        unit.commit()

    with store.unit_of_work() as unit:
        legacy = unit.get_outbox_event(event_id)
        delivery = unit.get_effect_delivery(event_id)

    assert migration is not None
    assert migration["name"] == "0015_effect_receipts_delivery_fk"
    assert any(
        row["table"] == "effect_deliveries"
        and row["from"] == "outbox_event_id"
        and row["to"] == "event_id"
        for row in foreign_keys
    )
    assert legacy is not None
    assert delivery is None

    receipt = EffectReceipt(
        effect_receipt_id="effect-receipt-missing-delivery",
        request_id=submission.receipt.request_id,
        outbox_event_id=event_id,
        attempt_id="attempt-missing-delivery",
        owner_id="owner-missing-delivery",
        outcome=EffectOutcome.EFFECT_SUCCEEDED,
        completed_at=400,
    )
    with pytest.raises(
        sqlite3.IntegrityError,
        match="FOREIGN KEY constraint failed",
    ):
        with store.unit_of_work() as unit:
            unit.add_effect_receipt(receipt)


def test_full_effect_pipeline_inserts_receipt_under_delivery_fk(
    store: SqliteStateStore,
) -> None:
    broker = _broker(store, "pipeline")
    submission = broker.submit(_request("pipeline"))
    event_id = submission.receipt.outbox_event_id
    claimed = broker.claim_effect(
        event_id,
        owner_id="owner-pipeline",
        attempt_id="attempt-pipeline",
    )
    receipt = broker.record_effect_result(
        claimed.event_id,
        owner_id="owner-pipeline",
        attempt_id="attempt-pipeline",
        outcome=EffectOutcome.EFFECT_SUCCEEDED,
    )

    assert broker.get_effect_receipt(event_id) == receipt
    with store.unit_of_work() as unit:
        delivery = unit.get_effect_delivery(event_id)
        violations = unit._db().execute(  # pyright: ignore[reportPrivateUsage]
            "PRAGMA foreign_key_check(effect_receipts)"
        ).fetchall()
    assert delivery is not None
    assert violations == []
