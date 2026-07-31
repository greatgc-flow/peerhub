"""Unit tests for pure Slice 3 request and attempt reducers."""

from __future__ import annotations

import hashlib
import unittest

from peerhub.core.errors import InvalidMutationError
from peerhub.core.execution import ExecutionCertainty
from peerhub.core.protocol import (
    PROTOCOL_MAJOR,
    PROTOCOL_MINOR,
    SCHEMA_VERSION,
    CommandEnvelope,
    CommandID,
)
from peerhub.dispatch.contract import (
    AskResult,
    CompletionAssessment,
    CompletionAssessmentState,
    CompletionContract,
    CompletionContractKind,
    ExecutionOutcome,
    LeaseReservationRequest,
    ProcessBirthIdentity,
    ProtocolAssessment,
    RequestState,
    SessionBindingKey,
)
from peerhub.dispatch.model import (
    admit_request,
    authorize_retry,
    begin_assessment,
    complete_attempt,
    create_attempt,
    create_session_binding,
    prepare_request,
    record_dispatch_intent,
    record_running,
    record_start_uncertain,
    reserve_lease,
    validate_submission,
)


class TestRequestAttemptModel(unittest.TestCase):
    def setUp(self) -> None:
        self.contract = CompletionContract(
            contract_id="contract-01",
            kind=CompletionContractKind.FIELD_REQUIRED,
            requirements=({"field": "status"},),
            replay_safe=False,
        )
        self.envelope = CommandEnvelope(
            protocol_major=PROTOCOL_MAJOR,
            protocol_minor=PROTOCOL_MINOR,
            schema_version=SCHEMA_VERSION,
            client_request_id="client-request-01",
            correlation_id="correlation-01",
            client_id="client-01",
            actor_id="actor-01",
            scope={
                "workspace_id": "workspace-01",
                "home_id": "home-01",
            },
            method="peer.ask",
            params={"prompt": "hello"},
            idempotency_key="idempotency-01",
            expected_policy_revision=3,
            expected_configuration_revision=4,
            client_timestamp=10,
        )
        self.command_id = CommandID("command-01")
        self.submission = validate_submission(
            self.envelope,
            authenticated_principal="principal-01",
            completion_contract=self.contract,
            state_changing=True,
        )
        self.request, _, _, _ = admit_request(
            self.submission,
            command_id=self.command_id,
            admission_receipt_id="admission-01",
            lease_id="lease-01",
            policy_revision=3,
            configuration_revision=4,
            selected_peer_instance_id="instance-01",
            selected_profile_id="profile-01",
            route_decision_digest=hashlib.sha256(
                b"route"
            ).hexdigest(),
            admitted_at=100,
        )
        self.lease = reserve_lease(
            LeaseReservationRequest(
                session_id="session-01",
                owner_principal_id="principal-01",
                owner_instance_id="instance-01",
                heartbeat_timeout_ms=5_000,
                command_id=self.command_id,
                authority_epoch=8,
            ),
            lease_id="lease-01",
            fencing_token=17,
            created_at=100,
        )
        self.binding = create_session_binding(
            SessionBindingKey(
                workspace_scope_id="workspace-01",
                instance_id="instance-01",
                profile_id="profile-01",
                conversation_scope="conversation-01",
            ),
            session_id="session-01",
            current_lease_id="lease-01",
            adapter_fingerprint="adapter-sha",
            readiness_binding="readiness-sha",
            session_generation=1,
            created_at=100,
        )

    def _prepared(self):
        prepared = prepare_request(
            self.request,
            session_binding=self.binding,
            lease=self.lease,
            updated_at=101,
        )
        attempt = create_attempt(
            prepared,
            self.lease,
            attempt_id="attempt-01",
            attempt_number=1,
            created_at=102,
        )
        return prepared, attempt

    def _retry_lease(
        self,
        command_id: CommandID | None = None,
        *,
        lease_id: str = "lease-02",
        created_at: int = 105,
    ):
        return reserve_lease(
            LeaseReservationRequest(
                session_id="session-01",
                owner_principal_id="principal-01",
                owner_instance_id="instance-01",
                heartbeat_timeout_ms=5_000,
                command_id=command_id or self.command_id,
                authority_epoch=8,
            ),
            lease_id=lease_id,
            fencing_token=18,
            created_at=created_at,
        )

    def test_prepare_and_create_attempt(self) -> None:
        prepared, attempt = self._prepared()
        self.assertEqual(prepared.state, RequestState.PREPARED)
        self.assertEqual(attempt.state, RequestState.PREPARED)
        self.assertEqual(attempt.attempt_number, 1)
        self.assertEqual(
            attempt.execution_certainty,
            ExecutionCertainty.NOT_STARTED,
        )

    def test_dispatch_intent_binds_attempt_and_uncertainty(
        self,
    ) -> None:
        prepared, attempt = self._prepared()
        request, intent, lease = record_dispatch_intent(
            prepared,
            attempt,
            self.lease,
            updated_at=103,
        )
        self.assertEqual(
            request.state,
            RequestState.DISPATCH_INTENT,
        )
        self.assertEqual(
            intent.execution_certainty,
            ExecutionCertainty.MAY_HAVE_STARTED,
        )
        self.assertEqual(
            lease.fence.attempt_id,
            "attempt-01",
        )
        self.assertIsNone(
            lease.fence.owner_process_birth_identity
        )

    def test_running_requires_attempt_bound_lease(self) -> None:
        prepared, attempt = self._prepared()
        with self.assertRaises(InvalidMutationError):
            record_running(
                prepared,
                attempt,
                self.lease,
                process_identity=ProcessBirthIdentity(
                    pid=1234,
                    process_creation_time=200,
                ),
                updated_at=103,
            )

    def test_full_verified_transition(self) -> None:
        prepared, attempt = self._prepared()
        request, intent, lease = record_dispatch_intent(
            prepared,
            attempt,
            self.lease,
            updated_at=103,
        )
        request, running, lease = record_running(
            request,
            intent,
            lease,
            process_identity=ProcessBirthIdentity(
                pid=1234,
                process_creation_time=200,
            ),
            updated_at=104,
        )
        request, assessing = begin_assessment(
            request,
            running,
            updated_at=105,
        )
        result = AskResult(
            execution=ExecutionOutcome(
                started=True,
                exit_code=0,
                timed_out=False,
                cancelled=False,
                execution_certainty=(
                    ExecutionCertainty.TERMINAL
                ),
            ),
            protocol=ProtocolAssessment(
                parsed=True,
                response_present=True,
                vendor_completion_marker=True,
                suspected_truncation=False,
                protocol_failure=None,
            ),
            completion=CompletionAssessment(
                state=CompletionAssessmentState.VERIFIED,
                evidence_refs=("evidence-01",),
            ),
            policy_revision=3,
        )
        request, completed = complete_attempt(
            request,
            assessing,
            result=result,
            updated_at=106,
        )
        self.assertEqual(
            request.state,
            RequestState.SUCCEEDED_VERIFIED,
        )
        self.assertEqual(
            completed.state,
            RequestState.SUCCEEDED_VERIFIED,
        )
        self.assertEqual(lease.state.value, "ACTIVE")

    def test_start_uncertain_is_not_blindly_retried(
        self,
    ) -> None:
        prepared, attempt = self._prepared()
        request, intent, _ = record_dispatch_intent(
            prepared,
            attempt,
            self.lease,
            updated_at=103,
        )
        request, uncertain = record_start_uncertain(
            request,
            intent,
            updated_at=104,
        )

        with self.assertRaises(InvalidMutationError):
            authorize_retry(
                request,
                uncertain,
                self._retry_lease(),
                reconciliation_complete=False,
                updated_at=105,
            )

    def test_reconciliation_allows_uncertain_retry(
        self,
    ) -> None:
        prepared, attempt = self._prepared()
        request, intent, _ = record_dispatch_intent(
            prepared,
            attempt,
            self.lease,
            updated_at=103,
        )
        request, uncertain = record_start_uncertain(
            request,
            intent,
            updated_at=104,
        )
        request, previous = authorize_retry(
            request,
            uncertain,
            self._retry_lease(),
            reconciliation_complete=True,
            updated_at=105,
        )

        self.assertEqual(request.state, RequestState.PREPARED)
        self.assertEqual(request.lease_id, "lease-02")
        self.assertEqual(
            previous.state,
            RequestState.INTERRUPTED,
        )
        self.assertTrue(previous.reconciliation_complete)

    def test_replay_safe_contract_allows_retry(self) -> None:
        replay_contract = CompletionContract(
            contract_id="contract-replay",
            kind=CompletionContractKind.FIELD_REQUIRED,
            requirements=({"field": "status"},),
            replay_safe=True,
        )
        replay_submission = validate_submission(
            self.envelope,
            authenticated_principal="principal-01",
            completion_contract=replay_contract,
            state_changing=True,
        )
        replay_request, _, _, _ = admit_request(
            replay_submission,
            command_id=CommandID("command-replay"),
            admission_receipt_id="admission-replay",
            lease_id="lease-replay",
            policy_revision=3,
            configuration_revision=4,
            selected_peer_instance_id="instance-01",
            selected_profile_id="profile-01",
            route_decision_digest=hashlib.sha256(
                b"route-replay"
            ).hexdigest(),
            admitted_at=200,
        )
        uncertain_request = replay_request.__class__(
            **{
                **replay_request.__dict__,
                "state": RequestState.START_UNCERTAIN,
                "revision": 2,
                "updated_at": 201,
            }
        )
        from peerhub.dispatch.contract import AttemptSnapshot

        uncertain_attempt = AttemptSnapshot(
            attempt_id="attempt-replay",
            command_id=replay_request.command_id,
            attempt_number=1,
            lease_id=replay_request.lease_id,
            state=RequestState.START_UNCERTAIN,
            execution_certainty=(
                ExecutionCertainty.MAY_HAVE_STARTED
            ),
            revision=1,
            created_at=200,
            updated_at=201,
        )
        retried, prior = authorize_retry(
            uncertain_request,
            uncertain_attempt,
            self._retry_lease(
                CommandID("command-replay"),
                lease_id="lease-replay-02",
                created_at=202,
            ),
            reconciliation_complete=False,
            updated_at=202,
        )
        self.assertEqual(retried.state, RequestState.PREPARED)
        self.assertEqual(retried.lease_id, "lease-replay-02")
        self.assertEqual(prior.state, RequestState.INTERRUPTED)


if __name__ == "__main__":
    unittest.main()
