"""Integration tests for ReadUnitOfWork and ReadStateStore (PH-UOW-READ-01)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from peerhub.core.errors import RecordNotFoundError
from peerhub.core.protocol import (
    PROTOCOL_MAJOR,
    PROTOCOL_MINOR,
    SCHEMA_VERSION,
    CommandEnvelope,
    CommandID,
)
from peerhub.dispatch.contract import (
    CompletionContract,
    CompletionContractKind,
    LeaseCloseRequest,
    LeaseCreateRequest,
    LeaseFenceCheckRequest,
)
from peerhub.dispatch.capability import CapabilityTier
from peerhub.dispatch.model import create_lease
from peerhub.dispatch.service import DispatchService
from peerhub.dispatch.unit_of_work import DispatchReadUnitOfWork
from peerhub.governance.broker import (
    GovernanceBroker,
    GovernanceReadUnitOfWork,
)
from peerhub.governance.contract import (
    EffectIntent,
    EffectOutcome,
    MutationRequest,
)
from peerhub.persistence.sqlite import (
    SqliteReadUnitOfWork,
    SqliteStateStore,
    SqliteUnitOfWork,
)
from peerhub.routing.service import RoutingReadUnitOfWork, RoutingService
from peerhub.state.contract import ReadStateStore, ReadUnitOfWork, StateStore
from peerhub.telemetry.projections import (
    TelemetryProjector,
    TelemetryReadUnitOfWork,
)
from tests.fakes import DeterministicClock, SequentialIdSource


@pytest.fixture
def store(tmp_path: Path) -> SqliteStateStore:
    db_path = tmp_path / "read_uow_test.sqlite3"
    state_store = SqliteStateStore(db_path, workspace_home_id="workspace-read-uow-test")
    state_store.initialize()
    return state_store


def test_read_unit_of_work_protocol_conformance(store: SqliteStateStore) -> None:
    """Verify SqliteStateStore and SqliteReadUnitOfWork satisfy the formal protocols."""
    assert isinstance(store, StateStore)
    assert isinstance(store, ReadStateStore)

    read_uow = store.read_unit_of_work()
    assert isinstance(read_uow, ReadUnitOfWork)
    assert isinstance(read_uow, DispatchReadUnitOfWork)
    assert isinstance(read_uow, GovernanceReadUnitOfWork)
    assert isinstance(read_uow, RoutingReadUnitOfWork)
    assert isinstance(read_uow, TelemetryReadUnitOfWork)
    assert isinstance(read_uow, SqliteReadUnitOfWork)


def test_read_unit_of_work_rejects_mutation_writes(store: SqliteStateStore) -> None:
    """Writes through read_unit_of_work fail with SQLite query_only enforcement."""
    with store.read_unit_of_work() as read_uow:
        # pyright: ignore[reportPrivateUsage]
        conn = read_uow._db()
        with pytest.raises(sqlite3.OperationalError, match="query_only|readonly"):
            conn.execute(
                """
                INSERT INTO governed_targets (target_id, revision, state_json, updated_at)
                VALUES ('target-illegal', 1, '{}', 100)
                """
            )


def test_read_unit_of_work_does_not_block_concurrent_write_transaction(
    store: SqliteStateStore,
) -> None:
    """Holding a read transaction under WAL does not block another connection's BEGIN IMMEDIATE."""
    with store.read_unit_of_work() as read_uow:
        # Initial read on connection 1
        initial_target = read_uow.get_target("target-concurrent")
        assert initial_target is None

        # Concurrently on connection 2, open a full write unit of work (BEGIN IMMEDIATE)
        with store.unit_of_work() as write_uow:
            from peerhub.governance.contract import TargetState
            write_uow.compare_and_set_target(
                None,
                TargetState(
                    target_id="target-concurrent",
                    revision=1,
                    state={"active": True},
                    updated_at=100,
                ),
            )
            write_uow.commit()

        # Connection 2 write succeeded while connection 1 held the read UoW.
        # Connection 1 still reads consistently in its snapshot.

    # After closing read_uow, a new read_unit_of_work sees the committed write.
    with store.read_unit_of_work() as fresh_read_uow:
        fresh_target = fresh_read_uow.get_target("target-concurrent")
        assert fresh_target is not None
        assert fresh_target.state == {"active": True}


