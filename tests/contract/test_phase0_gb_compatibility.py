"""Phase 0 GB vectors adapted to the production Slice 1 schema."""

from __future__ import annotations

from pathlib import Path

import pytest

from fakes import FakeClock, FakeIdSource, RaisingFaultInjector
from peerhub.core.errors import (
    ExclusiveClaimConflictError,
    IdempotencyPayloadMismatchError,
    StaleRevisionError,
)
from peerhub.core.protocol import CommandID
from peerhub.governance.broker import (
    FaultPoint,
    GovernanceBroker,
)
from peerhub.governance.contract import (
    EffectIntent,
    EffectOutcome,
    MutationDisposition,
    MutationRequest,
    OutboxState,
    RecoveryDisposition,
)
from peerhub.persistence.sqlite import SqliteStateStore


def _request(
    *,
    suffix: str,
    target_id: str,
    expected_revision: int,
    idempotency_key: str,
    value: int,
) -> MutationRequest:
    return MutationRequest(
        request_id=f"request-{suffix}",
        command_id=CommandID(f"command-{suffix}"),
        correlation_id=f"correlation-{suffix}",
        client_id="broker-client",
        command_type="governance.mutate",
        idempotency_key=idempotency_key,
        actor_id="actor-admin",
        policy_revision="policy-r1",
        target_id=target_id,
        expected_revision=expected_revision,
        operation="SET_VALUE",
        desired_state={"value": value},
        effect_intent=EffectIntent(
            kind="WRITE_AUDIT_MARKER",
            payload={"marker": f"marker-{suffix}"},
        ),
    )


def _store(tmp_path: Path) -> SqliteStateStore:
    store = SqliteStateStore(
        tmp_path / "peerhub.sqlite3",
        workspace_home_id="workspace-compatibility",
    )
    store.initialize()
    return store


