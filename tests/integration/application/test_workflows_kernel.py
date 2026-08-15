"""Integration tests for peerhub.application.workflows."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace

import pytest

from peerhub.application.api import ApplicationAPI
from peerhub.application.workflows import ApplicationWorkflows
from peerhub.core.errors import InvalidMutationError, RetryRouteUnavailableError
from peerhub.core.evidence import EvidenceRef, EvidenceState, EvidenceValue
from peerhub.core.identity import AuthenticatedSubject
from peerhub.core.protocol import (
    PROTOCOL_MAJOR,
    PROTOCOL_MINOR,
    SCHEMA_VERSION,
    CommandEnvelope,
    ErrorCode,
)
from peerhub.dispatch.contract import (
    CompletionContract,
    CompletionContractKind,
    RequestState,
)
from peerhub.core.ports import RequestContext
from peerhub.dispatch.capability import (
    CapabilityTier,
    EnforcementLevel,
    PeerEnforcementEvidence,
    PeerEnforcementEvidenceProvider,
)
from peerhub.dispatch.capability_policy import (
    StaticPeerEnforcementEvidenceProvider,
)
from peerhub.dispatch.service import DispatchService
from peerhub.dispatch.retry_authorization import (
    FAILED_TARGET_EXCLUDED_BY_RETRY,
    FailoverRoute,
    SameTargetRoute,
)
from peerhub.health.contract import (
    AdmissionSnapshot,
    HealthPolicy,
    HealthScopeMembershipSnapshot,
)
from peerhub.health.service import HealthService
from peerhub.persistence.sqlite import SqliteStateStore
from peerhub.routing.contract import (
    ConfigurationSnapshot,
    RouteCandidateInput,
    RouteRequest,
)
from peerhub.routing.service import RoutingService
from peerhub.telemetry.contract import ReadinessMeasurement, ReadinessObserved
from peerhub.telemetry.projections import TelemetryProjector
from tests.fakes import DeterministicClock, SequentialIdSource


@pytest.fixture
def store(tmp_path: Path) -> Iterator[SqliteStateStore]:
    state_store = SqliteStateStore(
        tmp_path / "workflows.sqlite3",
        workspace_home_id="workspace-workflows",
    )
    state_store.initialize()
    try:
        yield state_store
    finally:
        state_store.close()


def _policy() -> HealthPolicy:
    return HealthPolicy(
        policy_id="v1-health-default-r1",
        revision=1,
        readiness_freshness_seconds=7200,
        recovery_backoff_seconds=(30, 60, 120, 240, 480, 900),
        recovery_jitter_fraction=0.2,
        readiness_observation_threshold=1,
        administrative_recovery_probe_limit=1,
    )


def _membership(
    *,
    configuration_revision: int = 11,
    configured_members: tuple[tuple[str, str], ...] = (
        ("ag", "ag.deepthink"),
    ),
) -> HealthScopeMembershipSnapshot:
    return HealthScopeMembershipSnapshot(
        configuration_revision=configuration_revision,
        configuration_digest="e" * 64,
        configured_members=configured_members,
        bindings=(),
    )


def _readiness(
    observation_id: str,
    *,
    observed_at: int = 100,
    instance_id: str = "ag",
    profile_id: str = "ag.deepthink",
) -> ReadinessObserved:
    return ReadinessObserved(
        observation_id=observation_id,
        instance_id=instance_id,
        profile_id=profile_id,
        evidence=EvidenceValue(
            state=EvidenceState.MEASURED,
            source_tag="empirical_probe",
            provider_id="phase0-readiness",
            provider_version="1",
            observed_at=observed_at,
            captured_at=observed_at,
            freshness_ttl=7200,
            evidence_ref=EvidenceRef(f"sha256:{observation_id}"),
            value=ReadinessMeasurement(
                runtime_revision="runtime-r17",
                issued_at=1,
                valid_until=10_000,
                integrity_verified=True,
            ),
        ),
    )


def _absent_usage() -> EvidenceValue:
    return EvidenceValue(
        state=EvidenceState.ABSENT,
        source_tag="empirical_probe",
        provider_id="phase0-usage",
        provider_version="1",
        observed_at=None,
        captured_at=100,
        freshness_ttl=7200,
        evidence_ref=EvidenceRef("sha256:usage-absent"),
        value=None,
    )


def _candidate(
    eligible: bool = True,
    *,
    candidate_id: str = "ag.deepthink",
    instance_id: str = "ag",
    profile_id: str = "ag.deepthink",
) -> RouteCandidateInput:
    return RouteCandidateInput(
        candidate_id=candidate_id,
        instance_id=instance_id,
        representative_profile_id=profile_id,
        eligible=eligible,
        exclusion_reason=(
            None if eligible else "ROLE_EXCLUDED"
        ),
        usage_evidence=_absent_usage(),
        in_flight_reservations=0,
        evidence_refs=(),
    )


def _route_request_factory(
    *,
    client_request_id: str,
    configuration_revision: int,
    required_capability_tier: CapabilityTier = CapabilityTier.READ_ONLY,
    eligible: bool = True,
    candidates: tuple[RouteCandidateInput, ...] | None = None,
):
    def factory(
        admission_snapshot: AdmissionSnapshot,
    ) -> RouteRequest:
        return RouteRequest(
            client_request_id=client_request_id,
            configuration=ConfigurationSnapshot(
                revision=admission_snapshot.configuration_revision,
                digest=admission_snapshot.configuration_digest,
            ),
            admission_snapshot=admission_snapshot,
            required_capability_tier=required_capability_tier,
            requested_capabilities=(),
            profile_constraints={},
            required_readiness_binding=None,
            candidates=(
                (_candidate(eligible=eligible),)
                if candidates is None
                else candidates
            ),
            routing_policy_id="v1-routing-default-r1",
            routing_policy_revision=1,
        )

    return factory


def _envelope(
    *,
    client_request_id: str = "client-request-01",
    idempotency_key: str = "idempotency-01",
) -> CommandEnvelope:
    return CommandEnvelope(
        protocol_major=PROTOCOL_MAJOR,
        protocol_minor=PROTOCOL_MINOR,
        schema_version=SCHEMA_VERSION,
        client_request_id=client_request_id,
        correlation_id="correlation-01",
        client_id="client-01",
        actor_id="actor-01",
        scope={
            "workspace_id": "workspace-01",
            "home_id": "home-01",
        },
        method="peer.ask",
        params={"prompt": "hello"},
        idempotency_key=idempotency_key,
        expected_policy_revision=7,
        expected_configuration_revision=11,
        client_timestamp=10,
    )


def _completion_contract() -> CompletionContract:
    return CompletionContract(
        contract_id="completion-contract-01",
        kind=CompletionContractKind.DELIVERY_ONLY,
        requirements=(),
        replay_safe=False,
    )


def _workflows(
    store: SqliteStateStore,
    *,
    start: int = 200,
    configuration_revision: int = 11,
    health_ids: SequentialIdSource | None = None,
    routing_ids: SequentialIdSource | None = None,
    dispatch_ids: SequentialIdSource | None = None,
    enforcement_evidence: PeerEnforcementEvidenceProvider | None = None,
    configured_members: tuple[tuple[str, str], ...] = (
        ("ag", "ag.deepthink"),
    ),
) -> ApplicationWorkflows:
    telemetry = TelemetryProjector(
        store,
        ids=SequentialIdSource(),
        freshness_ttl=3600,
    )
    health = HealthService(
        store,
        telemetry=telemetry,
        policy=_policy(),
        membership=_membership(
            configuration_revision=configuration_revision,
            configured_members=configured_members,
        ),
        clock=DeterministicClock(start=start),
        ids=health_ids if health_ids is not None else SequentialIdSource(),
    )
    routing = RoutingService(
        store,
        clock=DeterministicClock(start=start),
        ids=routing_ids if routing_ids is not None else SequentialIdSource(),
    )
    dispatch = DispatchService(
        store,
        clock=DeterministicClock(start=start),
        ids=dispatch_ids if dispatch_ids is not None else SequentialIdSource(),
        enforcement_evidence=enforcement_evidence,
    )
    return ApplicationWorkflows(
        telemetry=telemetry,
        health=health,
        routing=routing,
        dispatch=dispatch,
    )


def _seed_health(
    store: SqliteStateStore,
    *,
    configured_members: tuple[tuple[str, str], ...] = (
        ("ag", "ag.deepthink"),
    ),
) -> None:
    with store.unit_of_work() as unit:
        unit.add_health_policy_revision(_policy())
        unit.commit()

    telemetry = TelemetryProjector(
        store,
        ids=SequentialIdSource(),
        freshness_ttl=3600,
    )
    health = HealthService(
        store,
        telemetry=telemetry,
        policy=_policy(),
        membership=_membership(configured_members=configured_members),
        clock=DeterministicClock(start=100),
        ids=SequentialIdSource(),
    )
    for index, (instance_id, profile_id) in enumerate(
        configured_members,
        start=1,
    ):
        health.evaluate_and_persist_readiness(
            _readiness(
                f"readiness-{index:02d}",
                instance_id=instance_id,
                profile_id=profile_id,
            ),
            sealed_runtime_revision="runtime-r17",
            adapter_declares_probe_safe=True,
        )


def _admission_kwargs() -> dict:
    return dict(
        required_capability_tier=CapabilityTier.READ_ONLY,
        authenticated_subject=AuthenticatedSubject(
            "principal-01",
            "test",
        ),
        completion_contract=_completion_contract(),
        dispatch_policy_revision=7,
        session_id="session-01",
        owner_principal_id="principal-01",
        owner_instance_id="ag",
        authority_epoch=3,
        heartbeat_timeout_ms=5_000,
        owner_peer_id="peer-01",
    )


def test_admit_request_projects_freezes_routes_and_admits(
    store: SqliteStateStore,
) -> None:
    _seed_health(store)
    workflows = _workflows(store)

    result = workflows.admit_request(
        _envelope(),
        route_request_factory=_route_request_factory(
            client_request_id="client-request-01",
            configuration_revision=11,
        ),
        **_admission_kwargs(),
    )

    assert result.dispatch_admission is not None
    assert result.route.error_code is None
    assert (
        result.route.decision.selected_candidate_id
        == "ag.deepthink"
    )
    assert len(result.admission_snapshot.entries) == 1

    request, _, _, _ = result.dispatch_admission
    assert request.selected_peer_instance_id == "ag"
    assert request.selected_profile_id == "ag.deepthink"
    assert request.state is RequestState.ADMITTED


def test_application_api_persists_required_capability_tier(
    store: SqliteStateStore,
) -> None:
    _seed_health(store)
    # This test admits a mutating tier, which increment 4 denies unless
    # machine-owned evidence proves the mandatory enforcement floor.  The
    # fixture's target is a controlled fake with a declared CONFINED ceiling;
    # it deliberately does NOT claim that the real "ag" peer is confined
    # (DIR-002 records the opposite), which is why the peer kind here is
    # "fake" rather than "ag".
    workflows = _workflows(
        store,
        enforcement_evidence=StaticPeerEnforcementEvidenceProvider(
            {
                "ag": PeerEnforcementEvidence(
                    peer_instance_id="ag",
                    peer_kind="fake",
                    enforcement_ceiling=EnforcementLevel.CONFINED,
                    source_tag="controlled_fake",
                )
            }
        ),
    )

    class AdmissionProvider:
        def resolve(self, command, caller):
            return SimpleNamespace(
                route_request_factory=_route_request_factory(
                    client_request_id=(
                        command.submission.client_request_id
                    ),
                    configuration_revision=11,
                    required_capability_tier=(
                        command.required_capability_tier
                    ),
                ),
                dispatch_policy_revision=7,
                session_id="session-api-capability",
                owner_principal_id=caller.principal,
                owner_instance_id="ag",
                authority_epoch=3,
                heartbeat_timeout_ms=5_000,
                owner_peer_id="peer-01",
            )

    api = ApplicationAPI(
        workflows=workflows,
        dispatch=workflows._dispatch,  # pyright: ignore[reportPrivateUsage]
        admission_provider=AdmissionProvider(),
    )
    envelope = CommandEnvelope(
        protocol_major=PROTOCOL_MAJOR,
        protocol_minor=PROTOCOL_MINOR,
        schema_version=SCHEMA_VERSION,
        client_request_id="client-request-api-capability",
        correlation_id="correlation-api-capability",
        client_id="client-api-capability",
        actor_id="actor-api-capability",
        scope={"workspace_id": "workspace-01"},
        method="dispatch.admit",
        params={
            "prompt": "write through the API",
            "required_capability_tier": "GIT_MUTATE",
            "requested_capabilities": [],
            "profile_constraints": {},
            "completion_contract": {
                "kind": "DELIVERY_ONLY",
                "requirements": [],
                "replay_safe": False,
            },
            "session_policy": {},
        },
        idempotency_key="idempotency-api-capability",
        expected_policy_revision=7,
        expected_configuration_revision=11,
        client_timestamp=10,
    )

    outcome = api.submit(
        envelope,
        caller=RequestContext(
            principal="principal-api-capability",
            client_id="client-api-capability",
        ),
    )

    assert outcome.ok
    assert outcome.command_id is not None
    stored = workflows._dispatch.get_request(  # pyright: ignore[reportPrivateUsage]
        outcome.command_id
    )
    assert stored is not None
    assert (
        stored.required_capability_tier
        is CapabilityTier.GIT_MUTATE
    )


def test_admit_request_is_idempotent_on_retry(
    store: SqliteStateStore,
) -> None:
    _seed_health(store)
    workflows = _workflows(store)
    factory = _route_request_factory(
        client_request_id="client-request-01",
        configuration_revision=11,
    )

    first = workflows.admit_request(
        _envelope(),
        route_request_factory=factory,
        **_admission_kwargs(),
    )

    # A retry of the identical envelope must not crash: canonical_route_
    # decision_digest embeds a freshly-minted decision_id every call, so
    # it can never match dispatch's already-recorded digest on replay --
    # the workflow must recognize this as an idempotent replay, not a
    # binding inconsistency.
    second = workflows.admit_request(
        _envelope(),
        route_request_factory=factory,
        **_admission_kwargs(),
    )

    assert second.admission_snapshot is None
    assert second.route is None
    assert second.dispatch_admission is not None
    assert (
        second.dispatch_admission[0].command_id
        == first.dispatch_admission[0].command_id
    )
    assert (
        second.dispatch_admission[0].route_decision_digest
        == first.dispatch_admission[0].route_decision_digest
    )


def test_admit_request_returns_route_exhausted_without_admitting(
    store: SqliteStateStore,
) -> None:
    _seed_health(store)
    workflows = _workflows(store)

    result = workflows.admit_request(
        _envelope(),
        route_request_factory=_route_request_factory(
            client_request_id="client-request-01",
            configuration_revision=11,
            eligible=False,
        ),
        **_admission_kwargs(),
    )

    assert result.route.error_code is ErrorCode.ROUTE_EXHAUSTED
    assert result.dispatch_admission is None


def test_prepare_for_dispatch_permits_with_no_drift(
    store: SqliteStateStore,
) -> None:
    _seed_health(store)
    workflows = _workflows(store)
    admission = workflows.admit_request(
        _envelope(),
        route_request_factory=_route_request_factory(
            client_request_id="client-request-01",
            configuration_revision=11,
        ),
        **_admission_kwargs(),
    )
    request, _, _, _ = admission.dispatch_admission

    outcome = workflows.prepare_for_dispatch(
        request.command_id,
        route_decision_id=admission.route.decision.decision_id,
        route_request_factory=_route_request_factory(
            client_request_id="client-request-01",
            configuration_revision=11,
        ),
    )

    assert outcome.route_recheck.validation.dispatch_permitted
    assert outcome.request.state is RequestState.PREPARED


def test_prepare_for_dispatch_rejects_and_replans_on_drift(
    store: SqliteStateStore,
) -> None:
    _seed_health(store)
    health_ids = SequentialIdSource()
    routing_ids = SequentialIdSource()
    dispatch_ids = SequentialIdSource()
    workflows = _workflows(
        store,
        health_ids=health_ids,
        routing_ids=routing_ids,
        dispatch_ids=dispatch_ids,
    )
    admission = workflows.admit_request(
        _envelope(),
        route_request_factory=_route_request_factory(
            client_request_id="client-request-01",
            configuration_revision=11,
        ),
        **_admission_kwargs(),
    )
    request, _, _, _ = admission.dispatch_admission

    drifted_workflows = _workflows(
        store,
        start=1_000,
        configuration_revision=12,
        health_ids=health_ids,
        routing_ids=routing_ids,
        dispatch_ids=dispatch_ids,
    )
    outcome = drifted_workflows.prepare_for_dispatch(
        request.command_id,
        route_decision_id=admission.route.decision.decision_id,
        route_request_factory=_route_request_factory(
            client_request_id="client-request-01",
            configuration_revision=12,
        ),
    )

    assert not outcome.route_recheck.validation.dispatch_permitted
    assert outcome.request.state is RequestState.REJECTED_POLICY
    assert outcome.route_recheck.replanned_route is not None
    assert (
        outcome.route_recheck.replanned_route.decision
        .configuration.revision
        == 12
    )
    assert (
        outcome.route_recheck.replanned_route.decision.decision_id
        != admission.route.decision.decision_id
    )


def test_authorize_retry_rechecks_route_before_authorizing(
    store: SqliteStateStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed_health(store)
    workflows = _workflows(store)
    admission = workflows.admit_request(
        _envelope(),
        route_request_factory=_route_request_factory(
            client_request_id="client-request-01",
            configuration_revision=11,
        ),
        **_admission_kwargs(),
    )
    request, _, _, _ = admission.dispatch_admission
    dispatch = workflows._dispatch  # noqa: SLF001 - direct service access for test setup only

    prepared = dispatch.prepare_request(request.command_id)
    attempt = dispatch.create_attempt(prepared.command_id, expected_authorized_attempt_number=1)
    failed_request, failed_attempt = dispatch.fail_pre_dispatch(
        prepared.command_id,
        attempt.attempt_id,
        error_code=ErrorCode.SPAWN_FAILED,
        transport="pipe",
    )
    dispatch.freeze_retry_policy(request.command_id, 3)
    monkeypatch.setattr(
        workflows._routing,  # pyright: ignore[reportPrivateUsage]
        "validate_route_for_dispatch",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("retry must not open a routing-service transaction")
        ),
    )

    outcome = workflows.authorize_retry(
        request.command_id,
        attempt.attempt_id,
        route_intent=SameTargetRoute(
            route_decision_id=admission.route.decision.decision_id,
            current_route_request=_route_request_factory(
                client_request_id="client-request-01",
                configuration_revision=11,
            )(admission.admission_snapshot),
        ),
        route_request_factory=_route_request_factory(
            client_request_id="client-request-01",
            configuration_revision=11,
        ),
        expected_request_revision=failed_request.revision,
        expected_previous_attempt_revision=failed_attempt.revision,
        expected_highest_attempt_number=failed_attempt.attempt_number,
        frozen_max_attempts=3,
        current_policy_revision=failed_request.policy_revision,
        reconciliation_complete=False,
        heartbeat_timeout_ms=5_000,
    )

    assert outcome.retry_admission.request.state is RequestState.PREPARED
    assert outcome.retry_admission.capability_lease.authorized_attempt_number == 2


def test_authorize_retry_prepares_failover_exclusion_and_atomic_rebind(
    store: SqliteStateStore,
) -> None:
    members = (("ag", "ag.deepthink"), ("cx", "cx.deepthink"))
    _seed_health(store, configured_members=members)
    shared_ids = SequentialIdSource()
    health_ids = SequentialIdSource()
    workflows = _workflows(
        store,
        health_ids=health_ids,
        routing_ids=shared_ids,
        dispatch_ids=shared_ids,
        configured_members=members,
    )
    failed_candidate = _candidate()
    replacement_candidate = _candidate(
        candidate_id="cx.deepthink",
        instance_id="cx",
        profile_id="cx.deepthink",
    )
    admission_kwargs = _admission_kwargs()
    admission_kwargs["owner_instance_id"] = "cli-instance"
    admission = workflows.admit_request(
        _envelope(),
        route_request_factory=_route_request_factory(
            client_request_id="client-request-01",
            configuration_revision=11,
            candidates=(failed_candidate,),
        ),
        **admission_kwargs,
    )
    assert admission.dispatch_admission is not None
    assert admission.admission_snapshot is not None
    assert admission.route is not None
    request, _, _, _ = admission.dispatch_admission
    dispatch = workflows._dispatch  # pyright: ignore[reportPrivateUsage]
    prepared = dispatch.prepare_request(request.command_id)
    attempt = dispatch.create_attempt(prepared.command_id, expected_authorized_attempt_number=1)
    failed_request, failed_attempt = dispatch.fail_pre_dispatch(
        prepared.command_id,
        attempt.attempt_id,
        error_code=ErrorCode.SPAWN_FAILED,
        transport="pipe",
    )
    dispatch.freeze_retry_policy(request.command_id, 3)
    raw_failover_request = _route_request_factory(
        client_request_id="client-request-01",
        configuration_revision=11,
        candidates=(failed_candidate, replacement_candidate),
    )(admission.admission_snapshot)
    retry_workflows = _workflows(
        store,
        start=1_000,
        configuration_revision=12,
        health_ids=health_ids,
        routing_ids=shared_ids,
        dispatch_ids=shared_ids,
        configured_members=members,
    )

    outcome = retry_workflows.authorize_retry(
        request.command_id,
        attempt.attempt_id,
        route_intent=FailoverRoute(
            failed_route_decision_id=admission.route.decision.decision_id,
            failover_route_request=raw_failover_request,
        ),
        route_request_factory=_route_request_factory(
            client_request_id="client-request-01",
            configuration_revision=12,
            candidates=(failed_candidate, replacement_candidate),
        ),
        expected_request_revision=failed_request.revision,
        expected_previous_attempt_revision=failed_attempt.revision,
        expected_highest_attempt_number=failed_attempt.attempt_number,
        frozen_max_attempts=3,
        current_policy_revision=failed_request.policy_revision,
        reconciliation_complete=False,
        heartbeat_timeout_ms=5_000,
    )

    assert outcome.request.selected_peer_instance_id == "cx"
    assert outcome.request.selected_profile_id == "cx.deepthink"
    assert outcome.request.configuration_revision == 12
    failed_audits = tuple(
        candidate
        for candidate in outcome.retry_admission.route_decision.candidates
        if candidate.instance_id == "ag"
    )
    assert failed_audits
    assert all(
        candidate.exclusion_reason == FAILED_TARGET_EXCLUDED_BY_RETRY
        for candidate in failed_audits
    )


def test_authorize_retry_refuses_on_drift(
    store: SqliteStateStore,
) -> None:
    _seed_health(store)
    health_ids = SequentialIdSource()
    routing_ids = SequentialIdSource()
    dispatch_ids = SequentialIdSource()
    workflows = _workflows(
        store,
        health_ids=health_ids,
        routing_ids=routing_ids,
        dispatch_ids=dispatch_ids,
    )
    admission = workflows.admit_request(
        _envelope(),
        route_request_factory=_route_request_factory(
            client_request_id="client-request-01",
            configuration_revision=11,
        ),
        **_admission_kwargs(),
    )
    request, _, _, _ = admission.dispatch_admission
    dispatch = workflows._dispatch  # noqa: SLF001 - direct service access for test setup only

    prepared = dispatch.prepare_request(request.command_id)
    attempt = dispatch.create_attempt(prepared.command_id, expected_authorized_attempt_number=1)
    failed_request, failed_attempt = dispatch.fail_pre_dispatch(
        prepared.command_id,
        attempt.attempt_id,
        error_code=ErrorCode.SPAWN_FAILED,
        transport="pipe",
    )
    dispatch.freeze_retry_policy(request.command_id, 3)

    drifted_workflows = _workflows(
        store,
        start=1_000,
        configuration_revision=12,
        health_ids=health_ids,
        routing_ids=routing_ids,
        dispatch_ids=dispatch_ids,
    )
    with pytest.raises(RetryRouteUnavailableError) as exc_info:
        drifted_workflows.authorize_retry(
            request.command_id,
            attempt.attempt_id,
            route_intent=SameTargetRoute(
                route_decision_id=admission.route.decision.decision_id,
                current_route_request=_route_request_factory(
                    client_request_id="client-request-01",
                    configuration_revision=11,
                )(admission.admission_snapshot),
            ),
            route_request_factory=_route_request_factory(
                client_request_id="client-request-01",
                configuration_revision=12,
            ),
            expected_request_revision=failed_request.revision,
            expected_previous_attempt_revision=failed_attempt.revision,
            expected_highest_attempt_number=failed_attempt.attempt_number,
            frozen_max_attempts=3,
            current_policy_revision=failed_request.policy_revision,
            reconciliation_complete=False,
            heartbeat_timeout_ms=5_000,
        )

    assert exc_info.value.error_code is ErrorCode.CONFIGURATION_STALE
    assert dispatch.get_request(request.command_id) == failed_request


def test_require_bound_route_rejects_mismatched_decision(
    store: SqliteStateStore,
) -> None:
    _seed_health(store)
    workflows = _workflows(store)
    first = workflows.admit_request(
        _envelope(client_request_id="client-request-01"),
        route_request_factory=_route_request_factory(
            client_request_id="client-request-01",
            configuration_revision=11,
        ),
        **_admission_kwargs(),
    )
    second = workflows.admit_request(
        _envelope(
            client_request_id="client-request-02",
            idempotency_key="idempotency-02",
        ),
        route_request_factory=_route_request_factory(
            client_request_id="client-request-02",
            configuration_revision=11,
        ),
        **_admission_kwargs(),
    )

    first_request, _, _, _ = first.dispatch_admission

    with pytest.raises(InvalidMutationError):
        workflows.prepare_for_dispatch(
            first_request.command_id,
            route_decision_id=second.route.decision.decision_id,
            route_request_factory=_route_request_factory(
                client_request_id="client-request-01",
                configuration_revision=11,
            ),
        )
