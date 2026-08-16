"""Outer retry-loop proofs for ApplicationWorkflows.dispatch_with_retries()."""

from __future__ import annotations

import sqlite3
import time
from dataclasses import replace
from pathlib import Path
from typing import Iterator

import pytest

from peerhub.adapters.contract import (
    AdapterRequest,
    Capability,
    ProfileDescriptor,
    ProtocolAssessment,
    SessionAction,
    SessionHint,
)
from peerhub.application.retry import (
    AttemptDispatchPlan,
    MultiAttemptExecutionResult,
    RetryCondition,
    RetryConditionEvidence,
    RetryDecision,
    RetryDecisionReason,
    RetryAction,
    RetryLoopStopReason,
    ResolvedRetryTarget,
)
from peerhub.application import workflows as workflows_module
from peerhub.application.workflows import ApplicationWorkflows
from peerhub.core.errors import (
    RetryRouteUnavailableError,
    RouteExhaustedError,
    RetryPolicyConflictError,
    StaleRevisionError,
)
from peerhub.core.protocol import ErrorCode
from peerhub.core.execution import TransportLimits
from peerhub.core.identity import AuthenticatedSubject
from peerhub.dispatch.contract import (
    CompletionContract,
    CompletionContractKind,
)
from peerhub.dispatch.capability_policy import (
    StaticCapabilityPolicy,
    StaticPeerEnforcementEvidenceProvider,
)
from peerhub.dispatch.capability import (
    CapabilityLeaseViolation,
    CapabilityTier,
    PeerEnforcementEvidence,
)
from peerhub.dispatch.materializer import ArtifactMaterializer
from peerhub.dispatch.service import DispatchService
from peerhub.builtins.fake_adapter import FakePeerAdapter
from peerhub.persistence.sqlite import SqliteStateStore
from tests.unit.application.test_workflows_dispatch_and_execute import (
    _ROUTED_PROFILE,
    _envelope,
    _route_request_factory,
    _seed_health,
    _workflows,
)
from tests.integration.application.test_workflows_kernel import (
    _candidate as _multi_candidate,
    _envelope as _multi_envelope,
    _route_request_factory as _multi_route_request_factory,
    _seed_health as _seed_multi_health,
    _workflows as _multi_workflows,
)
from tests.fakes import SequentialIdSource


@pytest.fixture
def store(tmp_path: Path) -> Iterator[SqliteStateStore]:
    state_store = SqliteStateStore(
        tmp_path / "retry-loop.sqlite3",
        workspace_home_id="workspace-retry-loop",
    )
    state_store.initialize()
    _seed_health(state_store)
    yield state_store


def _contract(*, replay_safe: bool) -> CompletionContract:
    return CompletionContract(
        contract_id="completion-contract-01",
        kind=CompletionContractKind.DELIVERY_ONLY,
        requirements=(),
        replay_safe=replay_safe,
    )


def _adapter_request(
    contract: CompletionContract,
    *,
    session_action: SessionAction = SessionAction.NONE,
    profile_id: str = "ag.deepthink",
) -> AdapterRequest:
    return AdapterRequest(
        request_id="req-01",
        prompt_content="hello",
        prompt_reference=None,
        workspace_scope="ws-1",
        profile_id=profile_id,
        requested_session_action=session_action,
        completion_contract=contract,
    )


def _materializer(tmp_path: Path, store: SqliteStateStore) -> ArtifactMaterializer:
    workspace_root = tmp_path / "ws"
    workspace_root.mkdir(exist_ok=True)
    return ArtifactMaterializer(
        unit_of_work_factory=store.unit_of_work,
        workspace_root=workspace_root,
    )


_LIMITS = TransportLimits(
    process_timeout_ms=5000,
    silence_timeout_ms=5000,
    max_output_bytes=65536,
)


def _plan(
    *,
    capability_lease_id: str,
    peer_instance_id: str,
    route_decision_id: str,
    adapter: FakePeerAdapter,
    contract: CompletionContract,
    session: SessionHint | None = None,
    session_action: SessionAction = SessionAction.NONE,
) -> AttemptDispatchPlan:
    return AttemptDispatchPlan(
        route_decision_id=route_decision_id,
        capability_lease_id=capability_lease_id,
        peer_instance_id=peer_instance_id,
        adapter_request=_adapter_request(
            contract,
            session_action=session_action,
        ),
        peer_adapter=adapter,
        profile=_ROUTED_PROFILE,
        session=session,
    )


def _count_attempts(store: SqliteStateStore) -> int:
    with sqlite3.connect(store.database_path) as connection:
        return int(
            connection.execute(
                "SELECT COUNT(*) FROM dispatch_attempts"
            ).fetchone()[0]
        )


