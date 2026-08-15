"""Real-SQLite proofs for same-target atomic retry authorization."""

from __future__ import annotations

import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from pathlib import Path

import pytest

from peerhub.adapters.contract import ProfileDescriptor
from peerhub.core.errors import (
    AttemptLimitReachedError,
    CapabilityAuthorizationDeniedError,
    InvalidMutationError,
    PolicyStaleError,
    RecordNotFoundError,
    RetryPolicyConflictError,
    RetryRouteUnavailableError,
    RouteExhaustedError,
    StaleRevisionError,
)
from peerhub.core.protocol import ErrorCode, OperationalFailureCategory, RevisionValue
from peerhub.dispatch.capability import (
    CapabilityGrantDecision,
    CapabilityLease,
    CapabilityLeaseViolation,
    CapabilityTier,
    EnforcementLevel,
    PeerEnforcementEvidence,
    PeerEnforcementEvidenceProvider,
)
from peerhub.dispatch.capability_policy import StaticCapabilityPolicy
from peerhub.dispatch.capability_policy import (
    StaticPeerEnforcementEvidenceProvider,
)
from peerhub.dispatch.contract import (
    AdmissionReceipt,
    AttemptSnapshot,
    LeaseSnapshot,
    RequestSnapshot,
)
from peerhub.dispatch.retry_authorization import (
    FAILED_TARGET_EXCLUDED_BY_RETRY,
    FailoverRoute,
    RetryAuthorizationBundle,
    SameTargetRoute,
)
from peerhub.dispatch.service import DispatchService
from peerhub.dispatch.unit_of_work import FaultInjector, FaultPoint
from peerhub.persistence.sqlite import SqliteStateStore
from peerhub.routing.contract import RouteDecision, RouteEligibility
from peerhub.routing.model import select_equal_weight_candidate, select_route
from tests.fakes import DeterministicClock, SequentialIdSource
from tests.integration.application.test_workflows_kernel import (
    _admission_kwargs,
    _candidate,
    _envelope,
    _route_request_factory,
    _seed_health,
    _workflows,
)


class _TaggedIds:
    """Thread-safe unique IDs with an allocation audit."""

    def __init__(self, tag: str) -> None:
        self._tag = tag
        self._counts: dict[str, int] = {}
        self._lock = threading.Lock()
        self.namespaces: list[str] = []

    def new_id(self, namespace: str) -> str:
        with self._lock:
            self.namespaces.append(namespace)
            count = self._counts.get(namespace, 0) + 1
            self._counts[namespace] = count
        return f"{namespace}-{self._tag}-{count}"


class _RaisingFaults(FaultInjector):
    def __init__(self, target: str) -> None:
        self._target = target

    def hit(self, point: str) -> None:
        if point == self._target:
            raise RuntimeError(f"injected fault at {point}")


class _DeniedDecisionWithSufficientFloorPolicy(StaticCapabilityPolicy):
    """Deny while preserving fields that satisfy every later grant check."""

    def decide(
        self,
        *,
        subject_principal_id: str,
        selected_peer_kind: str,
        selected_peer_instance_id: str,
        selected_profile_id: str,
        policy_revision: RevisionValue,
        required_tier: CapabilityTier,
        minimum_enforcement: EnforcementLevel,
    ) -> CapabilityGrantDecision:
        decision = super().decide(
            subject_principal_id=subject_principal_id,
            selected_peer_kind=selected_peer_kind,
            selected_peer_instance_id=selected_peer_instance_id,
            selected_profile_id=selected_profile_id,
            policy_revision=policy_revision,
            required_tier=required_tier,
            minimum_enforcement=minimum_enforcement,
        )
        # CapabilityGrantDecision normally forbids a denial carrying grant fields.
        # This adversarial boundary value isolates the coordinator's explicit
        # ``granted`` check from its later binding and enforcement backstops.
        object.__setattr__(decision, "granted", False)
        return decision


@dataclass(frozen=True)
class _RetryCase:
    store: SqliteStateStore
    dispatch: DispatchService
    request: RequestSnapshot
    attempt: AttemptSnapshot
    original_receipt: AdmissionReceipt
    original_lease: LeaseSnapshot
    original_capability: CapabilityLease
    original_route_decision: RouteDecision
    route_intent: SameTargetRoute


