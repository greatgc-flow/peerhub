"""Fault-boundary tests using the production SQLite dispatch repository."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from peerhub.core.context import PathLayout
from peerhub.dispatch.contract import (
    LeaseCreateRequest,
    LeaseState,
    ProcessBirthIdentity,
    RecoveryTrigger,
    SessionBindingKey,
)
from peerhub.dispatch.service import (
    DispatchService,
    FaultInjector,
    FaultPoint,
)
from peerhub.persistence.sqlite import SqliteStateStore
from tests.fakes import DeterministicClock, SequentialIdSource


class RaisingFaultInjector(FaultInjector):
    def __init__(self, target_point: str) -> None:
        self._target_point = target_point

    def hit(self, point: str) -> None:
        if point == self._target_point:
            raise RuntimeError(f"Simulated fault at {point}")


class TestSessionLeaseFaultBoundaries(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.layout = PathLayout.for_workspace(self.root)
        self.store = SqliteStateStore(
            self.layout.database_path,
            workspace_home_id="ws-home-faults",
        )
        self.store.initialize()

        self.process_identity = ProcessBirthIdentity(
            pid=7777,
            process_creation_time=8888,
        )
        self.key = SessionBindingKey(
            workspace_scope_id="ws-01",
            instance_id="inst-fault",
            profile_id="prof-fault",
            conversation_scope="conv-fault",
        )

    def tearDown(self) -> None:
        self.store.close()
        self.temp_dir.cleanup()

    def _request(self, session_id: str) -> LeaseCreateRequest:
        return LeaseCreateRequest(
            session_id=session_id,
            owner_principal_id="principal-fault",
            owner_instance_id="inst-fault",
            owner_process_birth_identity=self.process_identity,
            heartbeat_timeout_ms=5000,
        )

    def test_creation_fault_rolls_back_binding_and_lease(self) -> None:
        fault_service = DispatchService(
            self.store,
            clock=DeterministicClock(start=1000),
            ids=SequentialIdSource(),
            fault_injector=RaisingFaultInjector(
                FaultPoint.BEFORE_COMMIT
            ),
        )

        with self.assertRaises(RuntimeError):
            fault_service.create_session_and_lease(
                self.key,
                self._request("session-fault-01"),
                "fp-fault",
                "rb-fault",
            )

        with self.store.unit_of_work() as unit:
            self.assertIsNone(
                unit.get_session_binding(self.key)
            )
            self.assertIsNone(unit.get_lease("lease-1"))

    def test_recovery_fault_rolls_back_lease_and_receipt(self) -> None:
        clean_service = DispatchService(
            self.store,
            clock=DeterministicClock(start=1000),
            ids=SequentialIdSource(),
        )
        _, lease = clean_service.create_session_and_lease(
            self.key,
            self._request("session-fault-02"),
            "fp-fault",
            "rb-fault",
        )

        fault_service = DispatchService(
            self.store,
            clock=DeterministicClock(start=1000),
            ids=SequentialIdSource(),
            fault_injector=RaisingFaultInjector(
                FaultPoint.BEFORE_COMMIT
            ),
        )

        with self.assertRaises(RuntimeError):
            fault_service.recover_lease(
                lease.lease_id,
                recovery_actor_principal_id="recovery-agent",
                trigger=RecoveryTrigger.HEARTBEAT_TIMEOUT,
                evidence_digest="sha256:fault",
                policy_id="pol-fault",
                policy_revision=1,
                is_process_alive=True,
                process_identity_matches=True,
            )

        with self.store.unit_of_work() as unit:
            current_lease = unit.get_lease(lease.lease_id)
            self.assertIsNotNone(current_lease)
            self.assertEqual(
                current_lease.state,
                LeaseState.ACTIVE,
            )
            self.assertEqual(current_lease.fence.revision, 1)
            self.assertIsNone(
                unit.get_recovery_receipt("recovery-receipt-1")
            )


if __name__ == "__main__":
    unittest.main()