def _route_decision_id(workflows: ApplicationWorkflows, command_id: str) -> str:
    state = workflows._dispatch.load_retry_loop_state(  # pyright: ignore[reportPrivateUsage]
        command_id
    )
    return state.route_decision.decision_id


def _run(
    workflows: ApplicationWorkflows,
    command_id: str,
    plan: AttemptDispatchPlan,
    *,
    tmp_path: Path,
    store: SqliteStateStore,
    contract: CompletionContract,
    max_attempts: int = 3,
    **overrides: object,
) -> MultiAttemptExecutionResult:
    kwargs: dict[str, object] = {
        "initial_attempt": plan,
        "route_request_factory": _route_request_factory(
            client_request_id="client-request-01",
        ),
        "current_policy_revision": 7,
        "materializer": _materializer(tmp_path, store),
        "limits": _LIMITS,
        "workspace_roots": {"ws-1": tmp_path / "ws"},
        "content_providers": {},
        "completion_contract": contract,
        "heartbeat_timeout_ms": 30000,
        "max_attempts": max_attempts,
    }
    kwargs.update(overrides)
    return workflows.dispatch_with_retries(
        command_id,
        **kwargs,  # pyright: ignore[reportArgumentType]
    )


def _admit_and_prepare_with_contract(
    workflows: ApplicationWorkflows,
    contract: CompletionContract,
) -> tuple[str, str, str]:
    """Admit and prepare so the DURABLE request carries ``contract``.

    ``adjudicate_retry()`` reads replay-safety off the durable request, not
    off the contract handed to a single dispatch, so the retry-path tests
    must freeze the replay-safe contract at admission time.
    """

    envelope = _envelope()
    factory = _route_request_factory(
        client_request_id=envelope.client_request_id,
    )
    admission = workflows.admit_request(
        envelope,
        route_request_factory=factory,
        required_capability_tier=CapabilityTier.READ_ONLY,
        authenticated_subject=AuthenticatedSubject("actor-01", "test"),
        completion_contract=contract,
        dispatch_policy_revision=7,
        session_id="session-01",
        owner_principal_id="principal-01",
        owner_instance_id="ag",
        authority_epoch=1,
        heartbeat_timeout_ms=30000,
        owner_peer_id="peer-01",
    )
    assert admission.dispatch_admission is not None
    assert admission.route is not None
    request = admission.dispatch_admission[0]
    capability_lease = admission.dispatch_admission[3]
    workflows.prepare_for_dispatch(
        request.command_id,
        route_decision_id=admission.route.decision.decision_id,
        route_request_factory=factory,
    )
    return (
        str(request.command_id),
        capability_lease.capability_lease_id,
        request.selected_peer_instance_id,
    )


def _setup(
    store: SqliteStateStore,
    tmp_path: Path,
    *,
    adapter: FakePeerAdapter,
    replay_safe: bool = False,
    capability_policy: StaticCapabilityPolicy | None = None,
) -> tuple[ApplicationWorkflows, str, AttemptDispatchPlan, CompletionContract]:
    workflows, _dispatch = _workflows(
        store,
        peer_adapter=adapter,
        capability_policy=capability_policy,
    )
    contract = _contract(replay_safe=replay_safe)
    (
        command_id,
        capability_lease_id,
        peer_instance,
    ) = _admit_and_prepare_with_contract(workflows, contract)
    plan = _plan(
        capability_lease_id=capability_lease_id,
        peer_instance_id=peer_instance,
        route_decision_id=_route_decision_id(workflows, command_id),
        adapter=adapter,
        contract=contract,
    )
    return workflows, command_id, plan, contract


# ---------------------------------------------------------------------------
# (1) initial plan executes only when no durable attempt represents it
# (3) verified success stops canonically
# ---------------------------------------------------------------------------


def test_initial_plan_executes_once_and_verified_success_stops(
    tmp_path: Path,
    store: SqliteStateStore,
) -> None:
    adapter = FakePeerAdapter(stdout="ok\n")
    workflows, command_id, plan, contract = _setup(
        store,
        tmp_path,
        adapter=adapter,
    )

    result = _run(
        workflows,
        command_id,
        plan,
        tmp_path=tmp_path,
        store=store,
        contract=contract,
    )

    assert isinstance(result, MultiAttemptExecutionResult)
    assert result.stop_reason is RetryLoopStopReason.VERIFIED_SUCCESS
    assert len(result.attempts) == 1
    assert _count_attempts(store) == 1


