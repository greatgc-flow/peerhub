"""Phase 0 GB vectors adapted to the production Slice 1 schema."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier, get_ident

import pytest

from fakes import (
    FakeClock,
    FakeIdSource,
    RaisingFaultInjector,
    deterministic_uuid4,
)
from peerhub.core.errors import (
    ExclusiveClaimConflictError,
    IdempotencyPayloadMismatchError,
    StaleRevisionError,
)
from peerhub.core.protocol import (
    CommandID,
    PROTOCOL_MAJOR,
    PROTOCOL_MINOR,
    SCHEMA_VERSION,
)
from peerhub.governance.broker import (
    FaultPoint,
    GovernanceBroker,
)
from peerhub.governance.contract import (
    EffectIntent,
    EffectOutcome,
    MutationDisposition,
    MutationRequest,
    OutboxEvent,
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
    assert pending[0].event.event_id == deterministic_uuid4(
        "outbox-GB-01"
    )
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


def test_recover_pending_effects_excludes_pure_dispatch_events(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    broker = GovernanceBroker(
        store,
        clock=FakeClock([950]),
        ids=FakeIdSource(
            [
                "plan-recovery-gov",
                "receipt-recovery-gov",
                "outbox-recovery-gov",
            ]
        ),
    )
    submission = broker.submit(
        _request(
            suffix="recovery-gov",
            target_id="target-recovery-gov",
            expected_revision=0,
            idempotency_key="recovery-gov",
            value=7,
        )
    )
    dispatch_event = OutboxEvent(
        event_id=deterministic_uuid4("dispatch-recovery-only"),
        protocol_major=PROTOCOL_MAJOR,
        protocol_minor=PROTOCOL_MINOR,
        schema_version=SCHEMA_VERSION,
        correlation_id="correlation-dispatch-recovery",
        occurred_at=951,
        event_kind="dispatch.attempt.started",
        payload={"attempt_id": "attempt-dispatch-recovery"},
        state=OutboxState.PENDING,
        created_at=951,
    )
    with store.unit_of_work() as unit:
        unit.add_outbox_event(dispatch_event)
        unit.commit()

    recovered = broker.recover_pending_effects()

    assert tuple(item.event.event_id for item in recovered) == (
        submission.receipt.outbox_event_id,
    )
    with store.unit_of_work() as unit:
        assert unit.get_effect_delivery(dispatch_event.event_id) is None


def test_recover_pending_effects_preserves_limit_and_position_order(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    broker = GovernanceBroker(
        store,
        clock=FakeClock([960, 961, 962]),
        ids=FakeIdSource(
            [
                "plan-recovery-limit-1",
                "receipt-recovery-limit-1",
                "outbox-recovery-limit-1",
                "plan-recovery-limit-2",
                "receipt-recovery-limit-2",
                "outbox-recovery-limit-2",
                "plan-recovery-limit-3",
                "receipt-recovery-limit-3",
                "outbox-recovery-limit-3",
            ]
        ),
    )
    submissions = tuple(
        broker.submit(
            _request(
                suffix=f"recovery-limit-{index}",
                target_id=f"target-recovery-limit-{index}",
                expected_revision=0,
                idempotency_key=f"recovery-limit-{index}",
                value=index,
            )
        )
        for index in range(1, 4)
    )

    recovered = broker.recover_pending_effects(limit=2)

    assert tuple(item.event.event_id for item in recovered) == tuple(
        submission.receipt.outbox_event_id
        for submission in submissions[:2]
    )
    assert [item.event.outbox_position for item in recovered] == sorted(
        item.event.outbox_position for item in recovered
    )


def test_recover_pending_effects_keeps_claimed_unreceipted_delivery(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    broker = GovernanceBroker(
        store,
        clock=FakeClock([970, 971]),
        ids=FakeIdSource(
            [
                "plan-recovery-claimed",
                "receipt-recovery-claimed",
                "outbox-recovery-claimed",
            ]
        ),
    )
    submission = broker.submit(
        _request(
            suffix="recovery-claimed",
            target_id="target-recovery-claimed",
            expected_revision=0,
            idempotency_key="recovery-claimed",
            value=8,
        )
    )
    claimed = broker.claim_effect(
        submission.receipt.outbox_event_id,
        owner_id="owner-recovery-claimed",
        attempt_id="attempt-recovery-claimed",
    )

    recovered = broker.recover_pending_effects()

    assert len(recovered) == 1
    assert recovered[0].event.event_id == claimed.event_id
    assert recovered[0].event.state is OutboxState.CLAIMED
    assert (
        recovered[0].disposition
        is RecoveryDisposition.CONFIRMATION_REQUIRED
    )
    assert broker.get_effect_receipt(claimed.event_id) is None


def test_claim_effect_same_owner_attempt_is_idempotent(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    broker = GovernanceBroker(
        store,
        clock=FakeClock([972, 973]),
        ids=FakeIdSource(
            [
                "plan-claim-idempotent",
                "receipt-claim-idempotent",
                "outbox-claim-idempotent",
            ]
        ),
    )
    submission = broker.submit(
        _request(
            suffix="claim-idempotent",
            target_id="target-claim-idempotent",
            expected_revision=0,
            idempotency_key="claim-idempotent",
            value=9,
        )
    )

    first = broker.claim_effect(
        submission.receipt.outbox_event_id,
        owner_id="owner-claim-idempotent",
        attempt_id="attempt-claim-idempotent",
    )
    repeated = broker.claim_effect(
        submission.receipt.outbox_event_id,
        owner_id="owner-claim-idempotent",
        attempt_id="attempt-claim-idempotent",
    )

    assert repeated == first
    assert repeated.state is OutboxState.CLAIMED
    assert repeated.claimed_by == "owner-claim-idempotent"
    assert repeated.claim_attempt_id == "attempt-claim-idempotent"


def test_claim_effect_conflict_reports_delivery_owner(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    broker = GovernanceBroker(
        store,
        clock=FakeClock([974, 975]),
        ids=FakeIdSource(
            [
                "plan-claim-owner",
                "receipt-claim-owner",
                "outbox-claim-owner",
            ]
        ),
    )
    submission = broker.submit(
        _request(
            suffix="claim-owner",
            target_id="target-claim-owner",
            expected_revision=0,
            idempotency_key="claim-owner",
            value=10,
        )
    )
    broker.claim_effect(
        submission.receipt.outbox_event_id,
        owner_id="owner-first",
        attempt_id="attempt-first",
    )

    with pytest.raises(ExclusiveClaimConflictError) as raised:
        broker.claim_effect(
            submission.receipt.outbox_event_id,
            owner_id="owner-second",
            attempt_id="attempt-second",
        )

    assert raised.value.current_owner_id == "owner-first"


def test_claim_effect_concurrent_contenders_have_one_winner(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    broker = GovernanceBroker(
        store,
        clock=FakeClock([980, 981]),
        ids=FakeIdSource(
            [
                "plan-claim-concurrent",
                "receipt-claim-concurrent",
                "outbox-claim-concurrent",
            ]
        ),
    )
    submission = broker.submit(
        _request(
            suffix="claim-concurrent",
            target_id="target-claim-concurrent",
            expected_revision=0,
            idempotency_key="claim-concurrent",
            value=11,
        )
    )
    event_id = submission.receipt.outbox_event_id
    barrier = Barrier(2)

    def contend(
        index: int,
    ) -> tuple[
        int,
        str,
        OutboxEvent | ExclusiveClaimConflictError,
    ]:
        barrier.wait()
        try:
            claimed = broker.claim_effect(
                event_id,
                owner_id=f"owner-concurrent-{index}",
                attempt_id=f"attempt-concurrent-{index}",
            )
        except ExclusiveClaimConflictError as conflict:
            return (get_ident(), "conflict", conflict)
        return (get_ident(), "claimed", claimed)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = tuple(executor.map(contend, (1, 2)))

    assert len({thread_id for thread_id, _, _ in results}) == 2
    winners = [result for result in results if result[1] == "claimed"]
    conflicts = [result for result in results if result[1] == "conflict"]
    assert len(winners) == 1
    assert len(conflicts) == 1
    winner = winners[0][2]
    conflict = conflicts[0][2]
    assert isinstance(winner, OutboxEvent)
    assert isinstance(conflict, ExclusiveClaimConflictError)
    assert conflict.current_owner_id == winner.claimed_by

    with store.unit_of_work() as unit:
        delivery = unit.get_effect_delivery(event_id)
        legacy = unit.get_outbox_event(event_id)
    assert delivery is not None
    assert legacy is not None
    assert delivery.state is OutboxState.CLAIMED
    assert delivery.claimed_by == winner.claimed_by
    assert delivery.claimed_by == legacy.claimed_by
    assert delivery.claim_attempt_id == legacy.claim_attempt_id
    assert delivery.claimed_at == legacy.claimed_at
