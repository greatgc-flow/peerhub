"""Slice 3 compatibility tests for the frozen CJ and DP behaviors."""

from __future__ import annotations

import hashlib
import unittest
from dataclasses import replace

from peerhub.core.errors import (
    ActorUnauthorizedError,
    MissingIdempotencyKeyError,
)
from peerhub.core.execution import ExecutionCertainty
from peerhub.core.protocol import (
    PROTOCOL_MAJOR,
    PROTOCOL_MINOR,
    SCHEMA_VERSION,
    CommandEnvelope,
    CommandID,
    ErrorCode,
    EventEnvelope,
    canonical_json_bytes,
)
from peerhub.dispatch.contract import (
    AskResult,
    CompletionAssessment,
    CompletionAssessmentState,
    CompletionContract,
    CompletionContractKind,
    ExecutionOutcome,
    ProtocolAssessment,
    RequestState,
)
from peerhub.dispatch.model import (
    admit_request,
    canonical_payload_digest,
    complete_attempt,
    fail_pre_dispatch,
    validate_submission,
)


def _contract(
    *,
    replay_safe: bool = False,
) -> CompletionContract:
    return CompletionContract(
        contract_id="contract-01",
        kind=CompletionContractKind.FIELD_REQUIRED,
        requirements=(
            {
                "field": "status",
                "expected": "ok",
            },
        ),
        replay_safe=replay_safe,
    )


def _envelope(
    *,
    client_request_id: str = "client-request-01",
    correlation_id: str = "correlation-01",
    client_timestamp: int = 100,
    params: dict[str, object] | None = None,
    idempotency_key: str | None = "idem-01",
) -> CommandEnvelope:
    return CommandEnvelope(
        protocol_major=PROTOCOL_MAJOR,
        protocol_minor=PROTOCOL_MINOR,
        schema_version=SCHEMA_VERSION,
        client_request_id=client_request_id,
        correlation_id=correlation_id,
        client_id="client-01",
        actor_id="actor-01",
        scope={
            "workspace_id": "workspace-01",
            "home_id": "home-01",
        },
        method="peer.ask",
        params=params or {"prompt": "hello"},
        idempotency_key=idempotency_key,
        expected_policy_revision=7,
        expected_configuration_revision=11,
        client_timestamp=client_timestamp,
    )


