"""Compatibility tests adapting Phase 0 SL-01..06 scenarios."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from peerhub.core.context import PathLayout, RuntimeContext
from peerhub.core.protocol import CommandID
from peerhub.dispatch.contract import (
    LeaseCloseRequest,
    LeaseCreateRequest,
    LeaseFenceCheckRequest,
    LeaseFenceTuple,
    LeaseRenewRequest,
    LeaseState,
    ProcessBirthIdentity,
    RecoveryDecision,
    RecoveryTrigger,
    SessionBindingKey,
)
from peerhub.dispatch.service import DispatchService
from peerhub.persistence.sqlite import SqliteStateStore
from tests.fakes import DeterministicClock, SequentialIdSource


class TestPhase0SlCompatibility(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.layout = PathLayout.for_workspace(self.root)
        self.context = RuntimeContext(
            workspace_home_id="ws-home-sl",
            paths=self.layout,
            clock=DeterministicClock(start=1000),
            ids=SequentialIdSource(),
        )

        self.store = SqliteStateStore(
            self.layout.database_path,
            workspace_home_id="ws-home-sl",
        )
        self.store.initialize()

        self.service = DispatchService(
            self.store,
            clock=self.context.clock,
            ids=self.context.ids,
        )

        self.pid_a = ProcessBirthIdentity(
            pid=1001,
            process_creation_time=5000,
        )
        self.pid_b = ProcessBirthIdentity(
            pid=1002,
            process_creation_time=6000,
        )

        self.key_a = SessionBindingKey(
            workspace_scope_id="ws-01",
            instance_id="instance-ag-01",
            profile_id="prof-a",
            conversation_scope="conv-a",
        )

    def tearDown(self) -> None:
        self.store.close()
        self.temp_dir.cleanup()

    def _lease_request(
        self,
        *,
        suffix: str,
        session_id: str,
        owner_instance_id: str,
        process_identity: ProcessBirthIdentity,
    ) -> LeaseCreateRequest:
        return LeaseCreateRequest(
            session_id=session_id,
            owner_principal_id="principal-ag",
            owner_instance_id=owner_instance_id,
            owner_process_birth_identity=process_identity,
            heartbeat_timeout_ms=5000,
            command_id=CommandID(f"command-{suffix}"),
            attempt_id=f"attempt-{suffix}",
            authority_epoch=1,
            owner_peer_id="ag",
        )

    def test_sl01_atomic_session_and_lease_creation(self) -> None:
        request = self._lease_request(
            suffix="SL-01",
            session_id="session-SL-01",
            owner_instance_id="instance-ag-01",
            process_identity=self.pid_a,
        )
        binding, lease = self.service.create_session_and_lease(
            self.key_a,
            request,
            adapter_fingerprint="fp-sl01",
            readiness_binding="rb-sl01",
        )

        self.assertEqual(binding.session_id, "session-SL-01")
        self.assertEqual(lease.state, LeaseState.ACTIVE)
        self.assertEqual(lease.fence.fencing_token, 1)
        self.assertEqual(
            lease.fence.command_id,
            CommandID("command-SL-01"),
        )
        self.assertEqual(
            lease.fence.attempt_id,
            "attempt-SL-01",
        )
        self.assertEqual(lease.fence.authority_epoch, 1)

    def test_sl04_concurrent_leases_renew_close_and_stale_cas(
        self,
    ) -> None:
        request_a = self._lease_request(
            suffix="SL-04-A",
            session_id="session-SL-04-A",
            owner_instance_id="instance-ag-01",
            process_identity=self.pid_a,
        )
        key_b = SessionBindingKey(
            workspace_scope_id="ws-01",
            instance_id="instance-ag-02",
            profile_id="prof-b",
            conversation_scope="conv-b",
        )
        request_b = self._lease_request(
            suffix="SL-04-B",
            session_id="session-SL-04-B",
            owner_instance_id="instance-ag-02",
            process_identity=self.pid_b,
        )

        _, lease_a = self.service.create_session_and_lease(
            self.key_a,
            request_a,
            "fp",
            "rb",
        )
        _, lease_b = self.service.create_session_and_lease(
            key_b,
            request_b,
            "fp",
            "rb",
        )

        renewed_a = self.service.renew_lease(
            LeaseRenewRequest(
                lease_id=lease_a.lease_id,
                fence=lease_a.fence,
            ),
            heartbeat_timeout_ms=5000,
        )
        self.assertEqual(
            renewed_a.state,
            LeaseState.RENEWED,
        )
        self.assertEqual(renewed_a.fence.revision, 2)

        closed_b = self.service.close_lease(
            LeaseCloseRequest(
                lease_id=lease_b.lease_id,
                fence=lease_b.fence,
            )
        )
        self.assertEqual(closed_b.state, LeaseState.RELEASED)

        is_valid, mismatches = self.service.check_lease_fence(
            LeaseFenceCheckRequest(
                requester_fence=lease_a.fence
            )
        )
        self.assertFalse(is_valid)
        self.assertIn("fencing_token", mismatches)

    def test_sl05_authenticated_principal_fencing(self) -> None:
        request = self._lease_request(
            suffix="SL-05",
            session_id="session-SL-05",
            owner_instance_id="instance-ag-01",
            process_identity=self.pid_a,
        )
        _, lease = self.service.create_session_and_lease(
            self.key_a,
            request,
            "fp",
            "rb",
        )

        spoofed_fence = LeaseFenceTuple(
            session_id=lease.fence.session_id,
            lease_id=lease.lease_id,
            fencing_token=lease.fence.fencing_token,
            revision=lease.fence.revision,
            owner_principal_id="principal-cx-SPOOF",
            owner_instance_id=(
                lease.fence.owner_instance_id
            ),
            owner_process_birth_identity=self.pid_a,
            command_id=lease.fence.command_id,
            authority_epoch=lease.fence.authority_epoch,
            attempt_id=lease.fence.attempt_id,
            owner_peer_id="ag",
        )
        is_valid, mismatches = self.service.check_lease_fence(
            LeaseFenceCheckRequest(
                requester_fence=spoofed_fence
            )
        )

        self.assertFalse(is_valid)
        self.assertIn("owner_principal_id", mismatches)

    def test_sl06_recovery_receipt_generation(self) -> None:
        request = self._lease_request(
            suffix="SL-06",
            session_id="session-SL-06",
            owner_instance_id="instance-ag-01",
            process_identity=self.pid_a,
        )
        _, lease = self.service.create_session_and_lease(
            self.key_a,
            request,
            "fp",
            "rb",
        )

        recovered_lease, receipt = self.service.recover_lease(
            lease.lease_id,
            recovery_actor_principal_id=(
                "principal-recovery-agent"
            ),
            trigger=RecoveryTrigger.PROCESS_BIRTH_MISMATCH,
            evidence_digest="sha256:evidence-sl06",
            policy_id="policy-sl06",
            policy_revision=1,
            is_process_alive=True,
            process_identity_matches=False,
        )

        self.assertEqual(
            recovered_lease.state,
            LeaseState.IDENTITY_MISMATCH,
        )
        self.assertEqual(
            receipt.decision,
            RecoveryDecision.REJECT_AND_QUARANTINE,
        )
        self.assertEqual(receipt.post_revision, 2)
        self.assertEqual(receipt.post_fencing_token, 2)


if __name__ == "__main__":
    unittest.main()