def test_dispatch_service_count_active_leases_migrated_call_site(
    store: SqliteStateStore,
) -> None:
    """Verify DispatchService.count_active_leases() returns correct results through read_unit_of_work."""
    from peerhub.dispatch.contract import (
        LeaseCloseRequest,
        LeaseCreateRequest,
        ProcessBirthIdentity,
        SessionBindingKey,
    )

    dispatch = DispatchService(
        store,
        clock=DeterministicClock(start=100),
        ids=SequentialIdSource(),
    )

    # 1. Initially zero active leases
    assert dispatch.count_active_leases() == 0

    # 2. Create session binding and active lease
    req = LeaseCreateRequest(
        session_id="session-count-01",
        owner_principal_id="principal-count",
        owner_instance_id="inst-count",
        owner_process_birth_identity=ProcessBirthIdentity(
            pid=1234,
            process_creation_time=5678,
        ),
        heartbeat_timeout_ms=5000,
        command_id=CommandID("cmd-count-01"),
        attempt_id="att-count-01",
        authority_epoch=1,
    )
    key = SessionBindingKey(
        workspace_scope_id="ws-count",
        instance_id="inst-count",
        profile_id="prof-count",
        conversation_scope="conv-count",
    )
    binding, lease = dispatch.create_session_and_lease(
        key,
        req,
        "fp-count",
        "rb-count",
    )

    # Count active leases through migrated read_unit_of_work
    assert dispatch.count_active_leases() == 1

    # 3. Close the lease
    dispatch.close_lease(
        LeaseCloseRequest(
            lease_id=lease.lease_id,
            fence=lease.fence,
        )
    )

    # Count active leases is now 0
    assert dispatch.count_active_leases() == 0


def test_dispatch_domain_read_methods_use_read_unit_of_work(
    store: SqliteStateStore,
) -> None:
    """The remaining dispatch queries open read-only UoWs, never write UoWs."""
    write_uow_calls = 0
    read_uow_calls = 0

    class TrackingStore:
        def initialize(self) -> None:
            store.initialize()

        def unit_of_work(self) -> SqliteUnitOfWork:
            nonlocal write_uow_calls
            write_uow_calls += 1
            return store.unit_of_work()

        def read_unit_of_work(self) -> SqliteReadUnitOfWork:
            nonlocal read_uow_calls
            read_uow_calls += 1
            return store.read_unit_of_work()

        def close(self) -> None:
            store.close()

    dispatch = DispatchService(
        TrackingStore(),
        clock=DeterministicClock(start=300),
        ids=SequentialIdSource(),
    )
    envelope = CommandEnvelope(
        protocol_major=PROTOCOL_MAJOR,
        protocol_minor=PROTOCOL_MINOR,
        schema_version=SCHEMA_VERSION,
        client_request_id="client-request-read-uow-dispatch",
        correlation_id="correlation-read-uow-dispatch",
        client_id="client-read-uow-dispatch",
        actor_id="actor-read-uow-dispatch",
        scope={
            "workspace_id": "workspace-read-uow-dispatch",
            "home_id": "home-read-uow-dispatch",
        },
        method="peer.ask",
        params={"prompt": "read through the dispatch read UoW"},
        idempotency_key="idempotency-read-uow-dispatch",
        expected_policy_revision=7,
        expected_configuration_revision=11,
        client_timestamp=300,
    )
    contract = CompletionContract(
        contract_id="completion-contract-read-uow-dispatch",
        kind=CompletionContractKind.DELIVERY_ONLY,
        requirements=(),
        replay_safe=False,
    )
    admitted, _, reserved_lease, _capability = dispatch.admit_request(
        envelope,
        authenticated_principal="principal-read-uow-dispatch",
        actor_authorized=True,
        completion_contract=contract,
        policy_revision=7,
        configuration_revision=11,
        required_capability_tier=CapabilityTier.READ_ONLY,
        selected_peer_instance_id="instance-read-uow-dispatch",
        selected_profile_id="profile-read-uow-dispatch",
        route_decision_digest="d" * 64,
        session_id="session-read-uow-dispatch",
        owner_principal_id="principal-read-uow-dispatch",
        owner_instance_id="instance-read-uow-dispatch",
        authority_epoch=5,
        heartbeat_timeout_ms=5_000,
        owner_peer_id="peer-read-uow-dispatch",
    )
    prepared = dispatch.prepare_request(admitted.command_id)
    attempt = dispatch.create_attempt(admitted.command_id)

    write_uow_calls = 0
    read_uow_calls = 0

    assert dispatch.get_lease(reserved_lease.lease_id) == reserved_lease
    assert dispatch.get_request_and_attempt(
        admitted.command_id,
        attempt.attempt_id,
    ) == (prepared, attempt)
    assert dispatch.get_request(admitted.command_id) == prepared
    assert dispatch.check_lease_fence(
        LeaseFenceCheckRequest(requester_fence=reserved_lease.fence)
    ) == (True, ())

    assert read_uow_calls == 4
    assert write_uow_calls == 0


