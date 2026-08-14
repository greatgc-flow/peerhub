"""Pure proofs for the bounded 5C retry-orchestration policy."""

from __future__ import annotations

from dataclasses import replace

import pytest

from peerhub.adapters.claude_adapter import RealClaudeAdapter
from peerhub.adapters.codex_adapter import RealCodexAdapter
from peerhub.adapters.contract import (
    AdapterRequest,
    ProfileDescriptor,
    SessionAction,
    SessionHint,
)
from peerhub.application.retry import (
    AuthorizationErrorSignal,
    AttemptDispatchPlan,
    ResolvedRetryTarget,
    RetryAction,
    RetryCondition,
    RetryConditionEvidence,
    RetryConditionEvidenceProvider,
    RetryDecision,
    RetryDecisionReason,
    RetryLoopStopReason,
    build_retry_dispatch_plan,
    classify_authorization_error,
    evaluate_retry_condition_evidence,
    read_fresh_retry_condition_evidence,
    transition_retry_route,
)
from peerhub.core.errors import (
    AttemptLimitReachedError,
    CapabilityAuthorizationDeniedError,
    InvalidMutationError,
    InvalidStateTransitionError,
    PolicyStaleError,
    RecordNotFoundError,
    RetryPolicyConflictError,
    RetryRouteUnavailableError,
    RouteExhaustedError,
    StaleRevisionError,
)
from peerhub.core.evidence import EvidenceRef
from peerhub.core.execution import ExecutionCertainty
from peerhub.core.protocol import CommandID, ErrorCode, RetryDisposition
from peerhub.dispatch.capability import (
    CapabilityLease,
    CapabilityLeaseViolation,
    CapabilityTier,
    EnforcementLevel,
)
from peerhub.dispatch.contract import (
    AttemptSnapshot,
    CompletionContract,
    CompletionContractKind,
    LeaseFenceTuple,
    LeaseSnapshot,
    LeaseState,
    RequestSnapshot,
    RequestState,
)
from peerhub.dispatch.retry_authorization import RetryAuthorizationBundle
from peerhub.routing.contract import (
    ConfigurationSnapshot,
    RouteCandidateDecision,
    RouteDecision,
    RouteEligibility,
    canonical_route_decision_digest,
)


_COMPLETION_CONTRACT = CompletionContract(
    contract_id="contract-1",
    kind=CompletionContractKind.DELIVERY_ONLY,
    requirements=(),
    replay_safe=False,
)


def _profile(profile_id: str = "cx.standard") -> ProfileDescriptor:
    return ProfileDescriptor(
        profile_id=profile_id,
        profile_class="tier",
        supports_reasoning_effort=False,
    )


def _current_plan() -> AttemptDispatchPlan:
    return AttemptDispatchPlan(
        route_decision_id="route-old",
        capability_lease_id="capability-old",
        peer_instance_id="cx-old",
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
        profile=_profile(),
        session=SessionHint(
            external_session_id="external-session-1",
            adapter_fingerprint="codex-1",
            session_generation=1,
        ),
    )


