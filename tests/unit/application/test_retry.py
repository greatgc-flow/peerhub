import pytest

from peerhub.adapters.codex_adapter import RealCodexAdapter
from peerhub.adapters.contract import (
    AdapterRequest,
    ProfileDescriptor,
    SessionAction,
    SessionHint,
)
from peerhub.application.retry import (
    AttemptDispatchPlan,
    AttemptExecutionRecord,
    MultiAttemptExecutionResult,
    RetryAction,
    RetryCondition,
    RetryConditionEvidence,
    RetryDecision,
    RetryDecisionReason,
    RetryLoopStopReason,
    adjudicate_retry,
    map_retry_disposition,
)
from peerhub.application.workflows import ExecutionWorkflowResult
from peerhub.core.execution import ExecutionCertainty
from peerhub.core.protocol import (
    CommandID,
    ErrorCode,
    ErrorPhase,
    OperationalFailureCategory,
    RetryDisposition,
)
from peerhub.dispatch.capability import CapabilityTier
from peerhub.dispatch.contract import (
    AttemptFailureClassification,
    AttemptSnapshot,
    CompletionContract,
    CompletionContractKind,
    RequestSnapshot,
    RequestState,
    TerminalClassification,
)
from peerhub.dispatch.model import _OPERATIONAL_KINDS, _TERMINAL_ROWS


def test_map_retry_disposition_matrix() -> None:
    """Test every row in the explicit specification matrix."""
    # Row 1: START_UNCERTAIN
    assert map_retry_disposition(
        AttemptFailureClassification(ErrorCode.START_UNCERTAIN, ErrorPhase.POST_SPAWN, None),
        terminal_classification=TerminalClassification.START_UNCERTAIN,
    ) is RetryDisposition.UNSAFE

    # Row 2: SILENCE_TIMEOUT
    assert map_retry_disposition(
        AttemptFailureClassification(ErrorCode.SILENCE_TIMEOUT, ErrorPhase.POST_SPAWN, None),
        terminal_classification=TerminalClassification.SILENCE_TIMEOUT,
    ) is RetryDisposition.UNSAFE

    # Row 3: PROCESS_TIMEOUT
    assert map_retry_disposition(
        AttemptFailureClassification(ErrorCode.PROCESS_TIMEOUT, ErrorPhase.POST_SPAWN, None),
        terminal_classification=TerminalClassification.PROCESS_TIMEOUT,
    ) is RetryDisposition.UNSAFE

    # Row 4: EXIT_NON_ZERO, INTERNAL_ERROR, None
    assert map_retry_disposition(
        AttemptFailureClassification(ErrorCode.INTERNAL_ERROR, ErrorPhase.POST_SPAWN, None),
        terminal_classification=TerminalClassification.EXIT_NON_ZERO,
    ) is RetryDisposition.UNSAFE

    # Row 5: EXIT_NON_ZERO, SESSION_INVALID, None
    assert map_retry_disposition(
        AttemptFailureClassification(ErrorCode.SESSION_INVALID, ErrorPhase.POST_SPAWN, None),
        terminal_classification=TerminalClassification.EXIT_NON_ZERO,
    ) is RetryDisposition.CONDITIONAL

    # Row 6: EXIT_NON_ZERO, INVOCATION_PLAN_REJECTED, None
    assert map_retry_disposition(
        AttemptFailureClassification(ErrorCode.INVOCATION_PLAN_REJECTED, ErrorPhase.POST_SPAWN, None),
        terminal_classification=TerminalClassification.EXIT_NON_ZERO,
    ) is RetryDisposition.NEVER

    # Row 7: EXIT_NON_ZERO, INTERNAL_ERROR, AUTH_UNAVAILABLE
    assert map_retry_disposition(
        AttemptFailureClassification(ErrorCode.INTERNAL_ERROR, ErrorPhase.POST_SPAWN, OperationalFailureCategory.AUTH_UNAVAILABLE),
        terminal_classification=TerminalClassification.EXIT_NON_ZERO,
    ) is RetryDisposition.CONDITIONAL

    # Row 8: EXIT_NON_ZERO, INTERNAL_ERROR, NETWORK_UNAVAILABLE
    assert map_retry_disposition(
        AttemptFailureClassification(ErrorCode.INTERNAL_ERROR, ErrorPhase.POST_SPAWN, OperationalFailureCategory.NETWORK_UNAVAILABLE),
        terminal_classification=TerminalClassification.EXIT_NON_ZERO,
    ) is RetryDisposition.CONDITIONAL

    # Row 9: EXIT_NON_ZERO, INTERNAL_ERROR, PROVIDER_UNAVAILABLE
    assert map_retry_disposition(
        AttemptFailureClassification(ErrorCode.INTERNAL_ERROR, ErrorPhase.POST_SPAWN, OperationalFailureCategory.PROVIDER_UNAVAILABLE),
        terminal_classification=TerminalClassification.EXIT_NON_ZERO,
    ) is RetryDisposition.CONDITIONAL

    # Row 10: EXIT_NON_ZERO, INTERNAL_ERROR, QUOTA_EXHAUSTED
    assert map_retry_disposition(
        AttemptFailureClassification(ErrorCode.INTERNAL_ERROR, ErrorPhase.POST_SPAWN, OperationalFailureCategory.QUOTA_EXHAUSTED),
        terminal_classification=TerminalClassification.EXIT_NON_ZERO,
    ) is RetryDisposition.CONDITIONAL

    # Row 11: EXIT_NON_ZERO, INTERNAL_ERROR, RATE_LIMITED
    assert map_retry_disposition(
        AttemptFailureClassification(ErrorCode.INTERNAL_ERROR, ErrorPhase.POST_SPAWN, OperationalFailureCategory.RATE_LIMITED),
        terminal_classification=TerminalClassification.EXIT_NON_ZERO,
    ) is RetryDisposition.CONDITIONAL

    # Row 12: OUTPUT_LIMIT_EXCEEDED, PROCESS_KILLED, None
    assert map_retry_disposition(
        AttemptFailureClassification(ErrorCode.PROCESS_KILLED, ErrorPhase.POST_SPAWN, None),
        terminal_classification=TerminalClassification.OUTPUT_LIMIT_EXCEEDED,
    ) is RetryDisposition.NEVER

    # Row 13: None, (whatever code), None
    for code in ErrorCode:
        assert map_retry_disposition(
            AttemptFailureClassification(code, ErrorPhase.ASSESSMENT, None),
            terminal_classification=None,
        ) is RetryDisposition.UNSAFE


