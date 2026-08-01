"""Integration tests for peerhub.application.workflows."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from peerhub.application.workflows import ApplicationWorkflows
from peerhub.core.errors import InvalidMutationError
from peerhub.core.evidence import EvidenceRef, EvidenceState, EvidenceValue
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
from peerhub.dispatch.service import DispatchService
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
) -> HealthScopeMembershipSnapshot:
    return HealthScopeMembershipSnapshot(
        configuration_revision=configuration_revision,
        configuration_digest="e" * 64,
        configured_members=(("ag", "ag.deepthink"),),
        bindings=(),
    )


def _readiness(
    observation_id: str,
    *,
    observed_at: int = 100,
) -> ReadinessObserved:
    return ReadinessObserved(
        observation_id=observation_id,
        instance_id="ag",
        profile_id="ag.deepthink",
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


def _candidate(eligible: bool = True) -> RouteCandidateInput:
    return RouteCandidateInput(
        candidate_id="ag.deepthink",
        instance_id="ag",
        representative_profile_id="ag.deepthink",
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
    eligible: bool = True,
):
    def factory(
        admission_snapshot: AdmissionSnapshot,
    ) -> RouteRequest:
        return RouteRequest(
            client_request_id=client_request_id,
            configuration=ConfigurationSnapshot(
                revision=configuration_revision,
                digest="c" * 64,
            ),
            admission_snapshot=admission_snapshot,
            requested_capabilities=(),
            profile_constraints={},
            required_readiness_binding=None,
            candidates=(_candidate(eligible=eligible),),
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
            configuration_revision=configuration_revision
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
    )
    return ApplicationWorkflows(
        telemetry=telemetry,
        health=health,
        routing=routing,
        dispatch=dispatch,
    )


def _seed_health(store: SqliteStateStore) -> None:
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
        membership=_membership(),
        clock=DeterministicClock(start=100),
        ids=SequentialIdSource(),
    )
    health.evaluate_and_persist_readiness(
        _readiness("readiness-01"),
        sealed_runtime_revision="runtime-r17",
        adapter_declares_probe_safe=True,
    )


def _admission_kwargs() -> dict:
    return dict(
        authenticated_principal="principal-01",
        actor_authorized=True,
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

    request, _, _ = result.dispatch_admission
    assert request.selected_peer_instance_id == "ag"
    assert request.selected_profile_id == "ag.deepthink"
    assert request.state is RequestState.ADMITTED


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
    request, _, _ = admission.dispatch_admission

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
    request, _, _ = admission.dispatch_admission

    drifted_workflows = _workflows(
        store,
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
    request, _, _ = admission.dispatch_admission
    dispatch = workflows._dispatch  # noqa: SLF001 - direct service access for test setup only

    prepared = dispatch.prepare_request(request.command_id)
    attempt = dispatch.create_attempt(prepared.command_id)
    dispatch.fail_pre_dispatch(
        prepared.command_id,
        attempt.attempt_id,
        error_code=ErrorCode.SPAWN_FAILED,
        transport="pipe",
    )

    outcome = workflows.authorize_retry(
        request.command_id,
        attempt.attempt_id,
        route_decision_id=admission.route.decision.decision_id,
        route_request_factory=_route_request_factory(
            client_request_id="client-request-01",
            configuration_revision=11,
        ),
        reconciliation_complete=False,
        heartbeat_timeout_ms=5_000,
    )

    assert outcome.route_recheck.validation.dispatch_permitted
    assert outcome.retry_admission is not None
    assert outcome.retry_admission[0].state is RequestState.PREPARED


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
    request, _, _ = admission.dispatch_admission
    dispatch = workflows._dispatch  # noqa: SLF001 - direct service access for test setup only

    prepared = dispatch.prepare_request(request.command_id)
    attempt = dispatch.create_attempt(prepared.command_id)
    dispatch.fail_pre_dispatch(
        prepared.command_id,
        attempt.attempt_id,
        error_code=ErrorCode.SPAWN_FAILED,
        transport="pipe",
    )

    drifted_workflows = _workflows(
        store,
        configuration_revision=12,
        health_ids=health_ids,
        routing_ids=routing_ids,
        dispatch_ids=dispatch_ids,
    )
    outcome = drifted_workflows.authorize_retry(
        request.command_id,
        attempt.attempt_id,
        route_decision_id=admission.route.decision.decision_id,
        route_request_factory=_route_request_factory(
            client_request_id="client-request-01",
            configuration_revision=12,
        ),
        reconciliation_complete=False,
        heartbeat_timeout_ms=5_000,
    )

    assert not outcome.route_recheck.validation.dispatch_permitted
    assert outcome.retry_admission is None
    assert outcome.request.state is RequestState.FAILED_PRE_DISPATCH


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

    first_request, _, _ = first.dispatch_admission

    with pytest.raises(InvalidMutationError):
        workflows.prepare_for_dispatch(
            first_request.command_id,
            route_decision_id=second.route.decision.decision_id,
            route_request_factory=_route_request_factory(
                client_request_id="client-request-01",
                configuration_revision=11,
            ),
        )
