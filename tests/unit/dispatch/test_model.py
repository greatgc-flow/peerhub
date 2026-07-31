"""Pure reducer tests for lease lifecycle and session binding."""

from __future__ import annotations

import unittest

from peerhub.core.errors import StaleRevisionError
from peerhub.core.execution import ExecutionCertainty
from peerhub.dispatch.contract import (
    LeaseCloseRequest,
    LeaseCreateRequest,
    LeaseFenceTuple,
    LeaseRenewRequest,
    LeaseState,
    ProcessBirthIdentity,
    RecoveryDecision,
    RecoveryTrigger,
    SessionBindingKey,
    SessionBindingState,
    SessionResumeRequest,
)
from peerhub.dispatch.model import (
    close_lease,
    create_lease,
    create_session_binding,
    expire_and_recover_lease,
    renew_lease,
    resume_session_binding,
    validate_lease_fence,
)


class TestDispatchModelReducers(unittest.TestCase):
    def setUp(self) -> None:
        self.process_id = ProcessBirthIdentity(
            pid=1234,
            process_creation_time=5000,
        )
        self.fence = LeaseFenceTuple(
            session_id="session-01",
            lease_id="lease-01",
            fencing_token=1,
            revision=1,
            owner_principal_id="principal-ag",
            owner_instance_id="instance-ag-01",
            owner_process_birth_identity=self.process_id,
            owner_peer_id="ag",
        )
        self.create_req = LeaseCreateRequest(
            session_id="session-01",
            owner_principal_id="principal-ag",
            owner_instance_id="instance-ag-01",
            owner_process_birth_identity=self.process_id,
            heartbeat_timeout_ms=5000,
            owner_peer_id="ag",
        )
        self.binding_key = SessionBindingKey(
            workspace_scope_id="ws-01",
            instance_id="instance-ag-01",
            profile_id="prof-default",
            conversation_scope="conv-01",
        )

    def test_validate_lease_fence_match(self) -> None:
        is_match, mismatches = validate_lease_fence(
            self.fence,
            self.fence,
        )
        self.assertTrue(is_match)
        self.assertEqual(mismatches, ())

    def test_validate_lease_fence_mismatch_detected(self) -> None:
        other_process = ProcessBirthIdentity(
            pid=9999,
            process_creation_time=5000,
        )
        bad_fence = LeaseFenceTuple(
            session_id="session-01",
            lease_id="lease-01",
            fencing_token=1,
            revision=1,
            owner_principal_id="principal-cx",
            owner_instance_id="instance-ag-01",
            owner_process_birth_identity=other_process,
        )
        is_match, mismatches = validate_lease_fence(
            self.fence,
            bad_fence,
        )
        self.assertFalse(is_match)
        self.assertIn("owner_principal_id", mismatches)
        self.assertIn(
            "owner_process_birth_identity.pid",
            mismatches,
        )

    def test_owner_peer_id_is_descriptive_only(self) -> None:
        requester = LeaseFenceTuple(
            session_id=self.fence.session_id,
            lease_id=self.fence.lease_id,
            fencing_token=self.fence.fencing_token,
            revision=self.fence.revision,
            owner_principal_id=self.fence.owner_principal_id,
            owner_instance_id=self.fence.owner_instance_id,
            owner_process_birth_identity=self.process_id,
            owner_peer_id="different-descriptive-peer",
        )

        is_match, mismatches = validate_lease_fence(
            self.fence,
            requester,
        )

        self.assertTrue(is_match)
        self.assertEqual(mismatches, ())

    def test_create_lease_reducer(self) -> None:
        lease = create_lease(
            self.create_req,
            lease_id="lease-01",
            created_at=1000,
        )
        self.assertEqual(lease.lease_id, "lease-01")
        self.assertEqual(lease.state, LeaseState.ACTIVE)
        self.assertEqual(lease.heartbeat_expires_at, 6000)
        self.assertEqual(lease.fence.fencing_token, 1)

    def test_renew_lease_reducer_advances_revision(self) -> None:
        lease = create_lease(
            self.create_req,
            lease_id="lease-01",
            created_at=1000,
        )
        renew_req = LeaseRenewRequest(
            lease_id="lease-01",
            fence=lease.fence,
        )
        renewed = renew_lease(
            lease,
            renew_req,
            heartbeat_timeout_ms=5000,
            updated_at=2000,
        )
        self.assertEqual(renewed.state, LeaseState.RENEWED)
        self.assertEqual(renewed.fence.revision, 2)
        self.assertEqual(renewed.fence.fencing_token, 2)
        self.assertEqual(renewed.heartbeat_expires_at, 7000)

    def test_renew_lease_stale_revision_raises(self) -> None:
        lease = create_lease(
            self.create_req,
            lease_id="lease-01",
            created_at=1000,
        )
        stale_fence = LeaseFenceTuple(
            session_id="session-01",
            lease_id="lease-01",
            fencing_token=1,
            revision=99,
            owner_principal_id="principal-ag",
            owner_instance_id="instance-ag-01",
            owner_process_birth_identity=self.process_id,
        )
        renew_req = LeaseRenewRequest(
            lease_id="lease-01",
            fence=stale_fence,
        )
        with self.assertRaises(StaleRevisionError):
            renew_lease(
                lease,
                renew_req,
                heartbeat_timeout_ms=5000,
                updated_at=2000,
            )

    def test_close_lease_reducer(self) -> None:
        lease = create_lease(
            self.create_req,
            lease_id="lease-01",
            created_at=1000,
        )
        close_req = LeaseCloseRequest(
            lease_id="lease-01",
            fence=lease.fence,
        )
        closed = close_lease(
            lease,
            close_req,
            updated_at=3000,
        )
        self.assertEqual(closed.state, LeaseState.RELEASED)
        self.assertEqual(closed.fence.revision, 2)

    def test_recovery_alive_matching_is_still_uncertain(self) -> None:
        lease = create_lease(
            self.create_req,
            lease_id="lease-01",
            created_at=1000,
        )
        updated, receipt = expire_and_recover_lease(
            lease,
            recovery_receipt_id="rec-01",
            recovery_actor_principal_id="recovery-agent",
            trigger=RecoveryTrigger.HEARTBEAT_TIMEOUT,
            evidence_digest="sha256:test",
            policy_id="pol-01",
            policy_revision=1,
            detected_at=6000,
            is_process_alive=True,
            process_identity_matches=True,
        )
        self.assertEqual(updated.state, LeaseState.FENCED)
        self.assertEqual(
            receipt.decision,
            RecoveryDecision.FENCE_AND_CLOSE,
        )
        self.assertEqual(
            receipt.external_effect_certainty,
            ExecutionCertainty.MAY_HAVE_STARTED,
        )
        self.assertEqual(receipt.post_revision, 2)
        self.assertEqual(receipt.post_fencing_token, 2)

    def test_recovery_dead_matching_is_terminal(self) -> None:
        lease = create_lease(
            self.create_req,
            lease_id="lease-01",
            created_at=1000,
        )
        _, receipt = expire_and_recover_lease(
            lease,
            recovery_receipt_id="rec-dead",
            recovery_actor_principal_id="recovery-agent",
            trigger=RecoveryTrigger.HEARTBEAT_TIMEOUT,
            evidence_digest="sha256:dead",
            policy_id="pol-01",
            policy_revision=1,
            detected_at=6000,
            is_process_alive=False,
            process_identity_matches=True,
        )
        self.assertEqual(
            receipt.decision,
            RecoveryDecision.MARK_INTERRUPTED,
        )
        self.assertEqual(
            receipt.external_effect_certainty,
            ExecutionCertainty.TERMINAL,
        )

    def test_recovery_identity_mismatch_has_no_effect_certainty(self) -> None:
        lease = create_lease(
            self.create_req,
            lease_id="lease-01",
            created_at=1000,
        )
        updated, receipt = expire_and_recover_lease(
            lease,
            recovery_receipt_id="rec-02",
            recovery_actor_principal_id="recovery-agent",
            trigger=RecoveryTrigger.PROCESS_BIRTH_MISMATCH,
            evidence_digest="sha256:test",
            policy_id="pol-01",
            policy_revision=1,
            detected_at=6000,
            is_process_alive=True,
            process_identity_matches=False,
        )
        self.assertEqual(
            updated.state,
            LeaseState.IDENTITY_MISMATCH,
        )
        self.assertEqual(
            receipt.decision,
            RecoveryDecision.REJECT_AND_QUARANTINE,
        )
        self.assertIsNone(receipt.external_effect_certainty)

    def test_session_resume_exact_match(self) -> None:
        binding = create_session_binding(
            self.binding_key,
            session_id="session-01",
            current_lease_id="lease-01",
            adapter_fingerprint="fp-v1",
            readiness_binding="rb-v1",
            session_generation=1,
            created_at=1000,
        )
        request = SessionResumeRequest(
            key=self.binding_key,
            requested_session_id="session-01",
            adapter_fingerprint="fp-v1",
            readiness_binding="rb-v1",
            session_generation=1,
        )
        is_compatible, updated = resume_session_binding(
            binding,
            request,
            updated_at=2000,
        )
        self.assertTrue(is_compatible)
        self.assertEqual(updated.state, SessionBindingState.ACTIVE)
        self.assertEqual(updated.revision, 1)

    def test_session_resume_mismatch_advances_revision(self) -> None:
        binding = create_session_binding(
            self.binding_key,
            session_id="session-01",
            current_lease_id="lease-01",
            adapter_fingerprint="fp-v1",
            readiness_binding="rb-v1",
            session_generation=1,
            created_at=1000,
        )
        request = SessionResumeRequest(
            key=self.binding_key,
            requested_session_id="session-01",
            adapter_fingerprint="fp-CHANGED",
            readiness_binding="rb-v1",
            session_generation=1,
        )
        is_compatible, updated = resume_session_binding(
            binding,
            request,
            updated_at=2000,
        )
        self.assertFalse(is_compatible)
        self.assertEqual(updated.state, SessionBindingState.STALE)
        self.assertEqual(updated.revision, 2)


if __name__ == "__main__":
    unittest.main()