@pytest.fixture
def store(tmp_path: Path) -> SqliteStateStore:
    state_store = SqliteStateStore(
        tmp_path / "retry-authorization.sqlite3",
        workspace_home_id="workspace-retry-authorization",
    )
    state_store.initialize()
    return state_store


def _setup_retry_case(
    store: SqliteStateStore,
    *,
    max_attempts: int | None = 3,
    start_uncertain: bool = False,
    operational_failure_category: OperationalFailureCategory | None = None,
    required_capability_tier: CapabilityTier = CapabilityTier.READ_ONLY,
    enforcement_evidence: PeerEnforcementEvidenceProvider | None = None,
    fail_attempt: bool = True,
    failover_ready: bool = False,
) -> _RetryCase:
    configured_members = (
        (("ag", "ag.deepthink"), ("cx", "cx.deepthink"))
        if failover_ready
        else (("ag", "ag.deepthink"),)
    )
    _seed_health(store, configured_members=configured_members)
    dispatch_ids = SequentialIdSource()
    if failover_ready:
        # Routing and dispatch use independent deterministic sources in this
        # fixture; reserve the ID already used by initial route admission.
        assert dispatch_ids.new_id("route-decision") == "route-decision-1"
    workflows = _workflows(
        store,
        dispatch_ids=dispatch_ids,
        enforcement_evidence=enforcement_evidence,
        configured_members=configured_members,
    )
    admission_kwargs = _admission_kwargs()
    admission_kwargs["owner_instance_id"] = "cli-instance"
    admission_kwargs["required_capability_tier"] = required_capability_tier
    admission = workflows.admit_request(
        _envelope(),
        route_request_factory=_route_request_factory(
            client_request_id="client-request-01",
            configuration_revision=11,
            required_capability_tier=required_capability_tier,
        ),
        **admission_kwargs,
    )
    assert admission.dispatch_admission is not None
    assert admission.route is not None
    request, receipt, original_lease, capability = admission.dispatch_admission
    dispatch = workflows._dispatch  # pyright: ignore[reportPrivateUsage]
    if max_attempts is not None:
        dispatch.freeze_retry_policy(request.command_id, max_attempts)
    prepared = dispatch.prepare_request(request.command_id)
    attempt = dispatch.create_attempt(request.command_id, expected_authorized_attempt_number=1)
    if not fail_attempt:
        failed_request, failed_attempt = prepared, attempt
    elif start_uncertain:
        dispatch.record_dispatch_intent(
            prepared.command_id,
            attempt.attempt_id,
        )
        failed_request, failed_attempt = dispatch.record_start_uncertain(
            prepared.command_id,
            attempt.attempt_id,
        )
    else:
        failed_request, failed_attempt = dispatch.fail_pre_dispatch(
            prepared.command_id,
            attempt.attempt_id,
            error_code=ErrorCode.SPAWN_FAILED,
            transport="pipe",
            operational_failure_category=operational_failure_category,
        )
    current_candidates = (
        (
            _candidate(),
            _candidate(
                candidate_id="cx.deepthink",
                instance_id="cx",
                profile_id="cx.deepthink",
            ),
        )
        if failover_ready
        else None
    )
    current_route_request = _route_request_factory(
        client_request_id=request.client_request_id,
        configuration_revision=11,
        required_capability_tier=required_capability_tier,
        candidates=current_candidates,
    )(admission.admission_snapshot)
    return _RetryCase(
        store=store,
        dispatch=dispatch,
        request=failed_request,
        attempt=failed_attempt,
        original_receipt=receipt,
        original_lease=original_lease,
        original_capability=capability,
        original_route_decision=admission.route.decision,
        route_intent=SameTargetRoute(
            route_decision_id=admission.route.decision.decision_id,
            current_route_request=current_route_request,
        ),
    )


def _authorize(
    case: _RetryCase,
    service: DispatchService | None = None,
    **overrides: object,
) -> RetryAuthorizationBundle:
    values: dict[str, object] = {
        "route_intent": case.route_intent,
        "expected_request_revision": case.request.revision,
        "expected_previous_attempt_revision": case.attempt.revision,
        "expected_highest_attempt_number": case.attempt.attempt_number,
        "frozen_max_attempts": 3,
        "current_policy_revision": case.request.policy_revision,
        "reconciliation_complete": False,
        "heartbeat_timeout_ms": 5_000,
    }
    values.update(overrides)
    target = case.dispatch if service is None else service
    return target.authorize_retry(
        case.request.command_id,
        case.attempt.attempt_id,
        **values,  # pyright: ignore[reportArgumentType]
    )