def _generate_reachable_failures(
    terminal_classification: TerminalClassification | None,
) -> list[AttemptFailureClassification]:
    """Simulate what classify_attempt_failure() can actually produce."""
    if terminal_classification is None:
        # Any error code is possible for a protocol failure
        return [AttemptFailureClassification(code, ErrorPhase.ASSESSMENT, None) for code in ErrorCode]

    base_code = _TERMINAL_ROWS[terminal_classification]

    if terminal_classification is TerminalClassification.EXIT_NON_ZERO:
        reachable = [
            AttemptFailureClassification(base_code, ErrorPhase.POST_SPAWN, None),
            AttemptFailureClassification(ErrorCode.SESSION_INVALID, ErrorPhase.POST_SPAWN, None),
            AttemptFailureClassification(ErrorCode.INVOCATION_PLAN_REJECTED, ErrorPhase.POST_SPAWN, None),
        ]
        # And any operational category returned by vendor mapping today
        for category in _OPERATIONAL_KINDS.values():
            reachable.append(AttemptFailureClassification(base_code, ErrorPhase.POST_SPAWN, category))
        return reachable
    else:
        return [AttemptFailureClassification(base_code, ErrorPhase.POST_SPAWN, None)]


def test_map_retry_disposition_is_total() -> None:
    """Test that all combinations classify_attempt_failure() can produce are handled."""
    terminals = list(TerminalClassification) + [None]

    for terminal in terminals:
        failures = _generate_reachable_failures(terminal)
        for failure in failures:
            # Should not raise any exception
            disposition = map_retry_disposition(failure, terminal_classification=terminal)
            assert isinstance(disposition, RetryDisposition)


