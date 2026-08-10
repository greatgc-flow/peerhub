"""Integration tests for ReadUnitOfWork and ReadStateStore (PH-UOW-READ-01)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

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
)
from peerhub.dispatch.model import create_lease
from peerhub.dispatch.service import DispatchService
from peerhub.persistence.sqlite import SqliteReadUnitOfWork, SqliteStateStore, SqliteUnitOfWork
from peerhub.state.contract import ReadStateStore, ReadUnitOfWork, StateStore
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


def test_read_unit_of_work_cannot_be_reentered(store: SqliteStateStore) -> None:
    """Entering an already-entered ReadUnitOfWork raises RuntimeError."""
    read_uow = store.read_unit_of_work()
    with read_uow:
        with pytest.raises(RuntimeError, match="cannot be re-entered"):
            read_uow.__enter__()