def _row_counts(store: SqliteStateStore) -> tuple[int, int]:
    with sqlite3.connect(store.database_path) as connection:
        lease_count = connection.execute(
            "SELECT COUNT(*) FROM leases"
        ).fetchone()[0]
        capability_count = connection.execute(
            "SELECT COUNT(*) FROM capability_leases"
        ).fetchone()[0]
    return int(lease_count), int(capability_count)


def _failover_row_counts(
    store: SqliteStateStore,
) -> tuple[int, int, int, int]:
    with sqlite3.connect(store.database_path) as connection:
        return tuple(
            int(
                connection.execute(
                    f"SELECT COUNT(*) FROM {table}"
                ).fetchone()[0]
            )
            for table in (
                "route_decisions",
                "route_candidate_decisions",
                "leases",
                "capability_leases",
            )
        )  # pyright: ignore[reportReturnType]


def _failover_intent(
    case: _RetryCase,
    *,
    exclude_failed: bool = True,
    exhaust_replacements: bool = False,
    replacement_candidate_id: str | None = None,
) -> FailoverRoute:
    selected_id = case.original_route_decision.selected_candidate_id
    failed_instance = next(
        candidate.instance_id
        for candidate in case.original_route_decision.candidates
        if candidate.candidate_id == selected_id
    )
    candidates = tuple(
        replace(
            candidate,
            candidate_id=(
                replacement_candidate_id
                if (
                    replacement_candidate_id is not None
                    and candidate.instance_id != failed_instance
                )
                else candidate.candidate_id
            ),
            eligible=(
                False
                if (
                    (exclude_failed and candidate.instance_id == failed_instance)
                    or (
                        exhaust_replacements
                        and candidate.instance_id != failed_instance
                    )
                )
                else candidate.eligible
            ),
            exclusion_reason=(
                FAILED_TARGET_EXCLUDED_BY_RETRY
                if exclude_failed and candidate.instance_id == failed_instance
                else (
                    "NO_REPLACEMENT_AVAILABLE"
                    if (
                        exhaust_replacements
                        and candidate.instance_id != failed_instance
                    )
                    else candidate.exclusion_reason
                )
            ),
        )
        for candidate in case.route_intent.current_route_request.candidates
    )
    return FailoverRoute(
        failed_route_decision_id=case.route_intent.route_decision_id,
        failover_route_request=replace(
            case.route_intent.current_route_request,
            candidates=candidates,
        ),
    )


@pytest.mark.parametrize(
    ("field", "value", "target_suffix"),
    (
        ("expected_request_revision", -1, None),
        ("expected_previous_attempt_revision", -1, None),
        ("expected_highest_attempt_number", 0, ":attempt-history"),
    ),
)
def test_expected_values_are_enforced_before_allocation(
    store: SqliteStateStore,
    field: str,
    value: int,
    target_suffix: str | None,
) -> None:
    case = _setup_retry_case(store)
    ids = _TaggedIds(field)
    service = DispatchService(
        store,
        clock=DeterministicClock(start=500),
        ids=ids,
    )
    before = _row_counts(store)

    with pytest.raises(StaleRevisionError) as exc_info:
        _authorize(case, service, **{field: value})

    if target_suffix is not None:
        assert exc_info.value.target_id.endswith(target_suffix)
    assert "lease" not in ids.namespaces
    assert "capability-lease" not in ids.namespaces
    assert _row_counts(store) == before


@pytest.mark.parametrize("variant", ("missing", "conflict", "exhausted"))
def test_policy_and_attempt_limit_fail_without_writes(
    store: SqliteStateStore,
    variant: str,
) -> None:
    case = _setup_retry_case(
        store,
        max_attempts=None if variant == "missing" else (1 if variant == "exhausted" else 3),
    )
    ids = _TaggedIds(variant)
    service = DispatchService(
        store,
        clock=DeterministicClock(start=500),
        ids=ids,
    )
    before = _row_counts(store)
    expected_error = {
        "missing": RecordNotFoundError,
        "conflict": RetryPolicyConflictError,
        "exhausted": AttemptLimitReachedError,
    }[variant]
    supplied_max = 4 if variant == "conflict" else (1 if variant == "exhausted" else 3)

    with pytest.raises(expected_error):
        _authorize(case, service, frozen_max_attempts=supplied_max)

    assert ids.namespaces == []
    assert _row_counts(store) == before


