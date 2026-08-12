"""Pure reducer tests for lease lifecycle and session binding."""

from __future__ import annotations

import unittest

from peerhub.core.errors import StaleRevisionError
from peerhub.core.execution import ExecutionCertainty
from peerhub.core.protocol import CommandID
from peerhub.dispatch.contract import (
    LeaseCloseRequest,
    LeaseCreateRequest,
    LeaseFenceTuple,
    LeaseRenewRequest,
    LeaseReservationRequest,
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
    reserve_lease,
    resume_session_binding,
    validate_lease_fence,
)


class TestDispatchModelReducers(unittest.TestCase):
    def setUp(self) -> None:
        self.process_id = ProcessBirthIdentity(
            pid=1234,
            process_creation_time=5000,
        )
        self.command_id = CommandID("command-01")
        self.fence = LeaseFenceTuple(
            session_id="session-01",
            lease_id="lease-01",
            fencing_token=1,
            revision=1,
            owner_principal_id="principal-ag",
            owner_instance_id="instance-ag-01",
            owner_process_birth_identity=self.process_id,
            command_id=self.command_id,
            authority_epoch=1,
            attempt_id="attempt-01",
            owner_peer_id="ag",
        )
        self.create_req = LeaseCreateRequest(
            session_id="session-01",
            owner_principal_id="principal-ag",
            owner_instance_id="instance-ag-01",
            owner_process_birth_identity=self.process_id,
            heartbeat_timeout_ms=5000,
            command_id=self.command_id,
            attempt_id="attempt-01",
            authority_epoch=1,
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
            command_id=self.command_id,
            authority_epoch=1,
            attempt_id="attempt-01",
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

    def test_command_attempt_and_epoch_are_fenced(self) -> None:
        bad_fence = LeaseFenceTuple(
            session_id=self.fence.session_id,
            lease_id=self.fence.lease_id,
            fencing_token=self.fence.fencing_token,
            revision=self.fence.revision,
            owner_principal_id=self.fence.owner_principal_id,
            owner_instance_id=self.fence.owner_instance_id,
            owner_process_birth_identity=self.process_id,
            command_id=CommandID("command-other"),
            authority_epoch=2,
            attempt_id="attempt-other",
        )
        is_match, mismatches = validate_lease_fence(
            self.fence,
            bad_fence,
        )
        self.assertFalse(is_match)
        self.assertIn("command_id", mismatches)
        self.assertIn("attempt_id", mismatches)
        self.assertIn("authority_epoch", mismatches)

    def test_owner_peer_id_is_descriptive_only(self) -> None:
        requester = LeaseFenceTuple(
            session_id=self.fence.session_id,
            lease_id=self.fence.lease_id,
            fencing_token=self.fence.fencing_token,
            revision=self.fence.revision,
            owner_principal_id=self.fence.owner_principal_id,
            owner_instance_id=self.fence.owner_instance_id,
            owner_process_birth_identity=self.process_id,
            command_id=self.command_id,
            authority_epoch=1,
            attempt_id="attempt-01",
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
        self.assertEqual(lease.fence.command_id, self.command_id)
        self.assertEqual(lease.fence.attempt_id, "attempt-01")

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
            command_id=self.command_id,
            authority_epoch=1,
            attempt_id="attempt-01",
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

    def test_recovery_identity_mismatch_has_no_effect_certainty(
        self,
    ) -> None:
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

    def test_recovery_never_spawned_is_abandoned_pre_spawn(self) -> None:
        """DP-06: a lease reserved but never reaching a process-identity-
        bearing state (record_start_uncertain() leaves it RESERVED with no
        process identity) recovers as ABANDONED_PRE_SPAWN, not
        IDENTITY_MISMATCH -- there is no recorded identity to mismatch
        against. No automatic replay; MAY_HAVE_STARTED per DP-06."""
        reservation_req = LeaseReservationRequest(
            session_id="session-01",
            owner_principal_id="principal-ag",
            owner_instance_id="instance-ag-01",
            heartbeat_timeout_ms=5000,
            command_id=self.command_id,
            authority_epoch=1,
            owner_peer_id="ag",
        )
        lease = reserve_lease(
            reservation_req,
            lease_id="lease-01",
            fencing_token=1,
            created_at=1000,
        )
        self.assertEqual(lease.state, LeaseState.RESERVED)
        self.assertIsNone(lease.fence.owner_process_birth_identity)
        updated, receipt = expire_and_recover_lease(
            lease,
            recovery_receipt_id="rec-03",
            recovery_actor_principal_id="recovery-agent",
            trigger=RecoveryTrigger.EXPLICIT_RECOVERY_REQUEST,
            evidence_digest="sha256:test",
            policy_id="pol-01",
            policy_revision=1,
            detected_at=6000,
            is_process_alive=False,
            process_identity_matches=False,
        )
        self.assertEqual(
            updated.state,
            LeaseState.ABANDONED_PRE_SPAWN,
        )
        self.assertEqual(
            receipt.decision,
            RecoveryDecision.MARK_INTERRUPTED,
        )
        self.assertEqual(
            receipt.external_effect_certainty,
            ExecutionCertainty.MAY_HAVE_STARTED,
        )

    def test_recovery_never_spawned_takes_precedence_over_stale_identity_match_flag(
        self,
    ) -> None:
        """A caller passing process_identity_matches=True alongside a null
        owner_process_birth_identity is a meaningless combination (there is
        no stored identity for anything to have matched) -- the
        ABANDONED_PRE_SPAWN branch must still win, not silently fall through
        to a FENCE_AND_CLOSE/dead-process outcome (cx.effort review finding,
        2026-08-02: locks in the elif's precedence explicitly)."""
        reservation_req = LeaseReservationRequest(
            session_id="session-01",
            owner_principal_id="principal-ag",
            owner_instance_id="instance-ag-01",
            heartbeat_timeout_ms=5000,
            command_id=self.command_id,
            authority_epoch=1,
            owner_peer_id="ag",
        )
        lease = reserve_lease(
            reservation_req,
            lease_id="lease-01",
            fencing_token=1,
            created_at=1000,
        )
        updated, receipt = expire_and_recover_lease(
            lease,
            recovery_receipt_id="rec-04",
            recovery_actor_principal_id="recovery-agent",
            trigger=RecoveryTrigger.EXPLICIT_RECOVERY_REQUEST,
            evidence_digest="sha256:test",
            policy_id="pol-01",
            policy_revision=1,
            detected_at=6000,
            is_process_alive=True,
            process_identity_matches=True,
        )
        self.assertEqual(
            updated.state,
            LeaseState.ABANDONED_PRE_SPAWN,
        )
        self.assertEqual(
            receipt.decision,
            RecoveryDecision.MARK_INTERRUPTED,
        )

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
        self.assertEqual(
            updated.state,
            SessionBindingState.ACTIVE,
        )
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
        self.assertEqual(
            updated.state,
            SessionBindingState.STALE,
        )
        self.assertEqual(updated.revision, 2)


class TestSessionRotationKey(unittest.TestCase):
    def test_session_rotation_key_generation_id_validation(self):
        from peerhub.dispatch.contract import SessionRotationKey
        
        # Valid
        SessionRotationKey(
            workspace_scope_id="ws-1",
            instance_id="inst-1",
            profile_id="prof-1",
            conversation_scope="global",
            generation_id=1,
        )
        
        # Invalid (0)
        with self.assertRaisesRegex(ValueError, "generation_id must be an integer >= 1"):
            SessionRotationKey(
                workspace_scope_id="ws-1",
                instance_id="inst-1",
                profile_id="prof-1",
                conversation_scope="global",
                generation_id=0,
            )
        
        # Invalid (negative)
        with self.assertRaisesRegex(ValueError, "generation_id must be an integer >= 1"):
            SessionRotationKey(
                workspace_scope_id="ws-1",
                instance_id="inst-1",
                profile_id="prof-1",
                conversation_scope="global",
                generation_id=-1,
            )


from peerhub.dispatch.contract import ExecutionOutcome
from peerhub.core.execution import ExecutionCertainty

class TestClassifyAttemptFailure(unittest.TestCase):
    def _execution(self, exit_code: int | None = 1) -> ExecutionOutcome:
        return ExecutionOutcome(
            started=True,
            exit_code=exit_code,
            timed_out=False,
            cancelled=False,
            execution_certainty=ExecutionCertainty.TERMINAL,
        )

    def _protocol(self, failure: ErrorCode | None = None) -> ProtocolAssessment:
        from peerhub.adapters.contract import ProtocolAssessment
        return ProtocolAssessment(
            parsed=failure is None,
            response_present=failure is None,
            vendor_completion_marker=None,
            suspected_truncation=False,
            protocol_failure=failure,
        )

    def _decoded_event(
        self,
        kind: str,
        payload: dict[str, object],
    ):
        from peerhub.adapters.contract import DecodedOutput, DecoderEvent, DecoderEventKind
        return DecodedOutput(
            canonical_text="",
            canonical_lines=(),
            events=(DecoderEvent(kind=DecoderEventKind(kind), payload=payload),),
        )

    def test_all_five_terminal_rows_are_total(self) -> None:
        from peerhub.dispatch.model import classify_attempt_failure
        from peerhub.dispatch.contract import TerminalClassification
        from peerhub.core.protocol import ErrorCode, ErrorPhase
        cases = [
            (TerminalClassification.START_UNCERTAIN, ErrorCode.START_UNCERTAIN),
            (TerminalClassification.SILENCE_TIMEOUT, ErrorCode.SILENCE_TIMEOUT),
            (TerminalClassification.PROCESS_TIMEOUT, ErrorCode.PROCESS_TIMEOUT),
            (TerminalClassification.EXIT_NON_ZERO, ErrorCode.INTERNAL_ERROR),
            (TerminalClassification.OUTPUT_LIMIT_EXCEEDED, ErrorCode.PROCESS_KILLED),
        ]
        for terminal, expected_code in cases:
            with self.subTest(terminal=terminal):
                result = classify_attempt_failure(
                    terminal_classification=terminal,
                    execution=self._execution(),
                    protocol=self._protocol(),
                    decoded_output=None,
                )
                self.assertIsNotNone(result)
                self.assertIs(result.code, expected_code)
                self.assertIs(result.phase, ErrorPhase.POST_SPAWN)
                self.assertIsNone(result.operational_failure_category)

    def test_none_with_protocol_failure_maps_to_assessment(self) -> None:
        from peerhub.dispatch.model import classify_attempt_failure
        from peerhub.core.protocol import ErrorCode, ErrorPhase
        result = classify_attempt_failure(
            terminal_classification=None,
            execution=self._execution(exit_code=0),
            protocol=self._protocol(ErrorCode.PROTOCOL_ASSESSMENT_FAILED),
            decoded_output=None,
        )
        self.assertIsNotNone(result)
        self.assertIs(result.code, ErrorCode.PROTOCOL_ASSESSMENT_FAILED)
        self.assertIs(result.phase, ErrorPhase.ASSESSMENT)

    def test_none_without_protocol_failure_returns_none(self) -> None:
        from peerhub.dispatch.model import classify_attempt_failure
        result = classify_attempt_failure(
            terminal_classification=None,
            execution=self._execution(exit_code=0),
            protocol=self._protocol(),
            decoded_output=None,
        )
        self.assertIsNone(result)

    def test_normalized_vendor_error_reaches_proposed_codes(self) -> None:
        from peerhub.dispatch.model import classify_attempt_failure
        from peerhub.dispatch.contract import TerminalClassification
        from peerhub.core.protocol import ErrorCode
        cases = [
            ("session_invalid", ErrorCode.SESSION_INVALID),
            ("invocation_plan_rejected", ErrorCode.INVOCATION_PLAN_REJECTED),
        ]
        for kind, expected_code in cases:
            with self.subTest(kind=kind):
                decoded = self._decoded_event(
                    "VENDOR_ERROR",
                    {
                        "normalized_kind": kind,
                        "evidence_source": "known_terminal_pattern",
                    },
                )
                result = classify_attempt_failure(
                    terminal_classification=TerminalClassification.EXIT_NON_ZERO,
                    execution=self._execution(),
                    protocol=self._protocol(),
                    decoded_output=decoded,
                )
                self.assertIsNotNone(result)
                self.assertIs(result.code, expected_code)

    def test_normalized_operational_error_refines_category_only(self) -> None:
        from peerhub.dispatch.model import classify_attempt_failure
        from peerhub.dispatch.contract import TerminalClassification
        from peerhub.core.protocol import ErrorCode, OperationalFailureCategory
        decoded = self._decoded_event(
            "VENDOR_ERROR",
            {
                "normalized_kind": "auth_unavailable",
                "evidence_source": "structured_vendor_output",
            },
        )
        result = classify_attempt_failure(
            terminal_classification=TerminalClassification.EXIT_NON_ZERO,
            execution=self._execution(),
            protocol=self._protocol(),
            decoded_output=decoded,
        )
        self.assertIsNotNone(result)
        self.assertIs(result.code, ErrorCode.INTERNAL_ERROR)
        self.assertIs(
            result.operational_failure_category,
            OperationalFailureCategory.AUTH_UNAVAILABLE,
        )

    def test_unnormalized_text_cannot_trigger_stable_refinement(self) -> None:
        from peerhub.dispatch.model import classify_attempt_failure
        from peerhub.dispatch.contract import TerminalClassification
        from peerhub.core.protocol import ErrorCode
        cases = [
            self._decoded_event("ASSISTANT_TEXT", {"text": "invalid model operand"}),
            self._decoded_event("VENDOR_ERROR", {"text": "invalid model operand"}),
            self._decoded_event("VENDOR_ERROR", {"normalized_kind": "invocation_plan_rejected"}),
        ]
        for decoded in cases:
            with self.subTest(decoded=decoded):
                result = classify_attempt_failure(
                    terminal_classification=TerminalClassification.EXIT_NON_ZERO,
                    execution=self._execution(),
                    protocol=self._protocol(),
                    decoded_output=decoded,
                )
                self.assertIsNotNone(result)
                self.assertIs(result.code, ErrorCode.INTERNAL_ERROR)
                self.assertIsNone(result.operational_failure_category)


if __name__ == "__main__":
    unittest.main()
