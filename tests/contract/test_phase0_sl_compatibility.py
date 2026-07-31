"""Compatibility tests adapting Phase 0 SL-01..06 scenarios into Slice 2 production types."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from peerhub.core.context import PathLayout, RuntimeContext
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

        # Apply Slice 2 migration to SQLite connection
        conn = self.store._connect()
        try:
            migration = (
                Path("peerhub/persistence/migrations/0002_dispatch_session_lease.sql")
                .read_text(encoding="utf-8")
            )
            conn.executescript(migration)
        finally:
            conn.close()

        self.service = DispatchService(
            self.store,
            clock=self.context.clock,
            ids=self.context.ids,
        )

        self.pid_a = ProcessBirthIdentity(pid=1001, process_creation_time=5000)
        self.pid_b = ProcessBirthIdentity(pid=1002, process_creation_time=6000)

        self.key_a = SessionBindingKey(
            workspace_scope_id="ws-01",
            instance_id="instance-ag-01",
            profile_id="prof-a",
            conversation_scope="conv-a",
        )

    def tearDown(self) -> None:
        self.store.close()
        self.temp_dir.cleanup()

    def test_sl01_atomic_session_and_lease_creation(self) -> None:
        req = LeaseCreateRequest(
            session_id="session-SL-01",
            owner_principal_id="principal-ag",
            owner_instance_id="instance-ag-01",
            owner_process_birth_identity=self.pid_a,
            heartbeat_timeout_ms=5000,
            owner_peer_id="ag",
        )
        binding, lease = self.service.create_session_and_lease(
            self.key_a,
            req,
            adapter_fingerprint="fp-sl01",
            readiness_binding="rb-sl01",
        )
        self.assertEqual(binding.session_id, "session-SL-01")
        self.assertEqual(lease.state, LeaseState.ACTIVE)
        self.assertEqual(lease.fence.fencing_token, 1)

    def test_sl04_concurrent_leases_renew_close_and_stale_cas(self) -> None:
        req_a = LeaseCreateRequest(
            session_id="session-SL-04-A",
            owner_principal_id="principal-ag",
            owner_instance_id="instance-ag-01",
            owner_process_birth_identity=self.pid_a,
            heartbeat_timeout_ms=5000,
            owner_peer_id="ag",
        )
        key_b = SessionBindingKey(
            workspace_scope_id="ws-01",
            instance_id="instance-ag-02",
            profile_id="prof-b",
            conversation_scope="conv-b",
        )
        req_b = LeaseCreateRequest(
            session_id="session-SL-04-B",
            owner_principal_id="principal-ag",
            owner_instance_id="instance-ag-02",
            owner_process_birth_identity=self.pid_b,
            heartbeat_timeout_ms=5000,
            owner_peer_id="ag",
        )

        _, lease_a = self.service.create_session_and_lease(self.key_a, req_a, "fp", "rb")
        _, lease_b = self.service.create_session_and_lease(key_b, req_b, "fp", "rb")

        # Renew Lease A -> Lease B remains unaffected
        renew_a = LeaseRenewRequest(lease_id=lease_a.lease_id, fence=lease_a.fence)
        renewed_a = self.service.renew_lease(renew_a, heartbeat_timeout_ms=5000)
        self.assertEqual(renewed_a.state, LeaseState.RENEWED)
        self.assertEqual(renewed_a.fence.revision, 2)

        # Close Lease B -> Lease A remains active
        close_b = LeaseCloseRequest(lease_id=lease_b.lease_id, fence=lease_b.fence)
        closed_b = self.service.close_lease(close_b)
        self.assertEqual(closed_b.state, LeaseState.RELEASED)

        # Stale CAS replay on Lease A (using initial fence token 1) is rejected
        stale_fence_req = LeaseFenceCheckRequest(requester_fence=lease_a.fence)
        is_valid, mismatches = self.service.check_lease_fence(stale_fence_req)
        self.assertFalse(is_valid)
        self.assertIn("fencing_token", mismatches)

    def test_sl05_authenticated_principal_fencing(self) -> None:
        req = LeaseCreateRequest(
            session_id="session-SL-05",
            owner_principal_id="principal-ag",
            owner_instance_id="instance-ag-01",
            owner_process_birth_identity=self.pid_a,
            heartbeat_timeout_ms=5000,
            owner_peer_id="ag",
        )
        _, lease = self.service.create_session_and_lease(self.key_a, req, "fp", "rb")

        # Spoofed requester (matching instance/peer, wrong principal)
        spoofed_fence = LeaseFenceTuple(
            session_id=lease.fence.session_id,
            lease_id=lease.lease_id,
            fencing_token=lease.fence.fencing_token,
            revision=lease.fence.revision,
            owner_principal_id="principal-cx-SPOOF",
            owner_instance_id=lease.fence.owner_instance_id,
            owner_process_birth_identity=self.pid_a,
            owner_peer_id="ag",
        )
        check_req = LeaseFenceCheckRequest(requester_fence=spoofed_fence)
        is_valid, mismatches = self.service.check_lease_fence(check_req)
        self.assertFalse(is_valid)
        self.assertIn("owner_principal_id", mismatches)

    def test_sl06_recovery_receipt_generation(self) -> None:
        req = LeaseCreateRequest(
            session_id="session-SL-06",
            owner_principal_id="principal-ag",
            owner_instance_id="instance-ag-01",
            owner_process_birth_identity=self.pid_a,
            heartbeat_timeout_ms=5000,
            owner_peer_id="ag",
        )
        _, lease = self.service.create_session_and_lease(self.key_a, req, "fp", "rb")

        recovered_lease, receipt = self.service.recover_lease(
            lease.lease_id,
            recovery_actor_principal_id="principal-recovery-agent",
            trigger=RecoveryTrigger.PROCESS_BIRTH_MISMATCH,
            evidence_digest="sha256:evidence-sl06",
            policy_id="policy-sl06",
            policy_revision=1,
            is_process_alive=True,
            process_identity_matches=False,
        )

        self.assertEqual(recovered_lease.state, LeaseState.IDENTITY_MISMATCH)
        self.assertEqual(receipt.decision, RecoveryDecision.REJECT_AND_QUARANTINE)
        self.assertEqual(receipt.post_revision, 2)
        self.assertEqual(receipt.post_fencing_token, 2)


if __name__ == "__main__":
    unittest.main()