def test_fresh_capability_denial_is_typed_and_writes_nothing(
    store: SqliteStateStore,
) -> None:
    case = _setup_retry_case(store)
    ids = _TaggedIds("denied")
    service = DispatchService(
        store,
        clock=DeterministicClock(start=500),
        ids=ids,
        capability_policy=StaticCapabilityPolicy(
            denied_tiers=frozenset({CapabilityTier.READ_ONLY})
        ),
    )
    before = _row_counts(store)

    with pytest.raises(CapabilityAuthorizationDeniedError):
        _authorize(case, service)

    assert ids.namespaces == []
    assert _row_counts(store) == before


def test_explicit_policy_denial_is_checked_independently_of_enforcement_floor(
    store: SqliteStateStore,
) -> None:
    case = _setup_retry_case(store)
    ids = _TaggedIds("explicit-denial")
    service = DispatchService(
        store,
        clock=DeterministicClock(start=500),
        ids=ids,
        capability_policy=_DeniedDecisionWithSufficientFloorPolicy(),
    )
    before = _row_counts(store)

    with pytest.raises(CapabilityAuthorizationDeniedError):
        _authorize(case, service)

    assert ids.namespaces == []
    assert _row_counts(store) == before


def test_second_attempt_under_attempt_one_capability_is_rejected(
    store: SqliteStateStore,
) -> None:
    case = _setup_retry_case(store, fail_attempt=False)

    with pytest.raises(CapabilityLeaseViolation) as exc_info:
        case.dispatch.create_attempt(case.request.command_id, expected_authorized_attempt_number=2)

    assert exc_info.value.invariant == (
        "capability lease authorizes attempt 1, not next attempt 2"
    )
    with store.unit_of_work() as unit:
        attempts = unit.list_attempts(case.request.command_id)
    assert [attempt.attempt_number for attempt in attempts] == [1]


def test_fresh_enforcement_denial_is_typed_and_writes_nothing(
    store: SqliteStateStore,
) -> None:
    measured = StaticPeerEnforcementEvidenceProvider(
        {
            "ag": PeerEnforcementEvidence(
                peer_instance_id="ag",
                peer_kind="ag",
                enforcement_ceiling=EnforcementLevel.CONFINED,
                source_tag="controlled_fake",
            )
        }
    )
    case = _setup_retry_case(
        store,
        required_capability_tier=CapabilityTier.WORKTREE_WRITE,
        enforcement_evidence=measured,
    )
    ids = _TaggedIds("enforcement-denied")
    service = DispatchService(
        store,
        clock=DeterministicClock(start=500),
        ids=ids,
    )
    before = _row_counts(store)

    with pytest.raises(CapabilityAuthorizationDeniedError):
        _authorize(case, service)

    assert ids.namespaces == []
    assert _row_counts(store) == before


def test_policy_revision_drift_is_typed_and_writes_nothing(
    store: SqliteStateStore,
) -> None:
    case = _setup_retry_case(store)
    ids = _TaggedIds("policy-stale")
    service = DispatchService(
        store,
        clock=DeterministicClock(start=500),
        ids=ids,
    )
    before = _row_counts(store)

    with pytest.raises(PolicyStaleError):
        _authorize(case, service, current_policy_revision=999)

    assert ids.namespaces == []
    assert _row_counts(store) == before


def test_same_target_unavailability_is_typed_and_writes_nothing(
    store: SqliteStateStore,
) -> None:
    case = _setup_retry_case(store)
    candidate = case.route_intent.current_route_request.candidates[0]
    unavailable_intent = SameTargetRoute(
        route_decision_id=case.route_intent.route_decision_id,
        current_route_request=replace(
            case.route_intent.current_route_request,
            candidates=(
                replace(
                    candidate,
                    eligible=False,
                    exclusion_reason="PEER_UNAVAILABLE",
                ),
            ),
        ),
    )
    ids = _TaggedIds("route-unavailable")
    service = DispatchService(
        store,
        clock=DeterministicClock(start=500),
        ids=ids,
    )
    before = _row_counts(store)

    with pytest.raises(RetryRouteUnavailableError) as exc_info:
        _authorize(case, service, route_intent=unavailable_intent)

    assert exc_info.value.error_code is ErrorCode.PEER_UNAVAILABLE
    assert ids.namespaces == []
    assert _row_counts(store) == before


