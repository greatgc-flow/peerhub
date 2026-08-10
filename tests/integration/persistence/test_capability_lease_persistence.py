"""Integration coverage for durable capability-lease storage."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from peerhub.core.protocol import (
    PROTOCOL_MAJOR,
    PROTOCOL_MINOR,
    SCHEMA_VERSION,
    CommandEnvelope,
)
from peerhub.dispatch.capability import (
    CapabilityLease,
    CapabilityTier,
    EnforcementLevel,
)
from peerhub.dispatch.contract import (
    AdmissionReceipt,
    CompletionContract,
    CompletionContractKind,
    RequestSnapshot,
)
from peerhub.dispatch.service import DispatchService
from peerhub.persistence.sqlite import SqliteStateStore
from tests.fakes import DeterministicClock, SequentialIdSource


def _store(database_path: Path) -> SqliteStateStore:
    store = SqliteStateStore(
        database_path,
        workspace_home_id="workspace-capability-lease",
    )
    store.initialize()
    return store


def _admit(
    store: SqliteStateStore,
) -> tuple[RequestSnapshot, AdmissionReceipt]:
    service = DispatchService(
        store,
        clock=DeterministicClock(start=100),
        ids=SequentialIdSource(),
    )
    envelope = CommandEnvelope(
        protocol_major=PROTOCOL_MAJOR,
        protocol_minor=PROTOCOL_MINOR,
        schema_version=SCHEMA_VERSION,
        client_request_id="client-request-capability",
        correlation_id="correlation-capability",
        client_id="client-capability",
        actor_id="actor-capability",
        scope={"workspace_id": "workspace-capability"},
        method="peer.ask",
        params={"prompt": "persist capability lease"},
        idempotency_key="idempotency-capability",
        expected_policy_revision=7,
        expected_configuration_revision=11,
        client_timestamp=10,
    )
    request, receipt, _ = service.admit_request(
        envelope,
        authenticated_principal="principal-capability",
        actor_authorized=True,
        completion_contract=CompletionContract(
            contract_id="completion-capability",
            kind=CompletionContractKind.DELIVERY_ONLY,
            requirements=(),
            replay_safe=False,
        ),
        policy_revision=7,
        configuration_revision=11,
        selected_peer_instance_id="cx-instance-capability",
        selected_profile_id="cx.deepthink",
        route_decision_digest="a" * 64,
        session_id="session-capability",
        owner_principal_id="principal-capability",
        owner_instance_id="cx-instance-capability",
        authority_epoch=3,
        heartbeat_timeout_ms=5_000,
        owner_peer_id="cx",
    )
    return request, receipt


def _capability_lease(
    request: RequestSnapshot,
    receipt: AdmissionReceipt,
) -> CapabilityLease:
    return CapabilityLease(
        capability_lease_id="capability-lease-01",
        command_id=request.command_id,
        admission_receipt_id=receipt.admission_receipt_id,
        session_lease_id=request.lease_id,
        subject_principal_id=request.authenticated_principal,
        selected_peer_kind="cx",
        required_tier=CapabilityTier.WORKTREE_WRITE,
        authorized_tier=CapabilityTier.WORKTREE_WRITE,
        minimum_enforcement=EnforcementLevel.ENFORCED,
        selected_peer_instance_id=request.selected_peer_instance_id,
        selected_profile_id=request.selected_profile_id,
        route_decision_digest=request.route_decision_digest,
        policy_revision=request.policy_revision,
        issuer_id="capability-policy-r1",
        issued_at=receipt.admitted_at,
        expires_at=receipt.admitted_at + 10_000,
    )


def test_migration_0018_registers_schema_without_implicit_tier(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "capability-schema.sqlite3"
    _store(database_path)

    connection = sqlite3.connect(database_path)
    try:
        assert connection.execute(
            "PRAGMA user_version"
        ).fetchone() == (18,)
        assert connection.execute(
            "SELECT name FROM schema_migrations WHERE version = 18"
        ).fetchone() == ("0018_capability_leases",)

        request_columns = {
            row[1]: row
            for row in connection.execute(
                "PRAGMA table_info(dispatch_requests)"
            ).fetchall()
        }
        tier_column = request_columns["required_capability_tier"]
        assert tier_column[3] == 0
        assert tier_column[4] is None

        foreign_keys = {
            (row[3], row[2], row[4])
            for row in connection.execute(
                "PRAGMA foreign_key_list(capability_leases)"
            ).fetchall()
        }
        assert foreign_keys == {
            ("command_id", "dispatch_requests", "command_id"),
            (
                "admission_receipt_id",
                "admission_receipts",
                "admission_receipt_id",
            ),
            ("session_lease_id", "leases", "lease_id"),
        }
        unique_indexes = {
            tuple(
                index_column[2]
                for index_column in connection.execute(
                    f"PRAGMA index_info({index_row[1]})"
                ).fetchall()
            )
            for index_row in connection.execute(
                "PRAGMA index_list(capability_leases)"
            ).fetchall()
            if index_row[2] == 1
        }
        assert {
            ("command_id",),
            ("admission_receipt_id",),
            ("session_lease_id",),
        }.issubset(unique_indexes)
        assert connection.execute(
            "PRAGMA foreign_key_check"
        ).fetchall() == []
    finally:
        connection.close()


def test_capability_lease_write_rolls_back_on_fault_before_commit(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path / "capability-rollback.sqlite3")
    request, receipt = _admit(store)
    lease = _capability_lease(request, receipt)

    with pytest.raises(RuntimeError, match="fault before commit"):
        with store.unit_of_work() as unit:
            unit.add_capability_lease(lease)
            raise RuntimeError("fault before commit")

    with store.read_unit_of_work() as unit:
        assert unit.get_capability_lease(
            lease.capability_lease_id
        ) is None
        assert unit.get_capability_lease_by_command_id(
            request.command_id
        ) is None
        assert unit.get_capability_lease_by_admission_receipt_id(
            receipt.admission_receipt_id
        ) is None


def test_capability_lease_replay_returns_identical_durable_record(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "capability-replay.sqlite3"
    store = _store(database_path)
    request, receipt = _admit(store)
    lease = _capability_lease(request, receipt)

    with store.unit_of_work() as unit:
        unit.add_capability_lease(lease)
        unit.commit()

    store.close()
    replay_store = _store(database_path)
    with replay_store.read_unit_of_work() as unit:
        by_id = unit.get_capability_lease(lease.capability_lease_id)
        by_command = unit.get_capability_lease_by_command_id(
            request.command_id
        )
        by_receipt = unit.get_capability_lease_by_admission_receipt_id(
            receipt.admission_receipt_id
        )

    assert by_id == lease
    assert by_command == lease
    assert by_receipt == lease
    assert by_id is not lease
    assert by_id is not by_command
    assert by_command is not by_receipt
