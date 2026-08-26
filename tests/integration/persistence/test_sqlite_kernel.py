"""Integration tests for the production SQLite store kernel."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fakes import FakeClock, FakeIdSource
from peerhub.core.context import PathLayout, RuntimeContext
from peerhub.core.errors import (
    StaleRevisionError,
    WorkspaceIdentityMismatchError,
)
from peerhub.core.protocol import CommandID
from peerhub.governance.broker import GovernanceBroker
from peerhub.governance.contract import (
    EffectIntent,
    EffectOutcome,
    MutationDisposition,
    MutationRequest,
    OutboxState,
    TargetState,
)
from peerhub.persistence.sqlite import SqliteStateStore
from peerhub.runtime import create_runtime


def _request(
    suffix: str,
    *,
    target_id: str = "target-integration",
    expected_revision: int = 0,
    value: int = 1,
) -> MutationRequest:
    return MutationRequest(
        request_id=f"request-{suffix}",
        command_id=CommandID(f"command-{suffix}"),
        correlation_id=f"correlation-{suffix}",
        client_id="client-integration",
        command_type="governance.mutate",
        idempotency_key=f"key-{suffix}",
        actor_id="actor-integration",
        policy_revision="policy-r1",
        target_id=target_id,
        expected_revision=expected_revision,
        operation="SET",
        desired_state={"value": value},
        effect_intent=EffectIntent(
            kind="INTEGRATION_EFFECT",
            payload={"value": value},
        ),
    )


def _store(tmp_path: Path) -> SqliteStateStore:
    store = SqliteStateStore(
        tmp_path / "peerhub.sqlite3",
        workspace_home_id="workspace-integration",
    )
    store.initialize()
    return store


def test_runtime_is_the_composition_root(
    tmp_path: Path,
) -> None:
    paths = PathLayout(
        workspace_root=tmp_path,
        workspace_home=tmp_path / ".peerhub",
        database_path=(
            tmp_path / ".peerhub" / "peerhub.sqlite3"
        ),
    )
    context = RuntimeContext(
        workspace_home_id="workspace-runtime",
        paths=paths,
        clock=FakeClock([1]),
        ids=FakeIdSource(
            ["plan-runtime", "receipt-runtime", "outbox-runtime"]
        ),
    )

    with create_runtime(context) as runtime:
        result = runtime.governance_broker.submit(
            _request("runtime")
        )
        assert (
            result.disposition
            is MutationDisposition.COMMITTED
        )
        assert runtime.state_store.database_path == (
            paths.database_path
        )


def test_workspace_identity_is_bound_to_database(
    tmp_path: Path,
) -> None:
    path = tmp_path / "identity.sqlite3"
    first = SqliteStateStore(
        path,
        workspace_home_id="workspace-a",
    )
    first.initialize()

    second = SqliteStateStore(
        path,
        workspace_home_id="workspace-b",
    )
    try:
        second.initialize()
    except WorkspaceIdentityMismatchError as error:
        assert error.stored_workspace_home_id == "workspace-a"
    else:
        raise AssertionError(
            "workspace identity mismatch was not rejected"
        )


def test_outbox_recovery_claim_and_completion(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    broker = GovernanceBroker(
        store,
        clock=FakeClock([10, 11, 12]),
        ids=FakeIdSource(
            [
                "plan-integration",
                "receipt-integration",
                "outbox-integration",
                "effect-receipt-integration",
            ]
        ),
    )

    submission = broker.submit(_request("outbox"))
    pending = broker.recover_pending_effects()
    assert len(pending) == 1
    assert pending[0].event.state is OutboxState.PENDING

    claimed = broker.claim_effect(
        submission.receipt.outbox_event_id,
        owner_id="worker-one",
        attempt_id="attempt-one",
    )
    assert claimed.state is OutboxState.CLAIMED

    effect_receipt = broker.record_effect_result(
        claimed.event_id,
        owner_id="worker-one",
        attempt_id="attempt-one",
        outcome=EffectOutcome.EFFECT_SUCCEEDED,
    )
    assert (
        broker.get_effect_receipt(claimed.event_id)
        == effect_receipt
    )
    assert broker.recover_pending_effects() == ()


def test_concurrent_same_revision_submissions_have_one_winner(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    requests = (
        _request(
            "contender-a",
            target_id="target-contention",
            value=1,
        ),
        _request(
            "contender-b",
            target_id="target-contention",
            value=2,
        ),
    )
    brokers = (
        GovernanceBroker(
            store,
            clock=FakeClock([20]),
            ids=FakeIdSource(
                ["plan-a", "receipt-a", "outbox-a"]
            ),
        ),
        GovernanceBroker(
            store,
            clock=FakeClock([21]),
            ids=FakeIdSource(
                ["plan-b", "receipt-b", "outbox-b"]
            ),
        ),
    )

    def submit(
        index: int,
    ) -> tuple[str, object]:
        try:
            return ("committed", brokers[index].submit(requests[index]))
        except StaleRevisionError as error:
            return ("stale", error)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = tuple(executor.map(submit, (0, 1)))

    committed = [
        value for status, value in results
        if status == "committed"
    ]
    stale = [
        value for status, value in results
        if status == "stale"
    ]
    assert len(committed) == 1
    assert len(stale) == 1
    assert isinstance(stale[0], StaleRevisionError)
    assert stale[0].current_revision == 1

    target = brokers[0].get_target("target-contention")
    assert target is not None
    assert target.revision == 1
    assert target.state["value"] in {1, 2}

    pending = brokers[0].recover_pending_effects()
    assert len(pending) == 1


def _write_target(store: SqliteStateStore, target_id: str, state: dict) -> None:
    with store.unit_of_work() as unit:
        assert unit.compare_and_set_target(
            None,
            TargetState(target_id=target_id, revision=1, state=state, updated_at=1),
        )
        unit.commit()


def test_list_targets_by_kind_and_scope(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _write_target(store, "task:one", {"kind": "task", "scope": "room-a"})
    _write_target(store, "task:two", {"kind": "task", "scope": "room-b"})
    _write_target(store, "lesson:one", {"kind": "lesson", "scope": "room-a"})
    broker = GovernanceBroker(store, clock=FakeClock([1]), ids=FakeIdSource([]))

    assert [target.target_id for target in broker.list_targets("task")] == ["task:one", "task:two"]
    assert [target.target_id for target in broker.list_targets("task", "room-a")] == ["task:one"]


def test_list_targets_empty_when_no_match(tmp_path: Path) -> None:
    store = _store(tmp_path)
    broker = GovernanceBroker(store, clock=FakeClock([1]), ids=FakeIdSource([]))
    assert broker.list_targets("missing") == ()


def test_list_targets_reflects_mixed_updates(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _write_target(store, "task:one", {"kind": "task", "scope": "room-a"})
    _write_target(store, "task:two", {"kind": "task", "scope": "room-a"})
    with store.unit_of_work() as unit:
        first = unit.get_target("task:one")
        second = unit.get_target("task:two")
        assert first is not None and second is not None
        assert unit.compare_and_set_target(
            first,
            TargetState(target_id=first.target_id, revision=2, state={"kind": "lesson", "scope": "room-a"}, updated_at=2),
        )
        assert unit.compare_and_set_target(
            second,
            TargetState(target_id=second.target_id, revision=2, state={"kind": "task", "scope": "room-b"}, updated_at=2),
        )
        unit.commit()
    broker = GovernanceBroker(store, clock=FakeClock([1]), ids=FakeIdSource([]))

    assert broker.list_targets("task", "room-a") == ()
    assert [target.target_id for target in broker.list_targets("task", "room-b")] == ["task:two"]
    assert [target.target_id for target in broker.list_targets("lesson", "room-a")] == ["task:one"]
