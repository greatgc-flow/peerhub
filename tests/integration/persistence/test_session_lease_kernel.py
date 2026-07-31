"""Integration tests against the production SQLite dispatch repository."""

from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from peerhub.core.context import PathLayout
from peerhub.core.protocol import CommandID
from peerhub.dispatch.contract import (
    LeaseCloseRequest,
    LeaseCreateRequest,
    LeaseRenewRequest,
    LeaseState,
    ProcessBirthIdentity,
    RecoveryTrigger,
    SessionBindingKey,
    SessionBindingState,
)
from peerhub.dispatch.service import DispatchService
from peerhub.persistence.sqlite import SqliteStateStore
from tests.fakes import DeterministicClock, SequentialIdSource


class TestSessionLeaseKernel(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.layout = PathLayout.for_workspace(self.root)
        self.store = SqliteStateStore(
            self.layout.database_path,
            workspace_home_id="ws-home-kernel",
        )
        self.store.initialize()

        self.service = DispatchService(
            self.store,
            clock=DeterministicClock(start=1000),
            ids=SequentialIdSource(),
        )
        self.process_identity = ProcessBirthIdentity(
            pid=5555,
            process_creation_time=9999,
        )
        self.key = SessionBindingKey(
            workspace_scope_id="ws-01",
            instance_id="inst-01",
            profile_id="prof-01",
            conversation_scope="conv-01",
        )

    def tearDown(self) -> None:
        self.store.close()
        self.temp_dir.cleanup()

    def _create(self):
        request = LeaseCreateRequest(
            session_id="session-kernel-01",
            owner_principal_id="principal-kernel",
            owner_instance_id="inst-01",
            owner_process_birth_identity=self.process_identity,
            heartbeat_timeout_ms=5000,
            command_id=CommandID("command-kernel-01"),
            attempt_id="attempt-kernel-01",
            authority_epoch=1,
        )
        return self.service.create_session_and_lease(
            self.key,
            request,
            "fp-kernel",
            "rb-kernel",
        )

    def test_full_lifecycle_create_renew_close(self) -> None:
        binding, lease = self._create()
        self.assertEqual(binding.revision, 1)
        self.assertEqual(lease.state, LeaseState.ACTIVE)
        self.assertEqual(
            lease.fence.command_id,
            CommandID("command-kernel-01"),
        )
        self.assertEqual(
            lease.fence.attempt_id,
            "attempt-kernel-01",
        )
        self.assertEqual(lease.fence.authority_epoch, 1)

        renewed = self.service.renew_lease(
            LeaseRenewRequest(
                lease_id=lease.lease_id,
                fence=lease.fence,
            ),
            heartbeat_timeout_ms=5000,
        )
        self.assertEqual(renewed.state, LeaseState.RENEWED)
        self.assertEqual(renewed.fence.revision, 2)

        closed = self.service.close_lease(
            LeaseCloseRequest(
                lease_id=lease.lease_id,
                fence=renewed.fence,
            )
        )
        self.assertEqual(closed.state, LeaseState.RELEASED)

    def test_session_binding_cas_rejects_stale_snapshot(
        self,
    ) -> None:
        binding, _ = self._create()
        updated = replace(
            binding,
            revision=binding.revision + 1,
            state=SessionBindingState.STALE,
            updated_at=2000,
        )

        with self.store.unit_of_work() as unit:
            self.assertTrue(
                unit.cas_update_session_binding(
                    binding,
                    updated,
                )
            )
            unit.commit()

        stale_update = replace(
            binding,
            revision=binding.revision + 1,
            state=SessionBindingState.SUSPECT,
            updated_at=3000,
        )
        with self.store.unit_of_work() as unit:
            self.assertFalse(
                unit.cas_update_session_binding(
                    binding,
                    stale_update,
                )
            )

        with self.store.unit_of_work() as unit:
            persisted = unit.get_session_binding(self.key)
            self.assertIsNotNone(persisted)
            self.assertEqual(persisted.revision, 2)
            self.assertEqual(
                persisted.state,
                SessionBindingState.STALE,
            )

    def test_recovery_receipt_round_trips_through_repository(
        self,
    ) -> None:
        _, lease = self._create()
        _, receipt = self.service.recover_lease(
            lease.lease_id,
            recovery_actor_principal_id="recovery-agent",
            trigger=RecoveryTrigger.PROCESS_BIRTH_MISMATCH,
            evidence_digest="sha256:kernel",
            policy_id="policy-kernel",
            policy_revision=1,
            is_process_alive=True,
            process_identity_matches=False,
        )

        with self.store.unit_of_work() as unit:
            persisted = unit.get_recovery_receipt(
                receipt.recovery_receipt_id
            )

        self.assertEqual(persisted, receipt)
        self.assertIsNone(
            persisted.external_effect_certainty
            if persisted is not None
            else "missing"
        )


if __name__ == "__main__":
    unittest.main()