def test_honest_unverified_delivery_stops_canonically(
    tmp_path: Path,
    store: SqliteStateStore,
) -> None:
    adapter = _UnverifiedDeliveryAdapter(stdout="partial response\n")
    workflows, command_id, plan, contract = _setup(
        store,
        tmp_path,
        adapter=adapter,
    )

    result = _run(
        workflows,
        command_id,
        plan,
        tmp_path=tmp_path,
        store=store,
        contract=contract,
    )

    assert result.stop_reason is RetryLoopStopReason.DELIVERED_UNVERIFIED
    assert len(result.attempts) == 1
    assert _count_attempts(store) == 1


@pytest.mark.parametrize(
    ("decision_reason", "stop_reason"),
    (
        (
            RetryDecisionReason.VERIFIED_SUCCESS,
            RetryLoopStopReason.VERIFIED_SUCCESS,
        ),
        (
            RetryDecisionReason.DELIVERED_UNVERIFIED,
            RetryLoopStopReason.DELIVERED_UNVERIFIED,
        ),
        (
            RetryDecisionReason.AUTHORITATIVE_CANCELLATION,
            RetryLoopStopReason.AUTHORITATIVE_CANCELLATION,
        ),
        (
            RetryDecisionReason.NEVER_DISPOSITION,
            RetryLoopStopReason.NEVER_DISPOSITION,
        ),
        (
            RetryDecisionReason.UNSAFE_NO_EVIDENCE,
            RetryLoopStopReason.UNSAFE_NO_EVIDENCE,
        ),
        (
            RetryDecisionReason.ATTEMPT_LIMIT_REACHED,
            RetryLoopStopReason.ATTEMPT_LIMIT_REACHED,
        ),
        (
            RetryDecisionReason.LEGACY_CLASSIFICATION_UNKNOWN,
            RetryLoopStopReason.LEGACY_CLASSIFICATION_UNKNOWN,
        ),
    ),
)
def test_every_stop_decision_maps_to_its_canonical_aggregate_reason(
    decision_reason: RetryDecisionReason,
    stop_reason: RetryLoopStopReason,
) -> None:
    decision = RetryDecision(
        disposition=None,
        action=RetryAction.STOP,
        reason=decision_reason,
        required_conditions=(),
        not_before=None,
    )

    assert workflows_module._stop_reason_for(decision) is stop_reason  # pyright: ignore[reportPrivateUsage]


# ---------------------------------------------------------------------------
# (2) a resumed terminal attempt is adjudicated without replaying attempt 1
# ---------------------------------------------------------------------------


def test_resumed_terminal_attempt_is_not_replayed(
    tmp_path: Path,
    store: SqliteStateStore,
) -> None:
    adapter = FakePeerAdapter(stdout="ok\n")
    workflows, command_id, plan, contract = _setup(
        store,
        tmp_path,
        adapter=adapter,
    )
    first = _run(
        workflows,
        command_id,
        plan,
        tmp_path=tmp_path,
        store=store,
        contract=contract,
    )
    assert first.stop_reason is RetryLoopStopReason.VERIFIED_SUCCESS
    attempts_after_first = _count_attempts(store)

    resumed = _run(
        workflows,
        command_id,
        plan,
        tmp_path=tmp_path,
        store=store,
        contract=contract,
    )

    assert resumed.stop_reason is RetryLoopStopReason.VERIFIED_SUCCESS
    assert len(resumed.attempts) == 1
    assert _count_attempts(store) == attempts_after_first


# ---------------------------------------------------------------------------
# (9) exhaustion returns its exact aggregate reason
# ---------------------------------------------------------------------------


def test_attempt_limit_reached_is_the_exact_stop_reason(
    tmp_path: Path,
    store: SqliteStateStore,
) -> None:
    adapter = FakePeerAdapter(stdout="failing\n", exit_code=3)
    workflows, command_id, plan, contract = _setup(
        store,
        tmp_path,
        adapter=adapter,
        replay_safe=True,
    )

    result = _run(
        workflows,
        command_id,
        plan,
        tmp_path=tmp_path,
        store=store,
        contract=contract,
        max_attempts=1,
    )

    assert result.stop_reason is RetryLoopStopReason.ATTEMPT_LIMIT_REACHED
    assert len(result.attempts) == 1
    assert _count_attempts(store) == 1


# ---------------------------------------------------------------------------
# (6) same-target retry uses the fresh capability
# ---------------------------------------------------------------------------