def _bundle(
    *,
    peer_kind: str = "cx",
    instance_id: str = "cx-old",
    profile_id: str = "cx.standard",
) -> RetryAuthorizationBundle:
    command_id = CommandID("command-1")
    decision = RouteDecision(
        decision_id="route-new",
        client_request_id="request-1",
        configuration=ConfigurationSnapshot(
            revision=2,
            digest="a" * 64,
        ),
        admission_snapshot_id="snapshot-1",
        admission_snapshot_revision=1,
        admission_snapshot_digest="b" * 64,
        routing_policy_id="routing-policy-1",
        routing_policy_revision=1,
        required_capability_tier=CapabilityTier.READ_ONLY,
        candidates=(
            RouteCandidateDecision(
                candidate_id=profile_id,
                instance_id=instance_id,
                representative_profile_id=profile_id,
                eligibility=RouteEligibility.ELIGIBLE,
                effective_weight=1,
                exclusion_reason=None,
                evidence_refs=(EvidenceRef("sha256:route"),),
            ),
        ),
        audit_seed="c" * 64,
        selection_index=0,
        selected_candidate_id=profile_id,
        created_at=20,
    )
    route_digest = canonical_route_decision_digest(decision)
    request = RequestSnapshot(
        command_id=command_id,
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
        completion_contract=_COMPLETION_CONTRACT,
        required_capability_tier=CapabilityTier.READ_ONLY,
        selected_peer_instance_id=instance_id,
        selected_profile_id=profile_id,
        route_decision_digest=route_digest,
        lease_id="lease-new",
        state=RequestState.PREPARED,
        revision=3,
        created_at=10,
        updated_at=20,
    )
    previous_attempt = AttemptSnapshot(
        attempt_id="attempt-1",
        command_id=command_id,
        attempt_number=1,
        lease_id="lease-old",
        state=RequestState.FAILED_PRE_DISPATCH,
        execution_certainty=ExecutionCertainty.NOT_STARTED,
        revision=2,
        created_at=10,
        updated_at=15,
        terminal_error_code=ErrorCode.SPAWN_FAILED,
    )
    session_lease = LeaseSnapshot(
        lease_id="lease-new",
        session_id="session-1",
        fence=LeaseFenceTuple(
            session_id="session-1",
            lease_id="lease-new",
            fencing_token=2,
            revision=1,
            owner_principal_id="principal-1",
            owner_instance_id="cli-instance",
            owner_process_birth_identity=None,
            command_id=command_id,
            authority_epoch=3,
            owner_peer_id="peer-1",
        ),
        state=LeaseState.RESERVED,
        heartbeat_expires_at=1_000,
        created_at=20,
        updated_at=20,
    )
    capability = CapabilityLease(
        capability_lease_id="capability-new",
        command_id=command_id,
        admission_receipt_id="receipt-1",
        session_lease_id="lease-new",
        subject_principal_id="principal-1",
        selected_peer_kind=peer_kind,
        required_tier=CapabilityTier.READ_ONLY,
        authorized_tier=CapabilityTier.READ_ONLY,
        minimum_enforcement=EnforcementLevel.ADVISORY,
        selected_peer_instance_id=instance_id,
        selected_profile_id=profile_id,
        route_decision_digest=route_digest,
        policy_revision=7,
        issuer_id="peerhub-core",
        issued_at=20,
        expires_at=None,
        authorized_attempt_number=2,
        previous_attempt_id="attempt-1",
    )
    return RetryAuthorizationBundle(
        request=request,
        previous_attempt=previous_attempt,
        session_lease=session_lease,
        capability_lease=capability,
        route_decision=decision,
    )


def _retry_decision() -> RetryDecision:
    return RetryDecision(
        disposition=RetryDisposition.CONDITIONAL,
        action=RetryAction.RETRY_SAME_TARGET,
        reason=RetryDecisionReason.CONDITION_MET,
        required_conditions=(RetryCondition.NETWORK_RECOVERED,),
        not_before=None,
    )


def test_route_transition_is_bounded_and_preserves_safety_decision() -> None:
    decision = _retry_decision()

    initial = transition_retry_route(decision)
    assert initial.next_action is RetryAction.RETRY_SAME_TARGET
    assert initial.decision is decision
    assert not initial.reload_required

    unavailable = RetryRouteUnavailableError(
        "command-1",
        ErrorCode.PEER_UNAVAILABLE,
        "current target unavailable",
    )
    failover = transition_retry_route(
        decision,
        attempted_action=RetryAction.RETRY_SAME_TARGET,
        error=unavailable,
    )
    assert failover.next_action is RetryAction.FAILOVER
    assert failover.decision.action is RetryAction.FAILOVER
    assert failover.decision.reason is decision.reason
    assert failover.decision.required_conditions == decision.required_conditions

    with pytest.raises(InvalidMutationError, match="exactly one failover"):
        transition_retry_route(
            failover.decision,
            attempted_action=RetryAction.FAILOVER,
            error=unavailable,
        )


