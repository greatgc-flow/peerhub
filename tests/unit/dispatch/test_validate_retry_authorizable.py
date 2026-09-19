"""Unit coverage for validate_retry_authorizable (quality-audit-2026-09-19.md
item 2: the primary gatekeeper for whether a failed attempt can be
safely retried had zero unit tests)."""

from __future__ import annotations

from dataclasses import replace

import pytest

from peerhub.core.errors import InvalidMutationError
from peerhub.core.execution import ExecutionCertainty
from peerhub.core.protocol import CommandID
from peerhub.dispatch.contract import (
    AttemptSnapshot,
    CompletionContract,
    CompletionContractKind,
    RequestSnapshot,
    RequestState,
)
from peerhub.dispatch.capability import CapabilityTier
from peerhub.dispatch.model import (
    InvalidStateTransitionError,
    validate_retry_authorizable,
)

_COMMAND_ID = CommandID("command-01")


def _request(**overrides: object) -> RequestSnapshot:
    completion_contract = CompletionContract(
        contract_id="contract-01",
        kind=CompletionContractKind.DELIVERY_ONLY,
        requirements=(),
        replay_safe=False,
    )
    base = RequestSnapshot(
        command_id=_COMMAND_ID,
        client_id="client-01",
        client_request_id="client-request-01",
        correlation_id="correlation-01",
        authenticated_principal="principal-01",
        command_type="peer.ask",
        idempotency_key="idempotency-01",
        payload_digest="0" * 64,
        scope={},
        params={},
        expected_policy_revision=7,
        expected_configuration_revision=1,
        policy_revision=7,
        configuration_revision=1,
        completion_contract=completion_contract,
        required_capability_tier=CapabilityTier.WORKTREE_WRITE,
        selected_peer_instance_id="cx-instance-01",
        selected_profile_id="cx-profile-01",
        route_decision_digest="1" * 64,
        lease_id="session-lease-01",
        state=RequestState.FAILED,
        revision=1,
        created_at=10,
        updated_at=10,
    )
    return replace(base, **overrides)  # type: ignore[arg-type]


def _attempt(**overrides: object) -> AttemptSnapshot:
    base = AttemptSnapshot(
        attempt_id="prev-attempt-01",
        command_id=_COMMAND_ID,
        attempt_number=1,
        lease_id="session-lease-01",
        state=RequestState.FAILED,
        execution_certainty=ExecutionCertainty.NOT_STARTED,
        revision=1,
        created_at=10,
        updated_at=10,
        reconciliation_complete=False,
        result=None,
        terminal_error_code=None,
    )
    return replace(base, **overrides)  # type: ignore[arg-type]


def test_accepts_retry_when_previous_attempt_never_started() -> None:
    validate_retry_authorizable(
        _request(), _attempt(), reconciliation_complete=False
    )


def test_rejects_command_id_mismatch() -> None:
    mismatched_attempt = _attempt(command_id=CommandID("different-command"))
    with pytest.raises(InvalidMutationError):
        validate_retry_authorizable(
            _request(), mismatched_attempt, reconciliation_complete=False
        )


def test_rejects_lease_id_mismatch() -> None:
    mismatched_attempt = _attempt(lease_id="different-lease")
    with pytest.raises(InvalidMutationError, match="does not use the request lease"):
        validate_retry_authorizable(
            _request(), mismatched_attempt, reconciliation_complete=False
        )


def test_rejects_non_retryable_request_state() -> None:
    with pytest.raises(InvalidStateTransitionError):
        validate_retry_authorizable(
            _request(state=RequestState.ADMITTED),
            _attempt(),
            reconciliation_complete=False,
        )


def test_rejects_non_retryable_attempt_state() -> None:
    with pytest.raises(InvalidStateTransitionError):
        validate_retry_authorizable(
            _request(),
            _attempt(state=RequestState.ADMITTED),
            reconciliation_complete=False,
        )


def test_rejects_retry_with_no_replay_safety_evidence() -> None:
    unsafe_attempt = _attempt(execution_certainty=ExecutionCertainty.MAY_HAVE_STARTED)
    with pytest.raises(InvalidMutationError, match="requires NOT_STARTED evidence"):
        validate_retry_authorizable(
            _request(), unsafe_attempt, reconciliation_complete=False
        )


def test_accepts_retry_when_reconciliation_complete() -> None:
    uncertain_attempt = _attempt(execution_certainty=ExecutionCertainty.MAY_HAVE_STARTED)
    validate_retry_authorizable(
        _request(), uncertain_attempt, reconciliation_complete=True
    )


def test_accepts_retry_when_completion_contract_is_replay_safe() -> None:
    uncertain_attempt = _attempt(execution_certainty=ExecutionCertainty.MAY_HAVE_STARTED)
    replay_safe_request = _request(
        completion_contract=replace(
            _request().completion_contract, replay_safe=True
        )
    )
    validate_retry_authorizable(
        replay_safe_request, uncertain_attempt, reconciliation_complete=False
    )
