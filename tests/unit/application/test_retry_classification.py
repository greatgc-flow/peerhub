from peerhub.application.retry import (
    ConcurrentClaimOutcome,
    ConcurrentClaimResolution,
    MAX_CONCURRENT_READJUDICATE_SPIN_LIMIT,
    classify_concurrent_claim,
)
from peerhub.core.execution import ExecutionCertainty
from peerhub.dispatch.capability import CapabilityLease
from peerhub.dispatch.contract import (
    AttemptSnapshot,
    RequestSnapshot,
    RequestState,
    RetryLoopState,
)
from peerhub.routing.contract import RouteDecision


def test_classify_terminal_state():
    state = _mock_state(RequestState.SUCCEEDED_VERIFIED, attempts=[], authorized_attempt=1)
    res = classify_concurrent_claim(state, target_attempt_number=2)
    assert res.outcome is ConcurrentClaimOutcome.TERMINAL_STATE


def test_classify_concurrent_attempt_in_progress_from_capability():
    # b. current capability authorizes N+1 while durable history remains only 1..N
    state = _mock_state(RequestState.RUNNING, attempts=[_mock_attempt(1, False)], authorized_attempt=2)
    res = classify_concurrent_claim(state, target_attempt_number=2)
    assert res.outcome is ConcurrentClaimOutcome.ATTEMPT_IN_PROGRESS


def test_classify_concurrent_attempt_in_progress_from_history():
    # c. highest durable attempt is N+1 and it is active
    state = _mock_state(RequestState.RUNNING, attempts=[_mock_attempt(1, True), _mock_attempt(2, False)], authorized_attempt=2)
    res = classify_concurrent_claim(state, target_attempt_number=2)
    assert res.outcome is ConcurrentClaimOutcome.ATTEMPT_IN_PROGRESS


def test_classify_concurrent_attempt_terminal_rebuild():
    # d. highest durable attempt is N+1 and it is terminal
    state = _mock_state(RequestState.RUNNING, attempts=[_mock_attempt(1, True), _mock_attempt(2, True)], authorized_attempt=2)
    res = classify_concurrent_claim(state, target_attempt_number=2)
    assert res.outcome is ConcurrentClaimOutcome.ATTEMPT_TERMINAL_REBUILD
    assert res.rebuild_attempt_number == 2


def test_classify_no_advancement_readjudicate():
    # e. no authoritative advancement at all
    state = _mock_state(RequestState.RUNNING, attempts=[_mock_attempt(1, True)], authorized_attempt=1)
    res = classify_concurrent_claim(state, target_attempt_number=2)
    assert res.outcome is ConcurrentClaimOutcome.NO_ADVANCEMENT_READJUDICATE
    assert res.readjudicate_retry_limit == MAX_CONCURRENT_READJUDICATE_SPIN_LIMIT


def _mock_attempt(number: int, is_terminal: bool) -> AttemptSnapshot:
    state = RequestState.SUCCEEDED_VERIFIED if is_terminal else RequestState.RUNNING
    return AttemptSnapshot(
        attempt_id=f"attempt-{number}",
        command_id="cmd",
        attempt_number=number,
        lease_id="lease-1",
        state=state,
        execution_certainty=ExecutionCertainty.TERMINAL if is_terminal else ExecutionCertainty.NOT_STARTED,
        revision=1,
        created_at=10,
        updated_at=10,
    )


def _mock_state(req_state: RequestState, attempts: list[AttemptSnapshot], authorized_attempt: int) -> RetryLoopState:
    from peerhub.dispatch.capability import CapabilityTier, EnforcementLevel
    req = RequestSnapshot(
        command_id="cmd",
        client_id="client-1",
        client_request_id="request-1",
        correlation_id="correlation-1",
        authenticated_principal="principal-1",
        command_type="ask",
        idempotency_key="idempotency-1",
        payload_digest="d" * 64,
        scope={},
        params={},
        expected_policy_revision=None,
        expected_configuration_revision=None,
        policy_revision=7,
        configuration_revision=2,
        completion_contract={"type": "standard"},
        required_capability_tier=CapabilityTier.READ_ONLY,
        selected_peer_instance_id="instance-1",
        selected_profile_id="profile-1",
        route_decision_digest="e" * 64,
        lease_id="lease-1",
        state=req_state,
        revision=1,
        created_at=10,
        updated_at=10,
    )
    cap = CapabilityLease(
        capability_lease_id="capability-1",
        command_id="cmd",
        admission_receipt_id="receipt-1",
        session_lease_id="lease-1",
        subject_principal_id="principal-1",
        selected_peer_kind="standard",
        required_tier=CapabilityTier.READ_ONLY,
        authorized_tier=CapabilityTier.READ_ONLY,
        minimum_enforcement=EnforcementLevel.ADVISORY,
        selected_peer_instance_id="instance-1",
        selected_profile_id="profile-1",
        route_decision_digest="e" * 64,
        policy_revision=7,
        issuer_id="peerhub-core",
        issued_at=20,
        expires_at=None,
        authorized_attempt_number=authorized_attempt,
        previous_attempt_id="attempt-1" if authorized_attempt > 1 else None,
    )
    return RetryLoopState(
        request=req,
        max_attempts=5,
        attempts=tuple(attempts),
        current_lease=None, # type: ignore
        current_capability=cap,
        route_decision=None, # type: ignore
    )