def test_gb01_atomic_transition_commits_all_records(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    broker = GovernanceBroker(
        store,
        clock=FakeClock([900]),
        ids=FakeIdSource(
            [
                "plan-GB-01",
                "receipt-GB-01",
                "outbox-GB-01",
            ]
        ),
    )
    request = _request(
        suffix="GB-01",
        target_id="target-GB-01",
        expected_revision=0,
        idempotency_key="GB-01-atomic-commit",
        value=42,
    )

    result = broker.submit(request)

    assert result.disposition is MutationDisposition.COMMITTED
    assert result.receipt.receipt_id == "receipt-GB-01"
    assert result.receipt.previous_revision == 0
    assert result.receipt.next_revision == 1

    target = broker.get_target("target-GB-01")
    assert target is not None
    assert target.revision == 1
    assert dict(target.state) == {"value": 42}

    pending = broker.recover_pending_effects()
    assert len(pending) == 1
    assert pending[0].event.event_id == "outbox-GB-01"
    assert (
        pending[0].transition_receipt
        == result.receipt
    )


def test_gb02_stale_cas_reports_current_revision_without_mutation(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    broker = GovernanceBroker(
        store,
        clock=FakeClock([901]),
        ids=FakeIdSource(
            ["plan-first", "receipt-first", "outbox-first"]
        ),
    )
    first = _request(
        suffix="GB-02-first",
        target_id="target-GB-02",
        expected_revision=0,
        idempotency_key="GB-02-first",
        value=1,
    )
    stale = _request(
        suffix="GB-02-stale",
        target_id="target-GB-02",
        expected_revision=0,
        idempotency_key="GB-02-stale",
        value=2,
    )

    broker.submit(first)
    with pytest.raises(StaleRevisionError) as raised:
        broker.submit(stale)

    assert raised.value.current_revision == 1
    target = broker.get_target("target-GB-02")
    assert target is not None
    assert target.revision == 1
    assert dict(target.state) == {"value": 1}
    assert len(broker.recover_pending_effects()) == 1


def test_gb03_idempotency_hit_and_payload_mismatch(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    clock = FakeClock([910])
    ids = FakeIdSource(
        ["plan-GB-03", "receipt-GB-03", "outbox-GB-03"]
    )
    broker = GovernanceBroker(store, clock=clock, ids=ids)
    first = _request(
        suffix="GB-03-first",
        target_id="target-GB-03",
        expected_revision=0,
        idempotency_key="GB-03-key",
        value=1,
    )

    committed = broker.submit(first)
    repeated = broker.submit(first)

    assert committed.disposition is MutationDisposition.COMMITTED
    assert (
        repeated.disposition
        is MutationDisposition.IDEMPOTENCY_HIT
    )
    assert repeated.receipt == committed.receipt
    assert clock.calls == 1
    assert len(ids.namespaces) == 3

    changed = _request(
        suffix="GB-03-changed",
        target_id="target-GB-03",
        expected_revision=0,
        idempotency_key="GB-03-key",
        value=2,
    )
    with pytest.raises(IdempotencyPayloadMismatchError):
        broker.submit(changed)

    target = broker.get_target("target-GB-03")
    assert target is not None
    assert target.revision == 1
    assert dict(target.state) == {"value": 1}
    assert len(broker.recover_pending_effects()) == 1


def test_gb04_post_commit_recovery_never_reapplies_transition(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    interrupted = GovernanceBroker(
        store,
        clock=FakeClock([920]),
        ids=FakeIdSource(
            ["plan-GB-04", "receipt-GB-04", "outbox-GB-04"]
        ),
        fault_injector=RaisingFaultInjector(
            FaultPoint.AFTER_COMMIT
        ),
    )
    request = _request(
        suffix="GB-04",
        target_id="target-GB-04",
        expected_revision=0,
        idempotency_key="GB-04-pending",
        value=4,
    )

    with pytest.raises(RuntimeError, match="AFTER_COMMIT"):
        interrupted.submit(request)

    recovered = GovernanceBroker(
        store,
        clock=FakeClock([]),
        ids=FakeIdSource([]),
    )
    first_scan = recovered.recover_pending_effects()
    second_scan = recovered.recover_pending_effects()

    assert first_scan == second_scan
    assert len(first_scan) == 1
    assert (
        first_scan[0].disposition
        is RecoveryDisposition.READY_TO_CLAIM
    )
    assert first_scan[0].event.state is OutboxState.PENDING

    target = recovered.get_target("target-GB-04")
    assert target is not None
    assert target.revision == 1


def test_gb05_terminal_effect_receipt_is_immutable(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    broker = GovernanceBroker(
        store,
        clock=FakeClock([930, 931, 932]),
        ids=FakeIdSource(
            [
                "plan-GB-05",
                "receipt-GB-05",
                "outbox-GB-05",
                "effect-receipt-GB-05",
            ]
        ),
    )
    request = _request(
        suffix="GB-05",
        target_id="target-GB-05",
        expected_revision=0,
        idempotency_key="GB-05-claim",
        value=5,
    )

    submission = broker.submit(request)
    claimed = broker.claim_effect(
        submission.receipt.outbox_event_id,
        owner_id="owner-first",
        attempt_id="attempt-GB-05",
    )
    assert claimed.state is OutboxState.CLAIMED

    receipt = broker.record_effect_result(
        claimed.event_id,
        owner_id="owner-first",
        attempt_id="attempt-GB-05",
        outcome=EffectOutcome.EFFECT_SUCCEEDED,
    )
    repeated = broker.record_effect_result(
        claimed.event_id,
        owner_id="owner-first",
        attempt_id="attempt-GB-05",
        outcome=EffectOutcome.EFFECT_SUCCEEDED,
    )
    assert repeated == receipt

    with pytest.raises(ExclusiveClaimConflictError):
        broker.record_effect_result(
            claimed.event_id,
            owner_id="owner-second",
            attempt_id="attempt-GB-05-second",
            outcome=EffectOutcome.EFFECT_FAILED,
        )

    assert broker.get_effect_receipt(claimed.event_id) == receipt
    assert broker.recover_pending_effects() == ()


def test_gb06_exclusive_claim_rejects_contender(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    broker = GovernanceBroker(
        store,
        clock=FakeClock([940, 941, 942]),
        ids=FakeIdSource(
            [
                "plan-GB-06",
                "receipt-GB-06",
                "outbox-GB-06",
                "effect-receipt-GB-06",
            ]
        ),
    )
    submission = broker.submit(
        _request(
            suffix="GB-06",
            target_id="target-GB-06",
            expected_revision=0,
            idempotency_key="GB-06-lock",
            value=6,
        )
    )
    event_id = submission.receipt.outbox_event_id

    first_claim = broker.claim_effect(
        event_id,
        owner_id="owner-ag",
        attempt_id="attempt-ag",
    )
    with pytest.raises(ExclusiveClaimConflictError):
        broker.claim_effect(
            event_id,
            owner_id="owner-cx",
            attempt_id="attempt-cx",
        )

    assert first_claim.claimed_by == "owner-ag"
    receipt = broker.record_effect_result(
        event_id,
        owner_id="owner-ag",
        attempt_id="attempt-ag",
        outcome=EffectOutcome.EFFECT_SUCCEEDED,
    )
    assert receipt.owner_id == "owner-ag"
