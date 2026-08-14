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
    PolicyStaleError,
    RecordNotFoundError,
    RetryPolicyConflictError,
    RetryRouteUnavailableError,
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
    RequestSnapshot,
)
from peerhub.dispatch.retry_authorization import (
    RetryAuthorizationBundle,
    SameTargetRoute,
)
from peerhub.dispatch.service import DispatchService
from peerhub.dispatch.unit_of_work import FaultInjector, FaultPoint
from peerhub.persistence.sqlite import SqliteStateStore
from tests.fakes import DeterministicClock, SequentialIdSource
from tests.integration.application.test_workflows_kernel import (
    _admission_kwargs,
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
    original_capability: CapabilityLease
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
) -> _RetryCase:
    _seed_health(store)
    dispatch_ids = SequentialIdSource()
    workflows = _workflows(
        store,
        dispatch_ids=dispatch_ids,
        enforcement_evidence=enforcement_evidence,
    )
    admission_kwargs = _admission_kwargs()
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
    request, receipt, _, capability = admission.dispatch_admission
    dispatch = workflows._dispatch  # pyright: ignore[reportPrivateUsage]
    if max_attempts is not None:
        dispatch.freeze_retry_policy(request.command_id, max_attempts)
    prepared = dispatch.prepare_request(request.command_id)
    attempt = dispatch.create_attempt(request.command_id)
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
    current_route_request = _route_request_factory(
        client_request_id=request.client_request_id,
        configuration_revision=11,
        required_capability_tier=required_capability_tier,
    )(admission.admission_snapshot)
    return _RetryCase(
        store=store,
        dispatch=dispatch,
        request=failed_request,
        attempt=failed_attempt,
        original_receipt=receipt,
        original_capability=capability,
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
        case.dispatch.create_attempt(case.request.command_id)

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

    next_attempt = case.dispatch.create_attempt(bundle.request.command_id)

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