def test_map_retry_disposition_invalid_inputs_raise() -> None:
    """Test that structurally impossible combinations fail loudly."""
    # (a) null terminal classification with non-null category
    with pytest.raises(ValueError):
        map_retry_disposition(
            AttemptFailureClassification(ErrorCode.INTERNAL_ERROR, ErrorPhase.ASSESSMENT, OperationalFailureCategory.NETWORK_UNAVAILABLE),
            terminal_classification=None,
        )

    # (b) invalid phases
    with pytest.raises(ValueError):
        map_retry_disposition(
            AttemptFailureClassification(ErrorCode.INTERNAL_ERROR, ErrorPhase.POST_SPAWN, None),
            terminal_classification=None,
        )

    with pytest.raises(ValueError):
        map_retry_disposition(
            AttemptFailureClassification(ErrorCode.INTERNAL_ERROR, ErrorPhase.ASSESSMENT, None),
            terminal_classification=TerminalClassification.EXIT_NON_ZERO,
        )


_COMPLETION_CONTRACT = CompletionContract(
    contract_id="contract-1",
    kind=CompletionContractKind.DELIVERY_ONLY,
    requirements=(),
    replay_safe=False,
)


def _execution_result() -> ExecutionWorkflowResult:
    command_id = CommandID("command-1")
    request = RequestSnapshot(
        command_id=command_id,
        client_id="client-1",
        client_request_id="request-1",
        correlation_id="correlation-1",
        authenticated_principal="principal-1",
        command_type="ask",
        idempotency_key="idempotency-1",
        payload_digest="a" * 64,
        scope={},
        params={},
        expected_policy_revision=None,
        expected_configuration_revision=None,
        policy_revision=1,
        configuration_revision=1,
        completion_contract=_COMPLETION_CONTRACT,
        required_capability_tier=CapabilityTier.READ_ONLY,
        selected_peer_instance_id="peer-1",
        selected_profile_id="cx.standard",
        route_decision_digest="b" * 64,
        lease_id="lease-1",
        state=RequestState.SUCCEEDED_VERIFIED,
        revision=1,
        created_at=1,
        updated_at=2,
    )
    attempt = AttemptSnapshot(
        attempt_id="attempt-1",
        command_id=command_id,
        attempt_number=1,
        lease_id="lease-1",
        state=RequestState.SUCCEEDED_VERIFIED,
        execution_certainty=ExecutionCertainty.TERMINAL,
        revision=1,
        created_at=1,
        updated_at=2,
    )
    return ExecutionWorkflowResult(request=request, attempt=attempt)


def _stop_decision() -> RetryDecision:
    return RetryDecision(
        disposition=None,
        action=RetryAction.STOP,
        reason=RetryDecisionReason.VERIFIED_SUCCESS,
        required_conditions=(),
        not_before=None,
    )


def _attempt_record() -> AttemptExecutionRecord:
    return AttemptExecutionRecord(
        execution=_execution_result(),
        error_detail=None,
        retry_decision=_stop_decision(),
        retry_authorization=None,
    )


def _attempt_plan(
    *,
    route_decision_id: str = "route-1",
    capability_lease_id: str = "capability-1",
    peer_instance_id: str = "peer-1",
) -> AttemptDispatchPlan:
    return AttemptDispatchPlan(
        route_decision_id=route_decision_id,
        capability_lease_id=capability_lease_id,
        peer_instance_id=peer_instance_id,
        adapter_request=AdapterRequest(
            request_id="request-1",
            prompt_content="hello",
            prompt_reference=None,
            workspace_scope="workspace-1",
            profile_id="cx.standard",
            requested_session_action=SessionAction.RESUME,
            completion_contract=_COMPLETION_CONTRACT,
        ),
        peer_adapter=RealCodexAdapter(),
        profile=ProfileDescriptor(
            profile_id="cx.standard",
            profile_class="tier",
            supports_reasoning_effort=False,
        ),
        session=SessionHint(
            external_session_id="session-1",
            adapter_fingerprint=None,
            session_generation=1,
        ),
    )