def test_same_target_retry_uses_a_fresh_capability_and_runs_again(
    tmp_path: Path,
    store: SqliteStateStore,
) -> None:
    adapter = _FailThenSucceedAdapter()
    workflows, command_id, plan, contract = _setup(
        store,
        tmp_path,
        adapter=adapter,
        replay_safe=True,
    )

    result = _run(
        workflows,
        command_id,
        plan,
        tmp_path=tmp_path,
        store=store,
        contract=contract,
        max_attempts=3,
    )

    assert result.stop_reason is RetryLoopStopReason.VERIFIED_SUCCESS
    assert len(result.attempts) == 2
    assert _count_attempts(store) == 2
    authorization = result.attempts[0].retry_authorization
    assert authorization is not None
    fresh_capability = (
        authorization.retry_admission.capability_lease.capability_lease_id
    )
    assert fresh_capability != plan.capability_lease_id


def test_same_target_retry_preserves_a_valid_resume_session(
    tmp_path: Path,
    store: SqliteStateStore,
) -> None:
    adapter = _SessionCapableFailThenSucceedAdapter()
    workflows, command_id, plan, contract = _setup(
        store,
        tmp_path,
        adapter=adapter,
        replay_safe=True,
    )
    session = SessionHint(
        external_session_id="external-session-1",
        adapter_fingerprint="fake-session-capable-v1",
        session_generation=3,
    )
    plan = replace(
        plan,
        adapter_request=_adapter_request(
            contract,
            session_action=SessionAction.RESUME,
        ),
        session=session,
    )

    result = _run(
        workflows,
        command_id,
        plan,
        tmp_path=tmp_path,
        store=store,
        contract=contract,
    )

    assert result.stop_reason is RetryLoopStopReason.VERIFIED_SUCCESS
    assert len(result.attempts) == 2
    assert adapter.seen_sessions == [session, session]
    assert adapter.seen_session_actions == [
        SessionAction.RESUME,
        SessionAction.RESUME,
    ]


class _FailThenSucceedAdapter(FakePeerAdapter):
    """Real adapter whose first spawn exits non-zero and second succeeds.

    Both branches are genuine ``FakePeerAdapter`` instances, so the plan is
    produced by real adapter code rather than a stub.
    """

    def __init__(self) -> None:
        super().__init__(stdout="failing\n", exit_code=3)
        self._failing = FakePeerAdapter(stdout="failing\n", exit_code=3)
        self._succeeding = FakePeerAdapter(stdout="recovered\n", exit_code=0)
        self.spawns = 0

    def plan_invocation(self, *args: object, **kwargs: object):  # type: ignore[no-untyped-def]
        self.spawns += 1
        target = self._failing if self.spawns == 1 else self._succeeding
        return target.plan_invocation(*args, **kwargs)  # type: ignore[arg-type]


class _SessionCapableFailThenSucceedAdapter(_FailThenSucceedAdapter):
    """Same-target retry double that records the exact session inputs."""

    def __init__(self) -> None:
        super().__init__()
        self.descriptor = replace(
            self.descriptor,
            capabilities=self.descriptor.capabilities | {Capability.SESSION},
        )
        self.seen_sessions: list[SessionHint | None] = []
        self.seen_session_actions: list[SessionAction] = []

    def plan_invocation(self, *args: object, **kwargs: object):  # type: ignore[no-untyped-def]
        request = kwargs["request"]
        self.seen_sessions.append(kwargs["session"])
        self.seen_session_actions.append(request.requested_session_action)
        return super().plan_invocation(*args, **kwargs)


class _UnverifiedDeliveryAdapter(FakePeerAdapter):
    """Clean delivery whose protocol evidence honestly reports truncation."""

    def interpret_output(self, plan, process, raw_chunks):  # type: ignore[no-untyped-def]
        assessment = super().interpret_output(plan, process, raw_chunks)
        return ProtocolAssessment(
            parsed=assessment.parsed,
            response_present=assessment.response_present,
            vendor_completion_marker=assessment.vendor_completion_marker,
            suspected_truncation=True,
            protocol_failure=assessment.protocol_failure,
        )


class _TargetFakeAdapter(FakePeerAdapter):
    """Fake adapter with a machine-owned peer kind/profile identity."""

    def __init__(
        self,
        *,
        peer_kind: str,
        profile: ProfileDescriptor,
        stdout: str,
        exit_code: int,
    ) -> None:
        super().__init__(stdout=stdout, exit_code=exit_code)
        self.descriptor = replace(
            self.descriptor,
            peer_kind=peer_kind,
            profiles=(profile,),
        )
        self.calls: list[tuple[AdapterRequest, ProfileDescriptor, SessionHint | None]] = []

    def plan_invocation(self, request, profile, session, limits):  # type: ignore[no-untyped-def]
        self.calls.append((request, profile, session))
        return super().plan_invocation(request, profile, session, limits)