def test_freeze_retry_policy_is_idempotent_and_conflict_typed(
    store: SqliteStateStore,
) -> None:
    case = _setup_retry_case(store)

    assert case.dispatch.freeze_retry_policy(case.request.command_id, 3) == 3
    with pytest.raises(RetryPolicyConflictError):
        case.dispatch.freeze_retry_policy(case.request.command_id, 4)


@pytest.mark.parametrize(
    "fault_point",
    (
        FaultPoint.AFTER_RETRY_LEASE_WRITE,
        FaultPoint.AFTER_RETRY_CAPABILITY_WRITE,
        FaultPoint.AFTER_RETRY_PREVIOUS_ATTEMPT_CAS,
        FaultPoint.AFTER_RETRY_REQUEST_CAS,
        FaultPoint.BEFORE_COMMIT,
    ),
)
def test_retry_faults_roll_back_every_authority_write(
    store: SqliteStateStore,
    fault_point: str,
) -> None:
    case = _setup_retry_case(store)
    service = DispatchService(
        store,
        clock=DeterministicClock(start=500),
        ids=_TaggedIds(fault_point),
        fault_injector=_RaisingFaults(fault_point),
    )
    before_counts = _row_counts(store)

    with pytest.raises(RuntimeError, match="injected fault"):
        _authorize(case, service, reconciliation_complete=True)

    assert _row_counts(store) == before_counts
    with store.unit_of_work() as unit:
        assert unit.get_request(case.request.command_id) == case.request
        assert unit.get_attempt(case.attempt.attempt_id) == case.attempt


def test_same_target_retry_preserves_route_target_and_digest(
    store: SqliteStateStore,
) -> None:
    case = _setup_retry_case(store)

    bundle = _authorize(case)

    assert (
        bundle.request.configuration_revision,
        bundle.request.selected_peer_instance_id,
        bundle.request.selected_profile_id,
        bundle.request.route_decision_digest,
    ) == (
        case.request.configuration_revision,
        case.request.selected_peer_instance_id,
        case.request.selected_profile_id,
        case.request.route_decision_digest,
    )


@pytest.mark.parametrize("failover", (False, True), ids=("same-target", "failover"))
def test_retry_preserves_full_fence_owner_identity(
    store: SqliteStateStore,
    failover: bool,
) -> None:
    case = _setup_retry_case(store, failover_ready=failover)
    original_owner = (
        case.original_lease.fence.owner_principal_id,
        case.original_lease.fence.owner_instance_id,
        case.original_lease.fence.authority_epoch,
        case.original_lease.fence.owner_peer_id,
    )
    assert case.original_lease.fence.owner_instance_id == "cli-instance"

    bundle = _authorize(
        case,
        route_intent=_failover_intent(case) if failover else case.route_intent,
    )

    with store.unit_of_work() as unit:
        persisted_lease = unit.get_lease(bundle.session_lease.lease_id)
    assert persisted_lease is not None
    assert (
        persisted_lease.fence.owner_principal_id,
        persisted_lease.fence.owner_instance_id,
        persisted_lease.fence.authority_epoch,
        persisted_lease.fence.owner_peer_id,
    ) == original_owner


def test_retry_keeps_original_receipt_and_capability_immutable(
    store: SqliteStateStore,
) -> None:
    case = _setup_retry_case(store)
    original_capability = case.original_capability
    original_receipt = case.original_receipt

    _authorize(case)

    with store.unit_of_work() as unit:
        assert unit.get_admission_receipt(original_receipt.admission_receipt_id) == original_receipt
        assert unit.get_capability_lease(original_capability.capability_lease_id) == original_capability


def test_new_capability_binds_new_lease_and_next_attempt_number(
    store: SqliteStateStore,
) -> None:
    case = _setup_retry_case(store)

    bundle = _authorize(case)

    assert bundle.request.lease_id == bundle.session_lease.lease_id
    assert bundle.capability_lease.session_lease_id == bundle.request.lease_id
    assert bundle.capability_lease.authorized_attempt_number == case.attempt.attempt_number + 1
    assert bundle.capability_lease.previous_attempt_id == case.attempt.attempt_id