def test_retry_enum_vocabulary_covers_ratified_cases() -> None:
    assert {action.value for action in RetryAction} == {
        "STOP",
        "RETRY_SAME_TARGET",
        "FAILOVER",
        "DEFER",
    }
    assert {
        "VERIFIED_SUCCESS",
        "DELIVERED_UNVERIFIED",
        "UNSAFE_NO_EVIDENCE",
        "NEVER_DISPOSITION",
        "CONDITION_UNMET",
        "ATTEMPT_LIMIT_REACHED",
        "AUTHORITATIVE_CANCELLATION",
        "CONCURRENT_TERMINAL_STATE",
        "CONCURRENT_ATTEMPT_IN_PROGRESS",
        "FAILOVER_UNAVAILABLE",
        "LEGACY_CLASSIFICATION_UNKNOWN",
        "EXECUTION_NOT_STARTED",
        "RECONCILIATION_COMPLETE",
        "REPLAY_SAFE_COMPLETION_CONTRACT",
    } <= {reason.value for reason in RetryDecisionReason}
    assert {condition.value for condition in RetryCondition} == {
        "SESSION_REPLACED_OR_REMOVED",
        "AUTH_RESTORED",
        "NETWORK_RECOVERED",
        "PROVIDER_RECOVERED",
        "QUOTA_AVAILABLE",
        "RATE_LIMIT_BOUNDARY_ELAPSED",
    }
    assert {
        "VERIFIED_SUCCESS",
        "DELIVERED_UNVERIFIED",
        "AUTHORITATIVE_CANCELLATION",
        "NEVER_DISPOSITION",
        "UNSAFE_NO_EVIDENCE",
        "CONDITION_DEFERRED",
        "ATTEMPT_LIMIT_REACHED",
        "ROUTE_EXHAUSTED",
        "CONCURRENT_TERMINAL_STATE",
        "CONCURRENT_ATTEMPT_IN_PROGRESS",
        "FAILOVER_UNAVAILABLE",
        "LEGACY_CLASSIFICATION_UNKNOWN",
    } == {reason.value for reason in RetryLoopStopReason}


def test_retry_condition_evidence_constructs_and_validates() -> None:
    evidence = RetryConditionEvidence(
        condition=RetryCondition.RATE_LIMIT_BOUNDARY_ELAPSED,
        satisfied=False,
        evidence_source="provider_retry_after",
        observed_at=100,
        not_before=200,
    )
    assert evidence.not_before == 200

    with pytest.raises(ValueError, match="evidence_source"):
        RetryConditionEvidence(
            condition=RetryCondition.AUTH_RESTORED,
            satisfied=True,
            evidence_source=" ",
            observed_at=100,
            not_before=None,
        )
    with pytest.raises(ValueError, match="observed_at"):
        RetryConditionEvidence(
            condition=RetryCondition.AUTH_RESTORED,
            satisfied=True,
            evidence_source="auth_probe",
            observed_at=-1,
            not_before=None,
        )