class TestPhase0DpCjCompatibility(unittest.TestCase):
    def test_cj02_admission_mints_server_command_id(self) -> None:
        submission = validate_submission(
            _envelope(),
            authenticated_principal="principal-01",
            completion_contract=_contract(),
            state_changing=True,
        )
        request, client_binding, key_binding, receipt = (
            admit_request(
                submission,
                command_id=CommandID("server-command-01"),
                admission_receipt_id="admission-01",
                lease_id="lease-01",
                policy_revision=7,
                configuration_revision=11,
                selected_peer_instance_id="instance-01",
                selected_profile_id="profile-01",
                route_decision_digest=hashlib.sha256(
                    b"route"
                ).hexdigest(),
                admitted_at=200,
            )
        )

        self.assertEqual(
            request.command_id,
            CommandID("server-command-01"),
        )
        self.assertEqual(
            request.client_request_id,
            "client-request-01",
        )
        self.assertEqual(request.state, RequestState.ADMITTED)
        self.assertEqual(
            client_binding.command_id,
            request.command_id,
        )
        self.assertEqual(
            key_binding.command_id,
            request.command_id,
        )
        self.assertEqual(
            receipt.command_id,
            request.command_id,
        )

    def test_cj05_unauthorized_is_not_started(self) -> None:
        error = ActorUnauthorizedError("principal-denied")
        self.assertEqual(
            error.error_code,
            ErrorCode.ACTOR_UNAUTHORIZED,
        )
        self.assertNotIn("command_id", error.details)

        outcome = ExecutionOutcome(
            started=False,
            exit_code=None,
            timed_out=False,
            cancelled=False,
            execution_certainty=(
                ExecutionCertainty.NOT_STARTED
            ),
        )
        self.assertFalse(outcome.started)
        self.assertEqual(
            outcome.execution_certainty,
            ExecutionCertainty.NOT_STARTED,
        )

    def test_state_changing_submission_requires_key(self) -> None:
        with self.assertRaises(MissingIdempotencyKeyError):
            validate_submission(
                _envelope(idempotency_key=None),
                authenticated_principal="principal-01",
                completion_contract=_contract(),
                state_changing=True,
            )

    def test_digest_excludes_trace_and_timestamp_ids(self) -> None:
        first = _envelope()
        second = _envelope(
            client_request_id="client-request-CHANGED",
            correlation_id="correlation-CHANGED",
            client_timestamp=999,
        )

        first_digest = canonical_payload_digest(
            first,
            authenticated_principal="principal-01",
            completion_contract=_contract(),
        )
        second_digest = canonical_payload_digest(
            second,
            authenticated_principal="principal-01",
            completion_contract=_contract(),
        )
        self.assertEqual(first_digest, second_digest)

    def test_digest_changes_with_typed_params(self) -> None:
        first_digest = canonical_payload_digest(
            _envelope(params={"prompt": "one"}),
            authenticated_principal="principal-01",
            completion_contract=_contract(),
        )
        second_digest = canonical_payload_digest(
            _envelope(params={"prompt": "two"}),
            authenticated_principal="principal-01",
            completion_contract=_contract(),
        )
        self.assertNotEqual(first_digest, second_digest)

    def test_digest_formula_is_independently_pinned(self) -> None:
        envelope = _envelope(params={"ratio": 1e-6})
        self.assertEqual(
            canonical_payload_digest(
                envelope,
                authenticated_principal="principal-01",
                completion_contract=_contract(),
            ),
            "b8e4ed1c146743cad319afb1f0193de8"
            "ed6aeae162ca9d60797293868471153e",
        )

    def test_rfc8785_finite_float_vectors_are_pinned(self) -> None:
        self.assertEqual(
            canonical_json_bytes(
                {
                    "numbers": [
                        333333333.33333329,
                        1e30,
                        4.50,
                        2e-3,
                        1e-27,
                        -0.0,
                    ]
                }
            ),
            (
                b'{"numbers":[333333333.3333333,1e+30,4.5,'
                b"0.002,1e-27,0]}"
            ),
        )

        for non_finite in (
            float("nan"),
            float("inf"),
            float("-inf"),
        ):
            with self.assertRaises(ValueError):
                canonical_json_bytes({"number": non_finite})

    def test_protocol_event_id_requires_uuid4(self) -> None:
        valid_event_id = (
            "123e4567-e89b-42d3-a456-426614174000"
        )
        event = EventEnvelope(
            protocol_major=PROTOCOL_MAJOR,
            protocol_minor=PROTOCOL_MINOR,
            schema_version=SCHEMA_VERSION,
            event_id=valid_event_id,
            correlation_id="correlation-event",
            occurred_at=100,
            kind="ADMITTED",
            payload={"command_id": "command-01"},
        )
        self.assertEqual(event.event_id, valid_event_id)

        for invalid_event_id in (
            "outbox-event-1",
            "123e4567-e89b-12d3-a456-426614174000",
        ):
            with self.assertRaises(ValueError):
                EventEnvelope(
                    protocol_major=PROTOCOL_MAJOR,
                    protocol_minor=PROTOCOL_MINOR,
                    schema_version=SCHEMA_VERSION,
                    event_id=invalid_event_id,
                    correlation_id="correlation-event",
                    occurred_at=100,
                    kind="ADMITTED",
                    payload={"command_id": "command-01"},
                )

    def test_dp02_pre_dispatch_failure_is_not_started(
        self,
    ) -> None:
        from peerhub.dispatch.contract import (
            AttemptSnapshot,
            RequestSnapshot,
        )

        submission = validate_submission(
            _envelope(),
            authenticated_principal="principal-01",
            completion_contract=_contract(),
            state_changing=True,
        )
        request, _, _, _ = admit_request(
            submission,
            command_id=CommandID("command-dp02"),
            admission_receipt_id="admission-dp02",
            lease_id="lease-dp02",
            policy_revision=7,
            configuration_revision=11,
            selected_peer_instance_id="instance-01",
            selected_profile_id="profile-01",
            route_decision_digest=hashlib.sha256(
                b"route-dp02"
            ).hexdigest(),
            admitted_at=200,
        )
        prepared = replace(
            request,
            state=RequestState.PREPARED,
            revision=2,
            updated_at=201,
        )
        attempt = AttemptSnapshot(
            attempt_id="attempt-dp02",
            command_id=prepared.command_id,
            attempt_number=1,
            lease_id=prepared.lease_id,
            state=RequestState.PREPARED,
            execution_certainty=(
                ExecutionCertainty.NOT_STARTED
            ),
            revision=1,
            created_at=201,
            updated_at=201,
        )

        failed_request, failed_attempt = fail_pre_dispatch(
            prepared,
            attempt,
            error_code=ErrorCode.SPAWN_FAILED,
            updated_at=202,
        )
        self.assertIsInstance(failed_request, RequestSnapshot)
        self.assertEqual(
            failed_request.state,
            RequestState.FAILED_PRE_DISPATCH,
        )
        self.assertEqual(
            failed_attempt.execution_certainty,
            ExecutionCertainty.NOT_STARTED,
        )

    def test_dp03_nonzero_exit_cannot_be_verified(self) -> None:
        result = AskResult(
            execution=ExecutionOutcome(
                started=True,
                exit_code=9,
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
                contract_kind=(
                    CompletionContractKind.FIELD_REQUIRED
                ),
            ),
            policy_revision=7,
        )
        self.assertEqual(
            result.effective_status,
            RequestState.FAILED,
        )

    def test_exit_zero_text_is_only_unverified_without_proof(
        self,
    ) -> None:
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
                vendor_completion_marker=None,
                suspected_truncation=False,
                protocol_failure=None,
            ),
            completion=CompletionAssessment(
                state=CompletionAssessmentState.UNVERIFIED,
                contract_kind=(
                    CompletionContractKind.FIELD_REQUIRED
                ),
            ),
            policy_revision=7,
        )
        self.assertEqual(
            result.effective_status,
            RequestState.DELIVERED_UNVERIFIED,
        )


if __name__ == "__main__":
    unittest.main()