def test_dispatch_gate_accepts_new_and_rejects_old_capability(
    store: SqliteStateStore,
) -> None:
    case = _setup_retry_case(store)
    bundle = _authorize(case)
    profile = ProfileDescriptor(
        profile_id=bundle.request.selected_profile_id,
        profile_class="test",
        supports_reasoning_effort=True,
    )

    validated = case.dispatch.require_dispatch_capability(
        bundle.request.command_id,
        capability_lease_id=bundle.capability_lease.capability_lease_id,
        peer_instance_id=bundle.request.selected_peer_instance_id,
        adapter_peer_kind="ag",
        profile=profile,
        current_policy_revision=bundle.request.policy_revision,
    )
    assert validated.authorized_attempt_number == 2
    with pytest.raises(CapabilityLeaseViolation):
        case.dispatch.require_dispatch_capability(
            bundle.request.command_id,
            capability_lease_id=case.original_capability.capability_lease_id,
            peer_instance_id=bundle.request.selected_peer_instance_id,
            adapter_peer_kind="ag",
            profile=profile,
            current_policy_revision=bundle.request.policy_revision,
        )


def test_create_attempt_consumes_exactly_next_authority(
    store: SqliteStateStore,
) -> None:
    case = _setup_retry_case(store)
    bundle = _authorize(case)

    next_attempt = case.dispatch.create_attempt(bundle.request.command_id, expected_authorized_attempt_number=2)

    assert next_attempt.attempt_number == case.attempt.attempt_number + 1
    assert next_attempt.lease_id == bundle.session_lease.lease_id
    with store.unit_of_work() as unit:
        assert [item.attempt_number for item in unit.list_attempts(bundle.request.command_id)] == [1, 2]


def test_two_sqlite_callers_have_one_bundle_and_one_typed_stale_result(
    store: SqliteStateStore,
) -> None:
    case = _setup_retry_case(store)
    before = _row_counts(store)
    barrier = threading.Barrier(2)

    def call(tag: str) -> RetryAuthorizationBundle | StaleRevisionError:
        service = DispatchService(
            store,
            clock=DeterministicClock(start=500),
            ids=_TaggedIds(tag),
        )
        barrier.wait()
        try:
            return _authorize(case, service)
        except StaleRevisionError as error:
            return error

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = tuple(executor.map(call, ("one", "two")))

    assert sum(isinstance(item, RetryAuthorizationBundle) for item in results) == 1
    assert sum(isinstance(item, StaleRevisionError) for item in results) == 1
    after = _row_counts(store)
    assert after == (before[0] + 1, before[1] + 1)


def test_failover_audit_keeps_failed_instance_explicitly_excluded(
    store: SqliteStateStore,
) -> None:
    case = _setup_retry_case(store, failover_ready=True)

    bundle = _authorize(case, route_intent=_failover_intent(case))

    failed_audits = tuple(
        candidate
        for candidate in bundle.route_decision.candidates
        if candidate.instance_id == case.request.selected_peer_instance_id
    )
    assert failed_audits
    assert all(
        candidate.eligibility is RouteEligibility.EXCLUDED
        and candidate.exclusion_reason == FAILED_TARGET_EXCLUDED_BY_RETRY
        for candidate in failed_audits
    )


def test_failover_selects_an_instance_other_than_the_failed_instance(
    store: SqliteStateStore,
) -> None:
    case = _setup_retry_case(store, failover_ready=True)

    bundle = _authorize(case, route_intent=_failover_intent(case))

    assert bundle.request.selected_peer_instance_id == "cx"
    assert (
        bundle.request.selected_peer_instance_id
        != case.request.selected_peer_instance_id
    )
    assert bundle.request.selected_profile_id == "cx.deepthink"


@pytest.mark.parametrize("failure", ("route-exhausted", "capability-denied"))
def test_failover_exhaustion_and_denial_write_nothing(
    store: SqliteStateStore,
    failure: str,
) -> None:
    case = _setup_retry_case(store, failover_ready=True)
    ids = _TaggedIds(failure)
    service = DispatchService(
        store,
        clock=DeterministicClock(start=500),
        ids=ids,
        capability_policy=(
            StaticCapabilityPolicy(
                denied_tiers=frozenset({CapabilityTier.READ_ONLY})
            )
            if failure == "capability-denied"
            else None
        ),
    )
    before = _failover_row_counts(store)
    expected_error = (
        RouteExhaustedError
        if failure == "route-exhausted"
        else CapabilityAuthorizationDeniedError
    )

    with pytest.raises(expected_error):
        _authorize(
            case,
            service,
            route_intent=_failover_intent(
                case,
                exhaust_replacements=(failure == "route-exhausted"),
            ),
        )

    assert _failover_row_counts(store) == before
    assert "lease" not in ids.namespaces
    assert "capability-lease" not in ids.namespaces