def test_retry_decision_constructs_and_validates() -> None:
    decision = RetryDecision(
        disposition=RetryDisposition.CONDITIONAL,
        action=RetryAction.DEFER,
        reason=RetryDecisionReason.CONDITION_UNMET,
        required_conditions=(RetryCondition.QUOTA_AVAILABLE,),
        not_before=200,
    )
    assert decision.required_conditions == (RetryCondition.QUOTA_AVAILABLE,)

    with pytest.raises(ValueError, match="required_conditions"):
        RetryDecision(
            disposition=RetryDisposition.UNSAFE,
            action=RetryAction.STOP,
            reason=RetryDecisionReason.UNSAFE_NO_EVIDENCE,
            required_conditions=(RetryCondition.AUTH_RESTORED,),
            not_before=None,
        )
    with pytest.raises(ValueError, match="at least one"):
        RetryDecision(
            disposition=RetryDisposition.CONDITIONAL,
            action=RetryAction.DEFER,
            reason=RetryDecisionReason.CONDITION_UNMET,
            required_conditions=(),
            not_before=None,
        )
    with pytest.raises(ValueError, match="DEFER"):
        RetryDecision(
            disposition=RetryDisposition.CONDITIONAL,
            action=RetryAction.STOP,
            reason=RetryDecisionReason.CONDITION_UNMET,
            required_conditions=(RetryCondition.PROVIDER_RECOVERED,),
            not_before=200,
        )


def test_attempt_execution_record_constructs() -> None:
    record = _attempt_record()
    assert record.execution.attempt.attempt_id == "attempt-1"
    assert record.retry_authorization is None


def test_multi_attempt_execution_result_constructs_and_validates() -> None:
    record = _attempt_record()
    result = MultiAttemptExecutionResult(
        command_id=CommandID("command-1"),
        attempts=(record,),
        stop_reason=RetryLoopStopReason.VERIFIED_SUCCESS,
    )
    assert result.attempts[-1] is record

    with pytest.raises(ValueError, match="attempts"):
        MultiAttemptExecutionResult(
            command_id=CommandID("command-1"),
            attempts=(),
            stop_reason=RetryLoopStopReason.ATTEMPT_LIMIT_REACHED,
        )
    with pytest.raises(ValueError, match="command_id"):
        MultiAttemptExecutionResult(
            command_id=CommandID(" "),
            attempts=(record,),
            stop_reason=RetryLoopStopReason.VERIFIED_SUCCESS,
        )


def test_attempt_dispatch_plan_constructs_and_validates() -> None:
    plan = _attempt_plan()
    assert plan.peer_adapter.descriptor.adapter_id == "codex-peer"
    assert plan.session is not None
    assert plan.session.external_session_id == "session-1"

    for field_name in (
        "route_decision_id",
        "capability_lease_id",
        "peer_instance_id",
    ):
        values = {field_name: " "}
        with pytest.raises(ValueError, match=field_name):
            _attempt_plan(**values)


def test_adjudicate_retry_real_snapshots_missing_terminal_code():
    # L4: verify direct attribute access works on real instances when terminal_error_code is absent/None
    exec_res = _execution_result()
    # It has SUCCEEDED_VERIFIED initially, change state to FAILED_PRE_DISPATCH
    # Both snapshots have terminal_error_code=None by default unless initialized otherwise
    object.__setattr__(exec_res.request, "state", RequestState.FAILED_PRE_DISPATCH)
    object.__setattr__(exec_res.request, "terminal_error_code", None)

    object.__setattr__(exec_res.attempt, "state", RequestState.FAILED_PRE_DISPATCH)
    object.__setattr__(exec_res.attempt, "execution_certainty", ExecutionCertainty.NOT_STARTED)
    object.__setattr__(exec_res.attempt, "terminal_error_code", None)
    object.__setattr__(exec_res.attempt, "result", None)

    decision = adjudicate_retry(exec_res, durable_attempt_number=1, max_attempts=3, reconciliation_complete=False)

    # Should fall through to UNSAFE -> RETRY_SAME_TARGET due to NOT_STARTED
    assert decision.action == RetryAction.RETRY_SAME_TARGET
    assert decision.reason == RetryDecisionReason.EXECUTION_NOT_STARTED