def test_governance_broker_read_methods_use_read_unit_of_work(
    store: SqliteStateStore,
) -> None:
    """All four governance query methods use the read-only UoW factory."""
    write_uow_calls = 0
    read_uow_calls = 0

    class TrackingStore:
        def initialize(self) -> None:
            store.initialize()

        def unit_of_work(self) -> SqliteUnitOfWork:
            nonlocal write_uow_calls
            write_uow_calls += 1
            return store.unit_of_work()

        def read_unit_of_work(self) -> SqliteReadUnitOfWork:
            nonlocal read_uow_calls
            read_uow_calls += 1
            return store.read_unit_of_work()

        def close(self) -> None:
            store.close()

    broker = GovernanceBroker(
        TrackingStore(),
        clock=DeterministicClock(start=200),
        ids=SequentialIdSource(),
    )
    request = MutationRequest(
        request_id="request-read-uow-governance",
        command_id=CommandID("command-read-uow-governance"),
        correlation_id="correlation-read-uow-governance",
        client_id="client-read-uow-governance",
        command_type="governance.read-uow.test",
        idempotency_key="idempotency-read-uow-governance",
        actor_id="actor-read-uow-governance",
        policy_revision="policy-read-uow-governance",
        target_id="target-read-uow-governance",
        expected_revision=0,
        operation="set",
        desired_state={"enabled": True},
        effect_intent=EffectIntent(
            kind="test.effect",
            payload={"enabled": True},
        ),
    )
    submission = broker.submit(request)
    event_id = submission.receipt.outbox_event_id

    write_uow_calls = 0
    target = broker.get_target(request.target_id)
    event = broker.get_outbox_event(event_id)
    pending = broker.recover_pending_effects()

    assert target is not None
    assert target.state == {"enabled": True}
    assert event is not None
    assert event.event_id == event_id
    assert tuple(item.event.event_id for item in pending) == (event_id,)
    assert read_uow_calls == 3
    assert write_uow_calls == 0

    claimed = broker.claim_effect(
        event_id,
        owner_id="owner-read-uow-governance",
        attempt_id="attempt-read-uow-governance",
    )
    receipt = broker.record_effect_result(
        claimed.event_id,
        owner_id="owner-read-uow-governance",
        attempt_id="attempt-read-uow-governance",
        outcome=EffectOutcome.EFFECT_SUCCEEDED,
    )

    write_uow_calls = 0
    read_uow_calls = 0
    assert broker.get_effect_receipt(event_id) == receipt
    assert read_uow_calls == 1
    assert write_uow_calls == 0


def test_routing_and_telemetry_reads_use_read_unit_of_work(
    store: SqliteStateStore,
) -> None:
    """Routing lookup and both telemetry read phases use read-only UoWs."""
    write_uow_calls = 0
    read_uow_calls = 0

    class TrackingStore:
        def initialize(self) -> None:
            store.initialize()

        def unit_of_work(self) -> SqliteUnitOfWork:
            nonlocal write_uow_calls
            write_uow_calls += 1
            return store.unit_of_work()

        def read_unit_of_work(self) -> SqliteReadUnitOfWork:
            nonlocal read_uow_calls
            read_uow_calls += 1
            return store.read_unit_of_work()

        def close(self) -> None:
            store.close()

    tracking_store = TrackingStore()
    routing = RoutingService(
        tracking_store,
        clock=DeterministicClock(start=400),
        ids=SequentialIdSource(),
    )
    telemetry = TelemetryProjector(
        tracking_store,
        ids=SequentialIdSource(),
        freshness_ttl=3_600,
    )

    assert routing.get_route_decision("missing-route-decision") is None
    with pytest.raises(RecordNotFoundError, match="operational projection"):
        telemetry.get("missing-instance", "missing-profile")

    assert read_uow_calls == 2
    assert write_uow_calls == 0

    broker = GovernanceBroker(
        store,
        clock=DeterministicClock(start=500),
        ids=SequentialIdSource(),
    )
    broker.submit(
        MutationRequest(
            request_id="request-read-uow-telemetry",
            command_id=CommandID("command-read-uow-telemetry"),
            correlation_id="correlation-read-uow-telemetry",
            client_id="client-read-uow-telemetry",
            command_type="telemetry.read-uow.test",
            idempotency_key="idempotency-read-uow-telemetry",
            actor_id="actor-read-uow-telemetry",
            policy_revision="policy-read-uow-telemetry",
            target_id="target-read-uow-telemetry",
            expected_revision=0,
            operation="set",
            desired_state={"enabled": True},
            effect_intent=EffectIntent(
                kind="test.effect",
                payload={"enabled": True},
            ),
        )
    )

    write_uow_calls = 0
    read_uow_calls = 0
    assert telemetry.project_pending() == 1
    assert read_uow_calls == 1
    assert write_uow_calls == 1

    write_uow_calls = 0
    read_uow_calls = 0
    assert telemetry.project_pending() == 0
    assert read_uow_calls == 1
    assert write_uow_calls == 0


def test_read_unit_of_work_cannot_be_reentered(store: SqliteStateStore) -> None:
    """Entering an already-entered ReadUnitOfWork raises RuntimeError."""
    read_uow = store.read_unit_of_work()
    with read_uow:
        with pytest.raises(RuntimeError, match="cannot be re-entered"):
            read_uow.__enter__()