def test_route_transition_propagates_route_exhaustion_unchanged() -> None:
    error = RouteExhaustedError("command-1")

    with pytest.raises(RouteExhaustedError) as exc_info:
        transition_retry_route(
            _retry_decision(),
            attempted_action=RetryAction.FAILOVER,
            error=error,
        )
    assert exc_info.value is error


def test_route_transition_must_begin_with_same_target() -> None:
    decision = replace(_retry_decision(), action=RetryAction.FAILOVER)

    with pytest.raises(InvalidMutationError, match="begin with RETRY_SAME_TARGET"):
        transition_retry_route(decision)


@pytest.mark.parametrize(
    "attempted_action",
    (RetryAction.RETRY_SAME_TARGET, RetryAction.FAILOVER),
)
def test_stale_route_boundary_always_reloads_durable_history(
    attempted_action: RetryAction,
) -> None:
    decision = _retry_decision()
    if attempted_action is RetryAction.FAILOVER:
        decision = replace(decision, action=RetryAction.FAILOVER)
    transition = transition_retry_route(
        decision,
        attempted_action=attempted_action,
        error=StaleRevisionError("command-1", 1, 2),
    )

    assert transition.next_action is None
    assert transition.reload_required
    assert transition.decision is decision


def test_same_target_plan_retains_invocation_objects_and_uses_bundle_ids() -> None:
    current = _current_plan()
    bundle = _bundle()

    rebuilt = build_retry_dispatch_plan(
        bundle=bundle,
        route_action=RetryAction.RETRY_SAME_TARGET,
        current_plan=current,
        resolver=lambda peer_kind, instance_id, profile_id: None,
    )

    assert isinstance(rebuilt, AttemptDispatchPlan)
    assert rebuilt.route_decision_id == bundle.route_decision.decision_id
    assert rebuilt.capability_lease_id == bundle.capability_lease.capability_lease_id
    assert rebuilt.adapter_request is current.adapter_request
    assert rebuilt.peer_adapter is current.peer_adapter
    assert rebuilt.profile is current.profile
    assert rebuilt.session is current.session


def test_dispatch_plan_requires_adapter_and_profile_ids_to_agree() -> None:
    with pytest.raises(ValueError, match="profile_id must match"):
        replace(_current_plan(), profile=_profile("cx.other"))


class _RecordingResolver:
    def __init__(self, resolved: ResolvedRetryTarget | None) -> None:
        self.resolved = resolved
        self.calls: list[tuple[str, str, str]] = []

    def __call__(
        self,
        peer_kind: str,
        instance_id: str,
        profile_id: str,
    ) -> ResolvedRetryTarget | None:
        self.calls.append((peer_kind, instance_id, profile_id))
        return self.resolved


def test_failover_plan_resolves_peer_kind_and_clears_session_atomically() -> None:
    current = _current_plan()
    bundle = _bundle(
        peer_kind="cc",
        instance_id="cc-new",
        profile_id="cc.standard",
    )
    replacement_profile = _profile("cc.standard")
    resolver = _RecordingResolver(
        ResolvedRetryTarget(
            peer_adapter=RealClaudeAdapter(),
            profile=replacement_profile,
        )
    )

    rebuilt = build_retry_dispatch_plan(
        bundle=bundle,
        route_action=RetryAction.FAILOVER,
        current_plan=current,
        resolver=resolver,
    )

    assert isinstance(rebuilt, AttemptDispatchPlan)
    assert resolver.calls == [("cc", "cc-new", "cc.standard")]
    assert rebuilt.peer_instance_id == "cc-new"
    assert rebuilt.peer_adapter is resolver.resolved.peer_adapter
    assert rebuilt.profile is replacement_profile
    assert rebuilt.adapter_request.request_id == current.adapter_request.request_id
    assert rebuilt.adapter_request.prompt_content == current.adapter_request.prompt_content
    assert rebuilt.adapter_request.profile_id == "cc.standard"
    assert rebuilt.adapter_request.requested_session_action is SessionAction.NONE
    assert rebuilt.session is None