def test_adjudicate_retry_conditional_unsatisfied():
    exec_res = _execution_result()
    object.__setattr__(exec_res.request, "state", RequestState.FAILED_PRE_DISPATCH)
    object.__setattr__(exec_res.attempt, "state", RequestState.FAILED_PRE_DISPATCH)
    object.__setattr__(exec_res.attempt, "execution_certainty", ExecutionCertainty.NOT_STARTED)

    dummy_result = type("DummyAskResult", (), {
        "failure_classification": AttemptFailureClassification(ErrorCode.SESSION_INVALID, ErrorPhase.POST_SPAWN, None),
        "terminal_classification": TerminalClassification.EXIT_NON_ZERO
    })()
    object.__setattr__(exec_res.attempt, "result", dummy_result)

    evidence = RetryConditionEvidence(
        condition=RetryCondition.SESSION_REPLACED_OR_REMOVED,
        satisfied=False,
        evidence_source="test",
        observed_at=100,
        not_before=None,
    )

    decision = adjudicate_retry(
        exec_res,
        durable_attempt_number=1,
        max_attempts=3,
        reconciliation_complete=False,
        condition_evidence=evidence,
    )

    assert decision.action == RetryAction.DEFER
    assert decision.reason == RetryDecisionReason.CONDITION_UNMET
    assert decision.required_conditions == (RetryCondition.SESSION_REPLACED_OR_REMOVED,)


def test_adjudicate_retry_conditional_satisfied():
    exec_res = _execution_result()
    object.__setattr__(exec_res.request, "state", RequestState.FAILED_PRE_DISPATCH)
    object.__setattr__(exec_res.attempt, "state", RequestState.FAILED_PRE_DISPATCH)
    object.__setattr__(exec_res.attempt, "execution_certainty", ExecutionCertainty.NOT_STARTED)

    dummy_result = type("DummyAskResult", (), {
        "failure_classification": AttemptFailureClassification(ErrorCode.SESSION_INVALID, ErrorPhase.POST_SPAWN, None),
        "terminal_classification": TerminalClassification.EXIT_NON_ZERO
    })()
    object.__setattr__(exec_res.attempt, "result", dummy_result)

    evidence = RetryConditionEvidence(
        condition=RetryCondition.SESSION_REPLACED_OR_REMOVED,
        satisfied=True,
        evidence_source="test",
        observed_at=100,
        not_before=None,
    )

    decision = adjudicate_retry(
        exec_res,
        durable_attempt_number=1,
        max_attempts=3,
        reconciliation_complete=False,
        condition_evidence=evidence,
    )

    assert decision.action == RetryAction.RETRY_SAME_TARGET
    assert decision.reason == RetryDecisionReason.CONDITION_MET
    assert decision.required_conditions == (RetryCondition.SESSION_REPLACED_OR_REMOVED,)


def test_adjudicate_retry_conditional_needs_safety_gate():
    exec_res = _execution_result()
    object.__setattr__(exec_res.request, "state", RequestState.FAILED_PRE_DISPATCH)
    object.__setattr__(exec_res.attempt, "state", RequestState.FAILED_PRE_DISPATCH)
    object.__setattr__(exec_res.attempt, "execution_certainty", ExecutionCertainty.MAY_HAVE_STARTED)

    # ensure completion contract is not replay safe
    # _COMPLETION_CONTRACT is already replay_safe=False as defined in the test file

    dummy_result = type("DummyAskResult", (), {
        "failure_classification": AttemptFailureClassification(ErrorCode.SESSION_INVALID, ErrorPhase.POST_SPAWN, None),
        "terminal_classification": TerminalClassification.EXIT_NON_ZERO
    })()
    object.__setattr__(exec_res.attempt, "result", dummy_result)

    evidence = RetryConditionEvidence(
        condition=RetryCondition.SESSION_REPLACED_OR_REMOVED,
        satisfied=True,
        evidence_source="test",
        observed_at=100,
        not_before=None,
    )

    decision = adjudicate_retry(
        exec_res,
        durable_attempt_number=1,
        max_attempts=3,
        reconciliation_complete=False,
        condition_evidence=evidence,
    )

    assert decision.action == RetryAction.STOP
    assert decision.reason == RetryDecisionReason.UNSAFE_NO_EVIDENCE
    assert decision.required_conditions == (RetryCondition.SESSION_REPLACED_OR_REMOVED,)