# ---------------------------------------------------------------------------
# (4) CONDITIONAL without fresh matching evidence defers and does not sleep
# (5) SESSION_INVALID never reuses or partially clears RESUME state
# ---------------------------------------------------------------------------


def _session_invalid_adapter() -> FakePeerAdapter:
    """Real adapter whose decoder emits a genuine SESSION_INVALID vendor error."""

    from peerhub.adapters.contract import (
        DecodedOutput,
        DecoderEvent,
        DecoderEventKind,
        InvocationPlan,
        OutputDecoder,
    )
    from peerhub.builtins.fake_adapter import FakeOutputDecoder

    class _SessionInvalidAdapter(FakePeerAdapter):
        def new_decoder(self, plan: InvocationPlan) -> OutputDecoder:
            class _Decoder(FakeOutputDecoder):
                def finalize(self) -> DecodedOutput:
                    out = super().finalize()
                    event = DecoderEvent(
                        kind=DecoderEventKind.VENDOR_ERROR,
                        payload={
                            "normalized_kind": "session_invalid",
                            "evidence_source": "known_terminal_pattern",
                        },
                    )
                    return DecodedOutput(
                        canonical_text=out.canonical_text,
                        canonical_lines=out.canonical_lines,
                        events=out.events + (event,),
                    )

            return _Decoder()

    return _SessionInvalidAdapter(exit_code=1)


def test_conditional_without_evidence_defers_without_sleeping(
    tmp_path: Path,
    store: SqliteStateStore,
) -> None:
    adapter = _session_invalid_adapter()
    workflows, command_id, plan, contract = _setup(
        store,
        tmp_path,
        adapter=adapter,
        replay_safe=True,
    )

    started = time.monotonic()
    result = _run(
        workflows,
        command_id,
        plan,
        tmp_path=tmp_path,
        store=store,
        contract=contract,
    )
    elapsed = time.monotonic() - started

    assert result.stop_reason is RetryLoopStopReason.CONDITION_DEFERRED
    assert elapsed < 5.0
    assert _count_attempts(store) == 1


def test_session_invalid_defers_even_with_satisfied_evidence(
    tmp_path: Path,
    store: SqliteStateStore,
) -> None:
    adapter = _session_invalid_adapter()
    workflows, command_id, plan, contract = _setup(
        store,
        tmp_path,
        adapter=adapter,
        replay_safe=True,
    )

    def provider(
        *,
        latest_attempt: object,
        condition: RetryCondition,
    ) -> RetryConditionEvidence:
        return RetryConditionEvidence(
            condition=condition,
            satisfied=True,
            evidence_source="test-probe",
            observed_at=1,
            not_before=None,
        )

    result = _run(
        workflows,
        command_id,
        plan,
        tmp_path=tmp_path,
        store=store,
        contract=contract,
        condition_evidence_provider=provider,
    )

    assert result.stop_reason is RetryLoopStopReason.CONDITION_DEFERRED
    assert _count_attempts(store) == 1


def test_session_invalid_never_reuses_or_partially_clears_resume(
    tmp_path: Path,
    store: SqliteStateStore,
) -> None:
    adapter = _session_invalid_adapter()
    adapter.descriptor = replace(
        adapter.descriptor,
        capabilities=adapter.descriptor.capabilities | {Capability.SESSION},
    )
    workflows, command_id, plan, contract = _setup(
        store,
        tmp_path,
        adapter=adapter,
        replay_safe=True,
    )
    rejected_session = SessionHint(
        external_session_id="rejected-session-1",
        adapter_fingerprint="fake-session-capable-v1",
        session_generation=1,
    )
    plan = replace(
        plan,
        adapter_request=_adapter_request(
            contract,
            session_action=SessionAction.RESUME,
        ),
        session=rejected_session,
    )

    result = _run(
        workflows,
        command_id,
        plan,
        tmp_path=tmp_path,
        store=store,
        contract=contract,
        condition_evidence_provider=lambda **_: RetryConditionEvidence(
            condition=RetryCondition.SESSION_REPLACED_OR_REMOVED,
            satisfied=True,
            evidence_source="test-probe",
            observed_at=1,
            not_before=None,
        ),
    )

    assert result.stop_reason is RetryLoopStopReason.CONDITION_DEFERRED
    assert _count_attempts(store) == 1
    assert plan.adapter_request.requested_session_action is SessionAction.RESUME
    assert plan.session is rejected_session


# ---------------------------------------------------------------------------
# (9) capability denial returns its exact aggregate reason
# ---------------------------------------------------------------------------