def test_unresolved_failover_target_returns_without_a_dispatch_plan() -> None:
    current = _current_plan()
    bundle = _bundle(
        peer_kind="cc",
        instance_id="cc-new",
        profile_id="cc.standard",
    )
    unresolved = _RecordingResolver(None)

    assert build_retry_dispatch_plan(
        bundle=bundle,
        route_action=RetryAction.FAILOVER,
        current_plan=current,
        resolver=unresolved,
    ) is RetryLoopStopReason.FAILOVER_UNAVAILABLE

    assert unresolved.calls == [("cc", "cc-new", "cc.standard")]


@pytest.mark.parametrize(
    ("component", "field", "value"),
    (
        ("request", "selected_peer_instance_id", "attacker-instance"),
        ("request", "selected_profile_id", "attacker.profile"),
        ("request", "route_decision_digest", "e" * 64),
        ("capability_lease", "selected_peer_instance_id", "attacker-instance"),
        ("capability_lease", "selected_profile_id", "attacker.profile"),
        ("capability_lease", "route_decision_digest", "e" * 64),
    ),
)
def test_tampered_bundle_target_binding_is_rejected_before_resolution(
    component: str,
    field: str,
    value: str,
) -> None:
    current = _current_plan()
    bundle = _bundle(
        peer_kind="cc",
        instance_id="cc-new",
        profile_id="cc.standard",
    )
    replacement = replace(
        getattr(bundle, component),
        **{field: value},
    )
    tampered = replace(bundle, **{component: replacement})
    resolver = _RecordingResolver(None)

    with pytest.raises(CapabilityLeaseViolation):
        build_retry_dispatch_plan(
            bundle=tampered,
            route_action=RetryAction.FAILOVER,
            current_plan=current,
            resolver=resolver,
        )
    assert resolver.calls == []


def test_failover_must_replace_instance_and_resolver_must_match_bundle() -> None:
    current = _current_plan()
    same_instance = _bundle()
    with pytest.raises(CapabilityLeaseViolation):
        build_retry_dispatch_plan(
            bundle=same_instance,
            route_action=RetryAction.FAILOVER,
            current_plan=current,
            resolver=_RecordingResolver(None),
        )

    failover = _bundle(
        peer_kind="cc",
        instance_id="cc-new",
        profile_id="cc.standard",
    )
    wrong_target = _RecordingResolver(
        ResolvedRetryTarget(
            peer_adapter=RealCodexAdapter(),
            profile=_profile(),
        )
    )
    with pytest.raises(CapabilityLeaseViolation):
        build_retry_dispatch_plan(
            bundle=failover,
            route_action=RetryAction.FAILOVER,
            current_plan=current,
            resolver=wrong_target,
        )


class _ChangingEvidenceProvider:
    def __init__(self) -> None:
        self.calls: list[tuple[str, RetryCondition]] = []

    def __call__(
        self,
        *,
        latest_attempt: AttemptSnapshot,
        condition: RetryCondition,
    ) -> RetryConditionEvidence | None:
        self.calls.append((latest_attempt.attempt_id, condition))
        if len(self.calls) == 1:
            return None
        return RetryConditionEvidence(
            condition=condition,
            satisfied=True,
            evidence_source="fresh-probe",
            observed_at=30,
            not_before=None,
        )


def test_condition_provider_is_invoked_fresh_after_each_reload() -> None:
    provider = _ChangingEvidenceProvider()
    provider_contract: RetryConditionEvidenceProvider = provider
    attempt = _bundle().previous_attempt

    first = read_fresh_retry_condition_evidence(
        provider_contract,
        latest_attempt=attempt,
        condition=RetryCondition.NETWORK_RECOVERED,
    )
    second = read_fresh_retry_condition_evidence(
        provider_contract,
        latest_attempt=replace(attempt, revision=attempt.revision + 1),
        condition=RetryCondition.NETWORK_RECOVERED,
    )

    assert first is None
    assert second is not None and second.satisfied
    assert provider.calls == [
        ("attempt-1", RetryCondition.NETWORK_RECOVERED),
        ("attempt-1", RetryCondition.NETWORK_RECOVERED),
    ]


