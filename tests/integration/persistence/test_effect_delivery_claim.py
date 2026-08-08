"""Integration tests for the effect-delivery claim cutover primitive."""

from __future__ import annotations

from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier, get_ident

import pytest

from peerhub.governance.broker import GovernanceBroker
from peerhub.governance.contract import (
    EffectIntent,
    EffectOutcome,
    EffectReceipt,
    MutationRequest,
    OutboxEvent,
    OutboxState,
)
from peerhub.persistence.sqlite import SqliteStateStore
from tests.fakes import FakeClock, FakeIdSource


@pytest.fixture
def store(tmp_path: Path) -> Iterator[SqliteStateStore]:
    state_store = SqliteStateStore(
        tmp_path / "effect_delivery_claim.sqlite3",
        workspace_home_id="workspace-effect-delivery-claim",
    )
    state_store.initialize()
    try:
        yield state_store
    finally:
        state_store.close()


def _request() -> MutationRequest:
    return MutationRequest(
        request_id="request-claim-01",
        command_id="command-claim-01",
        correlation_id="correlation-claim-01",
        client_id="client-claim-01",
        command_type="governance.claim.test",
        idempotency_key="idempotency-claim-01",
        actor_id="actor-claim-01",
        policy_revision="policy-claim-01",
        target_id="target-claim-01",
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
                "plan-claim-01",
                "receipt-claim-01",
                "event-claim-01",
            )
        ),
    )
    submission = broker.submit(_request())
    return (
        submission.receipt.outbox_event_id,
        submission.receipt.request_id,
    )


def test_claim_effect_delivery_succeeds(store: SqliteStateStore) -> None:
    event_id, _ = _seed_delivery(store)

    with store.unit_of_work() as unit:
        claimed = unit.claim_effect_delivery(
            event_id,
            "owner-one",
            "attempt-one",
            200,
        )
        unit.commit()

    assert claimed is not None
    assert claimed.state is OutboxState.CLAIMED
    assert claimed.claimed_by == "owner-one"
    assert claimed.claim_attempt_id == "attempt-one"
    assert claimed.claimed_at == 200


def test_claim_effect_delivery_conflict_preserves_first_claim(
    store: SqliteStateStore,
) -> None:
    event_id, _ = _seed_delivery(store)

    with store.unit_of_work() as unit:
        first = unit.claim_effect_delivery(
            event_id,
            "owner-one",
            "attempt-one",
            200,
        )
        unit.commit()
    assert first is not None

    with store.unit_of_work() as unit:
        second = unit.claim_effect_delivery(
            event_id,
            "owner-two",
            "attempt-two",
            300,
        )
        unit.commit()
    assert second is None

    with store.unit_of_work() as unit:
        persisted = unit.get_effect_delivery(event_id)
    assert persisted is not None
    assert persisted.claimed_by == "owner-one"
    assert persisted.claim_attempt_id == "attempt-one"
    assert persisted.claimed_at == 200


def test_claim_effect_delivery_returns_none_when_already_receipted(
    store: SqliteStateStore,
) -> None:
    event_id, request_id = _seed_delivery(store)
    receipt = EffectReceipt(
        effect_receipt_id="effect-receipt-claim-01",
        request_id=request_id,
        outbox_event_id=event_id,
        attempt_id="completed-attempt",
        owner_id="completed-owner",
        outcome=EffectOutcome.EFFECT_SUCCEEDED,
        completed_at=150,
    )
    with store.unit_of_work() as unit:
        unit.add_effect_receipt(receipt)
        unit.commit()

    with store.unit_of_work() as unit:
        claimed = unit.claim_effect_delivery(
            event_id,
            "late-owner",
            "late-attempt",
            200,
        )
        unit.commit()

    assert claimed is None


def test_claim_effect_delivery_returns_none_for_missing_event(
    store: SqliteStateStore,
) -> None:
    with store.unit_of_work() as unit:
        claimed = unit.claim_effect_delivery(
            "missing-event",
            "owner-one",
            "attempt-one",
            200,
        )
        unit.commit()

    assert claimed is None


def test_claim_effect_delivery_legacy_mirror_mismatch_rolls_back(
    store: SqliteStateStore,
) -> None:
    event_id, _ = _seed_delivery(store)

    with store.unit_of_work() as unit:
        legacy_claim = unit.governance.claim_outbox_event(
            event_id,
            "legacy-owner",
            "legacy-attempt",
            150,
        )
        assert legacy_claim is not None
        unit.commit()

    with pytest.raises(
        RuntimeError,
        match="effect delivery claim could not be mirrored",
    ):
        with store.unit_of_work() as unit:
            unit.claim_effect_delivery(
                event_id,
                "new-owner",
                "new-attempt",
                200,
            )

    with store.unit_of_work() as unit:
        delivery = unit.get_effect_delivery(event_id)
        legacy = unit.get_outbox_event(event_id)

    assert delivery is not None
    assert delivery.state is OutboxState.PENDING
    assert delivery.claimed_by is None
    assert delivery.claim_attempt_id is None
    assert delivery.claimed_at is None
    assert legacy is not None
    assert legacy.state is OutboxState.CLAIMED
    assert legacy.claimed_by == "legacy-owner"
    assert legacy.claim_attempt_id == "legacy-attempt"
    assert legacy.claimed_at == 150


def test_concurrent_effect_delivery_claim_has_exactly_one_winner(
    store: SqliteStateStore,
) -> None:
    event_id, _ = _seed_delivery(store)
    barrier = Barrier(2)

    def contend(index: int) -> tuple[int, OutboxEvent | None]:
        barrier.wait()
        with store.unit_of_work() as unit:
            claimed = unit.claim_effect_delivery(
                event_id,
                f"owner-{index}",
                f"attempt-{index}",
                200 + index,
            )
            unit.commit()
        return (get_ident(), claimed)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = tuple(executor.map(contend, (1, 2)))

    thread_ids = {thread_id for thread_id, _ in results}
    claims = [claimed for _, claimed in results if claimed is not None]
    conflicts = [claimed for _, claimed in results if claimed is None]
    assert len(thread_ids) == 2
    assert len(claims) == 1
    assert len(conflicts) == 1

    with store.unit_of_work() as unit:
        delivery = unit.get_effect_delivery(event_id)
        legacy = unit.get_outbox_event(event_id)
    assert delivery is not None
    assert legacy is not None
    assert delivery.state is OutboxState.CLAIMED
    assert delivery.claimed_by in {"owner-1", "owner-2"}
    assert delivery.claimed_by == legacy.claimed_by
    assert delivery.claim_attempt_id == legacy.claim_attempt_id
    assert delivery.claimed_at == legacy.claimed_at