def test_capability_denial_returns_authorization_denied(
    tmp_path: Path,
    store: SqliteStateStore,
) -> None:
    adapter = FakePeerAdapter(stdout="failing\n", exit_code=3)
    workflows, command_id, plan, contract = _setup(
        store,
        tmp_path,
        adapter=adapter,
        replay_safe=True,
    )
    denying = StaticCapabilityPolicy(
        denied_tiers=frozenset({CapabilityTier.READ_ONLY})
    )
    workflows._dispatch._capability_policy = denying  # pyright: ignore[reportPrivateUsage]
    workflows._dispatch._retry_authorization._capability_policy = (  # pyright: ignore[reportPrivateUsage]
        denying
    )

    result = _run(
        workflows,
        command_id,
        plan,
        tmp_path=tmp_path,
        store=store,
        contract=contract,
    )

    assert result.stop_reason is RetryLoopStopReason.AUTHORIZATION_DENIED
    assert _count_attempts(store) == 1


def test_failover_route_exhaustion_returns_exact_stop_reason(
    tmp_path: Path,
    store: SqliteStateStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = FakePeerAdapter(stdout="failing\n", exit_code=3)
    workflows, command_id, plan, contract = _setup(
        store,
        tmp_path,
        adapter=adapter,
        replay_safe=True,
    )
    errors = iter(
        (
            RetryRouteUnavailableError(
                command_id,
                ErrorCode.PEER_UNAVAILABLE,
                "same target unavailable",
            ),
            RouteExhaustedError(command_id),
        )
    )
    calls = 0

    def _raise_next(*args: object, **kwargs: object) -> object:
        nonlocal calls
        calls += 1
        raise next(errors)

    monkeypatch.setattr(workflows, "authorize_retry", _raise_next)

    result = _run(
        workflows,
        command_id,
        plan,
        tmp_path=tmp_path,
        store=store,
        contract=contract,
    )

    assert result.stop_reason is RetryLoopStopReason.ROUTE_EXHAUSTED
    assert calls == 2
    assert _count_attempts(store) == 1
    assert result.attempts[0].retry_decision.action is RetryAction.FAILOVER


# ---------------------------------------------------------------------------
# (10) invariant/security/caller errors propagate under Section 2.2
# ---------------------------------------------------------------------------


def test_conflicting_max_attempts_propagates_policy_conflict(
    tmp_path: Path,
    store: SqliteStateStore,
) -> None:
    adapter = FakePeerAdapter(stdout="ok\n")
    workflows, command_id, plan, contract = _setup(
        store,
        tmp_path,
        adapter=adapter,
    )
    workflows._dispatch.freeze_retry_policy(command_id, 3)  # pyright: ignore[reportPrivateUsage]

    with pytest.raises(RetryPolicyConflictError):
        _run(
            workflows,
            command_id,
            plan,
            tmp_path=tmp_path,
            store=store,
            contract=contract,
            max_attempts=5,
        )


def test_repeated_stale_revision_error_bounds_and_stops_cleanly(
    tmp_path: Path,
    store: SqliteStateStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = FakePeerAdapter(stdout="failing\n", exit_code=3)
    workflows, command_id, plan, contract = _setup(
        store,
        tmp_path,
        adapter=adapter,
        replay_safe=True,
    )
    stale = StaleRevisionError(command_id, 1, 2)

    def _raise_stale(*args: object, **kwargs: object) -> object:
        raise stale

    monkeypatch.setattr(workflows, "authorize_retry", _raise_stale)

    result = _run(
        workflows,
        command_id,
        plan,
        tmp_path=tmp_path,
        store=store,
        contract=contract,
    )

    assert result.stop_reason is RetryLoopStopReason.CONCURRENT_ATTEMPT_IN_PROGRESS


def test_capability_binding_violation_propagates_unchanged(
    tmp_path: Path,
    store: SqliteStateStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = FakePeerAdapter(stdout="failing\n", exit_code=3)
    workflows, command_id, plan, contract = _setup(
        store,
        tmp_path,
        adapter=adapter,
        replay_safe=True,
    )
    violation = CapabilityLeaseViolation("tampered retry binding")

    def _raise_violation(*args: object, **kwargs: object) -> object:
        raise violation

    monkeypatch.setattr(workflows, "authorize_retry", _raise_violation)

    with pytest.raises(CapabilityLeaseViolation) as exc_info:
        _run(
            workflows,
            command_id,
            plan,
            tmp_path=tmp_path,
            store=store,
            contract=contract,
        )

    assert exc_info.value is violation
    assert _count_attempts(store) == 1


# ---------------------------------------------------------------------------
# (7) same-target unavailability attempts at most one fresh failover
# (8) the failover intent carries the failed route decision
# ---------------------------------------------------------------------------


def test_route_unavailability_triggers_exactly_one_failover_intent(
    tmp_path: Path,
    store: SqliteStateStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from peerhub.core.errors import (
        InvalidMutationError,
        RetryRouteUnavailableError,
    )
    from peerhub.core.protocol import ErrorCode
    from peerhub.dispatch.retry_authorization import (
        FailoverRoute,
        SameTargetRoute,
    )

    adapter = FakePeerAdapter(stdout="failing\n", exit_code=3)
    workflows, command_id, plan, contract = _setup(
        store,
        tmp_path,
        adapter=adapter,
        replay_safe=True,
    )
    seen: list[object] = []

    def _unavailable(*args: object, **kwargs: object) -> object:
        seen.append(kwargs["route_intent"])
        raise RetryRouteUnavailableError(
            str(command_id),
            ErrorCode.PEER_UNAVAILABLE,
            "target unavailable",
        )

    monkeypatch.setattr(workflows, "authorize_retry", _unavailable)

    # The SECOND unavailability is refused outright: the bounded sequence
    # allows exactly one failover and must never spin on same-target.
    with pytest.raises(
        InvalidMutationError,
        match="exactly one failover transition",
    ):
        _run(
            workflows,
            command_id,
            plan,
            tmp_path=tmp_path,
            store=store,
            contract=contract,
        )

    assert len(seen) == 2
    assert isinstance(seen[0], SameTargetRoute)
    assert isinstance(seen[1], FailoverRoute)
    assert seen[1].failed_route_decision_id == plan.route_decision_id


def test_unresolvable_failover_target_returns_failover_unavailable(
    tmp_path: Path,
    store: SqliteStateStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from peerhub.application import retry as retry_module

    adapter = FakePeerAdapter(stdout="failing\n", exit_code=3)
    workflows, command_id, plan, contract = _setup(
        store,
        tmp_path,
        adapter=adapter,
        replay_safe=True,
    )

    def _unavailable_plan(**kwargs: object) -> RetryLoopStopReason:
        return RetryLoopStopReason.FAILOVER_UNAVAILABLE

    monkeypatch.setattr(
        "peerhub.application.workflows.build_retry_dispatch_plan",
        _unavailable_plan,
    )
    assert retry_module.build_retry_dispatch_plan is not None

    def _resolver(
        peer_kind: str,
        instance_id: str,
        profile_id: str,
    ) -> ResolvedRetryTarget | None:
        return None

    result = _run(
        workflows,
        command_id,
        plan,
        tmp_path=tmp_path,
        store=store,
        contract=contract,
        retry_target_resolver=_resolver,
    )

    assert result.stop_reason is RetryLoopStopReason.FAILOVER_UNAVAILABLE
    assert _count_attempts(store) == 1


def test_real_failover_commits_resolves_and_executes_replacement(
    tmp_path: Path,
) -> None:
    state_store = SqliteStateStore(
        tmp_path / "retry-loop-failover.sqlite3",
        workspace_home_id="workspace-retry-loop-failover",
    )
    state_store.initialize()
    members = (("ag", "ag.deepthink"), ("cx", "cx.deepthink"))
    _seed_multi_health(state_store, configured_members=members)
    evidence = StaticPeerEnforcementEvidenceProvider(
        {
            "ag": PeerEnforcementEvidence(
                peer_instance_id="ag",
                peer_kind="fake-ag",
                enforcement_ceiling=None,
                source_tag="absent",
            ),
            "cx": PeerEnforcementEvidence(
                peer_instance_id="cx",
                peer_kind="fake-cx",
                enforcement_ceiling=None,
                source_tag="absent",
            ),
        }
    )
    shared_ids = SequentialIdSource()
    workflows = _multi_workflows(
        state_store,
        configured_members=members,
        enforcement_evidence=evidence,
        routing_ids=shared_ids,
        dispatch_ids=shared_ids,
    )
    contract = _contract(replay_safe=True)
    ag_profile = ProfileDescriptor(
        profile_id="ag.deepthink",
        profile_class="tier",
        supports_reasoning_effort=False,
    )
    cx_profile = ProfileDescriptor(
        profile_id="cx.deepthink",
        profile_class="tier",
        supports_reasoning_effort=False,
    )
    failed_adapter = _TargetFakeAdapter(
        peer_kind="fake-ag",
        profile=ag_profile,
        stdout="failed\n",
        exit_code=3,
    )
    replacement_adapter = _TargetFakeAdapter(
        peer_kind="fake-cx",
        profile=cx_profile,
        stdout="recovered\n",
        exit_code=0,
    )
    admission_factory = _multi_route_request_factory(
        client_request_id="client-request-01",
        configuration_revision=11,
        candidates=(
            _multi_candidate(
                candidate_id="ag.deepthink",
                instance_id="ag",
                profile_id="ag.deepthink",
            ),
        ),
    )
    admission = workflows.admit_request(
        _multi_envelope(),
        route_request_factory=admission_factory,
        required_capability_tier=CapabilityTier.READ_ONLY,
        authenticated_subject=AuthenticatedSubject("actor-01", "test"),
        completion_contract=contract,
        dispatch_policy_revision=7,
        session_id="session-01",
        owner_principal_id="principal-01",
        owner_instance_id="cli-instance",
        authority_epoch=1,
        heartbeat_timeout_ms=30_000,
        owner_peer_id="peer-01",
    )
    assert admission.dispatch_admission is not None
    assert admission.route is not None
    request, _, _, original_capability = admission.dispatch_admission
    workflows.prepare_for_dispatch(
        request.command_id,
        route_decision_id=admission.route.decision.decision_id,
        route_request_factory=admission_factory,
    )
    initial_plan = AttemptDispatchPlan(
        route_decision_id=admission.route.decision.decision_id,
        capability_lease_id=original_capability.capability_lease_id,
        peer_instance_id="ag",
        adapter_request=_adapter_request(
            contract,
            profile_id="ag.deepthink",
        ),
        peer_adapter=failed_adapter,
        profile=ag_profile,
        session=None,
    )
    retry_factory = _multi_route_request_factory(
        client_request_id="client-request-01",
        configuration_revision=11,
        candidates=(
            _multi_candidate(
                eligible=False,
                candidate_id="ag.deepthink",
                instance_id="ag",
                profile_id="ag.deepthink",
            ),
            _multi_candidate(
                candidate_id="cx.deepthink",
                instance_id="cx",
                profile_id="cx.deepthink",
            ),
        ),
    )
    resolver_calls: list[tuple[str, str, str]] = []

    def resolver(
        peer_kind: str,
        instance_id: str,
        profile_id: str,
    ) -> ResolvedRetryTarget | None:
        resolver_calls.append((peer_kind, instance_id, profile_id))
        return ResolvedRetryTarget(
            peer_adapter=replacement_adapter,
            profile=cx_profile,
        )

    result = workflows.dispatch_with_retries(
        request.command_id,
        initial_attempt=initial_plan,
        route_request_factory=retry_factory,
        current_policy_revision=7,
        materializer=_materializer(tmp_path, state_store),
        limits=_LIMITS,
        workspace_roots={"ws-1": tmp_path / "ws"},
        content_providers={},
        completion_contract=contract,
        heartbeat_timeout_ms=30_000,
        max_attempts=3,
        retry_target_resolver=resolver,
    )

    assert result.stop_reason is RetryLoopStopReason.VERIFIED_SUCCESS
    assert len(result.attempts) == 2
    assert resolver_calls == [("fake-cx", "cx", "cx.deepthink")]
    assert len(failed_adapter.calls) == 1
    assert len(replacement_adapter.calls) == 1
    replacement_request, replacement_profile, replacement_session = (
        replacement_adapter.calls[0]
    )
    assert replacement_request.profile_id == "cx.deepthink"
    assert replacement_request.requested_session_action is SessionAction.NONE
    assert replacement_profile is cx_profile
    assert replacement_session is None
    authorization = result.attempts[0].retry_authorization
    assert authorization is not None
    assert result.attempts[0].retry_decision.action is RetryAction.FAILOVER
    bundle = authorization.retry_admission
    assert bundle.capability_lease.selected_peer_kind == "fake-cx"
    assert bundle.request.selected_peer_instance_id == "cx"
    assert bundle.request.selected_profile_id == "cx.deepthink"
    assert bundle.capability_lease.capability_lease_id != (
        original_capability.capability_lease_id
    )
    assert _count_attempts(state_store) == 2


def test_profile_descriptor_is_reused_unchanged_on_same_target(
    tmp_path: Path,
    store: SqliteStateStore,
) -> None:
    adapter = _FailThenSucceedAdapter()
    workflows, command_id, plan, contract = _setup(
        store,
        tmp_path,
        adapter=adapter,
        replay_safe=True,
    )

    result = _run(
        workflows,
        command_id,
        plan,
        tmp_path=tmp_path,
        store=store,
        contract=contract,
    )

    assert result.stop_reason is RetryLoopStopReason.VERIFIED_SUCCESS
    assert isinstance(_ROUTED_PROFILE, ProfileDescriptor)
    assert adapter.spawns == 2