def test_condition_evaluation_defers_without_guessing_and_blocks_session_reuse() -> None:
    missing = evaluate_retry_condition_evidence(
        RetryCondition.NETWORK_RECOVERED,
        None,
    )
    assert missing.stop_reason is RetryLoopStopReason.CONDITION_DEFERRED
    assert missing.not_before is None

    unsatisfied = RetryConditionEvidence(
        condition=RetryCondition.NETWORK_RECOVERED,
        satisfied=False,
        evidence_source="network-probe",
        observed_at=30,
        not_before=50,
    )
    deferred = evaluate_retry_condition_evidence(
        RetryCondition.NETWORK_RECOVERED,
        unsatisfied,
    )
    assert deferred.stop_reason is RetryLoopStopReason.CONDITION_DEFERRED
    assert deferred.not_before == 50

    satisfied = replace(unsatisfied, satisfied=True, not_before=None)
    permitted = evaluate_retry_condition_evidence(
        RetryCondition.NETWORK_RECOVERED,
        satisfied,
    )
    assert permitted.stop_reason is None
    assert permitted.evidence_for_adjudication is satisfied

    claimed_session_replacement = RetryConditionEvidence(
        condition=RetryCondition.SESSION_REPLACED_OR_REMOVED,
        satisfied=True,
        evidence_source="session-probe",
        observed_at=30,
        not_before=None,
    )
    session_invalid = evaluate_retry_condition_evidence(
        RetryCondition.SESSION_REPLACED_OR_REMOVED,
        claimed_session_replacement,
    )
    assert session_invalid.stop_reason is RetryLoopStopReason.CONDITION_DEFERRED
    assert session_invalid.evidence_for_adjudication is None

    plan = _current_plan()
    with pytest.raises(ValueError, match="RESUME requires a session"):
        replace(plan, session=None)
    with pytest.raises(
        ValueError,
        match="session requires SessionAction.RESUME",
    ):
        replace(
            plan,
            adapter_request=replace(
                plan.adapter_request,
                requested_session_action=SessionAction.NONE,
            ),
        )


@pytest.mark.parametrize(
    ("error", "expected"),
    (
        (
            StaleRevisionError("command-1", 1, 2),
            AuthorizationErrorSignal.RELOAD_DURABLE_STATE,
        ),
        (
            AttemptLimitReachedError("command-1", 3, 3),
            RetryLoopStopReason.ATTEMPT_LIMIT_REACHED,
        ),
        (
            RouteExhaustedError("command-1"),
            RetryLoopStopReason.ROUTE_EXHAUSTED,
        ),
        (
            CapabilityAuthorizationDeniedError("command-1", "denied"),
            RetryLoopStopReason.AUTHORIZATION_DENIED,
        ),
        (
            RetryRouteUnavailableError(
                "command-1",
                ErrorCode.PEER_UNAVAILABLE,
                "unavailable",
            ),
            AuthorizationErrorSignal.PREPARE_FAILOVER,
        ),
    ),
)
def test_authorization_error_classifier_returns_only_ratified_outcomes(
    error: BaseException,
    expected: AuthorizationErrorSignal | RetryLoopStopReason,
) -> None:
    assert classify_authorization_error(error) is expected


@pytest.mark.parametrize(
    "error",
    (
        RetryPolicyConflictError("command-1", 4, 3),
        PolicyStaleError(1, 2),
        RecordNotFoundError("request", "command-1"),
        CapabilityLeaseViolation("tampered binding"),
        InvalidStateTransitionError(
            "request",
            "command-1",
            "FAILED",
            "PREPARED",
        ),
        InvalidMutationError("invalid mutation"),
        RuntimeError("unexpected storage failure"),
    ),
)
def test_authorization_error_classifier_propagates_invariants_and_unknowns(
    error: BaseException,
) -> None:
    with pytest.raises(type(error)) as exc_info:
        classify_authorization_error(error)
    assert exc_info.value is error