def test_failover_route_request_lease_and_capability_commit_together(
    store: SqliteStateStore,
) -> None:
    case = _setup_retry_case(store, failover_ready=True)
    before = _failover_row_counts(store)

    bundle = _authorize(case, route_intent=_failover_intent(case))

    assert _failover_row_counts(store) == (
        before[0] + 1,
        before[1] + 2,
        before[2] + 1,
        before[3] + 1,
    )
    with store.unit_of_work() as unit:
        assert (
            unit.get_route_decision(bundle.route_decision.decision_id)
            == bundle.route_decision
        )
        assert unit.get_request(bundle.request.command_id) == bundle.request
        assert unit.get_lease(bundle.session_lease.lease_id) == bundle.session_lease
        assert (
            unit.get_capability_lease(
                bundle.capability_lease.capability_lease_id
            )
            == bundle.capability_lease
        )
    assert (
        bundle.request.route_decision_digest
        == bundle.capability_lease.route_decision_digest
    )


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("selected_peer_instance_id", "tampered-instance"),
        ("selected_profile_id", "tampered-profile"),
        ("route_decision_digest", "f" * 64),
    ),
)
def test_failover_rejects_current_route_binding_tampering(
    store: SqliteStateStore,
    field: str,
    value: str,
) -> None:
    case = _setup_retry_case(store, failover_ready=True)
    tampered = replace(
        case.request,
        **{field: value},
        revision=case.request.revision + 1,
    )
    with store.unit_of_work() as unit:
        assert unit.cas_update_request(case.request, tampered)
        unit.commit()
    case = replace(case, request=tampered)
    before = _failover_row_counts(store)

    with pytest.raises((CapabilityLeaseViolation, InvalidMutationError)):
        _authorize(case, route_intent=_failover_intent(case))

    assert _failover_row_counts(store) == before


@pytest.mark.parametrize(
    "fault_point",
    (
        FaultPoint.AFTER_RETRY_ROUTE_WRITE,
        FaultPoint.AFTER_RETRY_LEASE_WRITE,
        FaultPoint.AFTER_RETRY_CAPABILITY_WRITE,
        FaultPoint.AFTER_RETRY_PREVIOUS_ATTEMPT_CAS,
        FaultPoint.AFTER_RETRY_REQUEST_CAS,
        FaultPoint.BEFORE_COMMIT,
    ),
)
def test_failover_faults_roll_back_route_and_authority_writes(
    store: SqliteStateStore,
    fault_point: str,
) -> None:
    case = _setup_retry_case(
        store,
        failover_ready=True,
        start_uncertain=True,
    )
    ids = _TaggedIds(fault_point)
    service = DispatchService(
        store,
        clock=DeterministicClock(start=500),
        ids=ids,
        fault_injector=_RaisingFaults(fault_point),
    )
    before = _failover_row_counts(store)

    with pytest.raises(RuntimeError, match="injected fault"):
        _authorize(
            case,
            service,
            route_intent=_failover_intent(case),
            reconciliation_complete=True,
        )

    assert _failover_row_counts(store) == before
    with store.unit_of_work() as unit:
        assert unit.get_request(case.request.command_id) == case.request
        assert unit.get_attempt(case.attempt.attempt_id) == case.attempt
        route_ids = tuple(
            namespace for namespace in ids.namespaces
            if namespace == "route-decision"
        )
        assert route_ids == ("route-decision",)
        assert (
            unit.get_route_decision(
                f"route-decision-{fault_point}-1"
            )
            is None
        )


def test_failover_preserves_all_original_authority_and_route_records(
    store: SqliteStateStore,
) -> None:
    case = _setup_retry_case(store, failover_ready=True)

    _authorize(case, route_intent=_failover_intent(case))

    with store.unit_of_work() as unit:
        assert (
            unit.get_route_decision(case.original_route_decision.decision_id)
            == case.original_route_decision
        )
        assert (
            unit.get_admission_receipt(
                case.original_receipt.admission_receipt_id
            )
            == case.original_receipt
        )
        assert (
            unit.get_capability_lease(
                case.original_capability.capability_lease_id
            )
            == case.original_capability
        )
        assert unit.get_lease(case.original_lease.lease_id) == case.original_lease
        assert unit.list_attempts(case.request.command_id) == (case.attempt,)


