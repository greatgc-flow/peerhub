"""Real SQLite transaction-boundary fault tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from fakes import FakeClock, FakeIdSource, RaisingFaultInjector
from peerhub.core.protocol import CommandID
from peerhub.governance.broker import (
    FaultPoint,
    GovernanceBroker,
)
from peerhub.governance.contract import (
    EffectIntent,
    MutationRequest,
)
from peerhub.governance.mutations import (
    apply_mutation_plan,
    build_outbox_event,
    build_transition_receipt,
    plan_mutation,
)
from peerhub.persistence.sqlite import SqliteStateStore


def _request(suffix: str) -> MutationRequest:
    return MutationRequest(
        request_id=f"request-{suffix}",
        command_id=CommandID(f"command-{suffix}"),
        correlation_id=f"correlation-{suffix}",
        client_id="client-fault",
        command_type="governance.mutate",
        idempotency_key=f"key-{suffix}",
        actor_id="actor-fault",
        policy_revision="policy-r1",
        target_id=f"target-{suffix}",
        expected_revision=0,
        operation="SET",
        desired_state={"value": 1},
        effect_intent=EffectIntent(
            kind="FAULT_TEST_EFFECT",
            payload={"value": 1},
        ),
    )


def _store(tmp_path: Path, name: str) -> SqliteStateStore:
    store = SqliteStateStore(
        tmp_path / f"{name}.sqlite3",
        workspace_home_id=f"workspace-{name}",
    )
    store.initialize()
    return store


def test_fault_after_target_write_rolls_back_all_rows(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path, "after-target")
    broker = GovernanceBroker(
        store,
        clock=FakeClock([100]),
        ids=FakeIdSource(
            [
                "plan-rollback",
                "receipt-rollback",
                "outbox-rollback",
            ]
        ),
        fault_injector=RaisingFaultInjector(
            FaultPoint.AFTER_TARGET_WRITE
        ),
    )

    with pytest.raises(RuntimeError, match="AFTER_TARGET_WRITE"):
        broker.submit(_request("rollback"))

    clean = GovernanceBroker(
        store,
        clock=FakeClock([]),
        ids=FakeIdSource([]),
    )
    assert clean.get_target("target-rollback") is None
    assert clean.recover_pending_effects() == ()

    with store.unit_of_work() as unit:
        assert (
            unit.get_transition_receipt("receipt-rollback")
            is None
        )
        assert unit.get_outbox_event("outbox-rollback") is None


def test_fault_before_commit_rolls_back_receipt_and_outbox(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path, "before-commit")
    broker = GovernanceBroker(
        store,
        clock=FakeClock([110]),
        ids=FakeIdSource(
            [
                "plan-before",
                "receipt-before",
                "outbox-before",
            ]
        ),
        fault_injector=RaisingFaultInjector(
            FaultPoint.BEFORE_COMMIT
        ),
    )

    with pytest.raises(RuntimeError, match="BEFORE_COMMIT"):
        broker.submit(_request("before"))

    clean = GovernanceBroker(
        store,
        clock=FakeClock([]),
        ids=FakeIdSource([]),
    )
    assert clean.get_target("target-before") is None
    assert clean.recover_pending_effects() == ()


def test_broken_partial_commit_probe_is_observable(
    tmp_path: Path,
) -> None:
    """Prove the suite can observe a target-only broken commit."""

    store = _store(tmp_path, "broken-probe")
    request = _request("broken")
    plan = plan_mutation(
        request,
        None,
        plan_id="plan-broken",
        planned_at=120,
    )
    receipt = build_transition_receipt(
        plan,
        receipt_id="receipt-broken",
        outbox_event_id="outbox-broken",
        committed_at=120,
    )
    event = build_outbox_event(
        plan,
        receipt,
        event_id="outbox-broken",
        created_at=120,
    )
    target = apply_mutation_plan(
        None,
        plan,
        updated_at=120,
    )

    # This deliberately models the regression: a broken broker commits
    # the target before writing its receipt and outbox event.
    with store.unit_of_work() as broken_unit:
        assert broken_unit.compare_and_set_target(None, target)
        broken_unit.commit()

    with store.unit_of_work() as inspection:
        assert inspection.get_target(target.target_id) == target
        assert (
            inspection.get_transition_receipt(
                receipt.receipt_id
            )
            is None
        )
        assert inspection.get_outbox_event(event.event_id) is None

    clean = GovernanceBroker(
        store,
        clock=FakeClock([]),
        ids=FakeIdSource([]),
    )
    assert clean.recover_pending_effects() == ()