def test_failover_dispatch_gate_accepts_new_and_rejects_old_authority(
    store: SqliteStateStore,
) -> None:
    case = _setup_retry_case(store, failover_ready=True)
    bundle = _authorize(case, route_intent=_failover_intent(case))
    replacement_profile = ProfileDescriptor(
        profile_id=bundle.request.selected_profile_id,
        profile_class="test",
        supports_reasoning_effort=True,
    )

    validated = case.dispatch.require_dispatch_capability(
        bundle.request.command_id,
        capability_lease_id=bundle.capability_lease.capability_lease_id,
        peer_instance_id=bundle.request.selected_peer_instance_id,
        adapter_peer_kind="cx",
        profile=replacement_profile,
        current_policy_revision=bundle.request.policy_revision,
    )
    assert validated.capability_lease_id == (
        bundle.capability_lease.capability_lease_id
    )
    with pytest.raises(CapabilityLeaseViolation):
        case.dispatch.require_dispatch_capability(
            bundle.request.command_id,
            capability_lease_id=case.original_capability.capability_lease_id,
            peer_instance_id=case.request.selected_peer_instance_id,
            adapter_peer_kind="ag",
            profile=ProfileDescriptor(
                profile_id=case.request.selected_profile_id,
                profile_class="test",
                supports_reasoning_effort=True,
            ),
            current_policy_revision=bundle.request.policy_revision,
        )


def test_failover_next_attempt_uses_replacement_request_lease(
    store: SqliteStateStore,
) -> None:
    case = _setup_retry_case(store, failover_ready=True)
    bundle = _authorize(case, route_intent=_failover_intent(case))

    next_attempt = case.dispatch.create_attempt(bundle.request.command_id, expected_authorized_attempt_number=2)

    assert next_attempt.attempt_number == case.attempt.attempt_number + 1
    assert next_attempt.lease_id == bundle.request.lease_id
    assert next_attempt.lease_id == bundle.session_lease.lease_id


def test_two_sqlite_failover_callers_have_exactly_one_winner(
    store: SqliteStateStore,
) -> None:
    case = _setup_retry_case(store, failover_ready=True)
    before = _failover_row_counts(store)
    barrier = threading.Barrier(2)

    def call(tag: str) -> RetryAuthorizationBundle | StaleRevisionError:
        independently_built_intent = _failover_intent(case)
        service = DispatchService(
            store,
            clock=DeterministicClock(start=500),
            ids=_TaggedIds(tag),
        )
        barrier.wait()
        try:
            return _authorize(
                case,
                service,
                route_intent=independently_built_intent,
            )
        except StaleRevisionError as error:
            return error

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = tuple(executor.map(call, ("one", "two")))

    assert sum(
        isinstance(item, RetryAuthorizationBundle) for item in results
    ) == 1
    assert sum(isinstance(item, StaleRevisionError) for item in results) == 1
    after = _failover_row_counts(store)
    assert after == (
        before[0] + 1,
        before[1] + 2,
        before[2] + 1,
        before[3] + 1,
    )


def test_failed_instance_exclusion_guard_is_independent_of_selection_guard(
    store: SqliteStateStore,
) -> None:
    case = _setup_retry_case(store, failover_ready=True)
    request = case.route_intent.current_route_request
    default_selection = select_equal_weight_candidate(
        client_request_id=request.client_request_id,
        snapshot_digest=request.admission_snapshot.digest,
        candidate_ids=("ag.deepthink", "cx.deepthink"),
    )
    replacement_id = (
        "cx.deepthink"
        if default_selection.selected_candidate == "cx.deepthink"
        else "0-cx.deepthink"
    )
    unexcluded = _failover_intent(
        case,
        exclude_failed=False,
        replacement_candidate_id=replacement_id,
    )
    prospective = select_route(
        unexcluded.failover_route_request,
        decision_id="route-decision-mutation-probe",
        created_at=500,
    ).decision
    selected = next(
        candidate
        for candidate in prospective.candidates
        if candidate.candidate_id == prospective.selected_candidate_id
    )
    assert selected.instance_id == "cx"

    with pytest.raises(
        InvalidMutationError,
        match="failed route instance candidates must remain explicitly excluded",
    ):
        _authorize(case, route_intent=unexcluded)
