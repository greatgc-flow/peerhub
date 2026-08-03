"""Unit tests for ApplicationWorkflows.dispatch_and_execute."""

from __future__ import annotations

from collections.abc import Iterator
import hashlib
from pathlib import Path
import sys
import pytest

from peerhub.adapters.contract import (
    AdapterRequest,
    ArtifactSpec,
    InvocationPlan,
    ProtocolAssessment,
    SessionAction,
)
from peerhub.application.workflows import ApplicationWorkflows
from peerhub.core.errors import InvalidMutationError
from peerhub.core.evidence import EvidenceRef, EvidenceState, EvidenceValue
from peerhub.core.execution import TransportKind, TransportLimits
from peerhub.core.protocol import (
    PROTOCOL_MAJOR,
    PROTOCOL_MINOR,
    SCHEMA_VERSION,
    CommandEnvelope,
)
from peerhub.dispatch.contract import (
    CompletionAssessmentState,
    CompletionContract,
    CompletionContractKind,
    LeaseState,
    RequestState,
)
from peerhub.dispatch.materializer import (
    ArtifactMaterializer,
    MaterializationStatus,
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
        tmp_path / "workflows_exec.sqlite3",
        workspace_home_id="workspace-workflows-exec",
    )
    state_store.initialize()
    _seed_health(state_store)
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


def _membership(*, configuration_revision: int = 11) -> HealthScopeMembershipSnapshot:
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
        source_tag="absent",
        provider_id="phase0-usage",
        provider_version="1",
        observed_at=None,
        captured_at=100,
        freshness_ttl=7200,
        evidence_ref=EvidenceRef("sha256:usage-absent"),
        value=None,
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


def _route_request_factory(client_request_id: str = "client-request-01", configuration_revision: int = 11, eligible: bool = True):
    def factory(admission_snapshot: AdmissionSnapshot) -> RouteRequest:
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


def _envelope(*, client_request_id: str = "client-request-01", idempotency_key: str = "idempotency-01") -> CommandEnvelope:
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
) -> tuple[ApplicationWorkflows, DispatchService]:
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
        ids=SequentialIdSource(),
        clock=DeterministicClock(start=start),
    )
    routing = RoutingService(
        store,
        ids=SequentialIdSource(),
        clock=DeterministicClock(start=start + 10),
    )
    dispatch = DispatchService(
        store,
        ids=SequentialIdSource(),
        clock=DeterministicClock(start=start + 20),
    )
    workflows = ApplicationWorkflows(
        telemetry=telemetry,
        health=health,
        routing=routing,
        dispatch=dispatch,
    )
    return workflows, dispatch


def _admit_and_prepare(
    workflows: ApplicationWorkflows,
    envelope: CommandEnvelope,
) -> str:
    factory = _route_request_factory(client_request_id=envelope.client_request_id)
    contract = _completion_contract()
    adm = workflows.admit_request(
        envelope,
        route_request_factory=factory,
        authenticated_principal="actor-01",
        actor_authorized=True,
        completion_contract=contract,
        dispatch_policy_revision=7,
        session_id="session-01",
        owner_principal_id="principal-01",
        owner_instance_id="ag",
        authority_epoch=1,
        heartbeat_timeout_ms=30000,
        owner_peer_id="peer-01",
    )
    req = adm.dispatch_admission[0]
    prep = workflows.prepare_for_dispatch(
        req.command_id,
        route_decision_id=adm.route.decision.decision_id,
        route_request_factory=factory,
    )
    return str(req.command_id)


def test_dispatch_and_execute_happy_path_zero_artifacts(tmp_path: Path, store: SqliteStateStore) -> None:
    """Trivial subprocess exiting 0 cleanly with zero artifacts."""
    workflows, dispatch = _workflows(store)
    cmd_id = _admit_and_prepare(workflows, _envelope())

    workspace_root = tmp_path / "ws"
    workspace_root.mkdir()

    materializer = ArtifactMaterializer(
        unit_of_work_factory=store.unit_of_work,
        workspace_root=workspace_root,
    )

    contract = _completion_contract()
    adapter_req = AdapterRequest(
        request_id="req-01",
        prompt_content="hello",
        prompt_reference=None,
        workspace_scope="ws-1",
        profile_id="ag.deepthink",
        requested_session_action=SessionAction.NONE,
        completion_contract=contract,
    )
    inv_plan = InvocationPlan(
        argv=(sys.executable, "-c", "print('hello')"),
        cwd_reference=".",
        environment_delta={},
        transport=TransportKind.PIPE,
        stdin_payload=None,
        limits=TransportLimits(process_timeout_ms=5000, silence_timeout_ms=5000, max_output_bytes=65536),
        redacted_display="python -c print",
        artifacts=(),
        session_action=SessionAction.NONE,
    )
    protocol_assessment = ProtocolAssessment(
        parsed=True,
        response_present=True,
        vendor_completion_marker=True,
        suspected_truncation=False,
        protocol_failure=None,
    )

    res = workflows.dispatch_and_execute(
        cmd_id,
        materializer=materializer,
        adapter_request=adapter_req,
        invocation_plan=inv_plan,
        workspace_roots={"ws-1": workspace_root},
        content_providers={},
        completion_contract=contract,
        protocol_assessment=protocol_assessment,
        heartbeat_timeout_ms=10000,
    )

    assert res.request.state is RequestState.SUCCEEDED_VERIFIED
    assert res.attempt.state is RequestState.SUCCEEDED_VERIFIED
    assert res.lease is not None
    assert res.lease.state is LeaseState.RELEASED
    assert res.process_outcome is not None
    assert res.process_outcome.execution_outcome.exit_code == 0
    assert res.completion_assessment is not None
    assert res.completion_assessment.state is CompletionAssessmentState.VERIFIED


def test_dispatch_and_execute_happy_path_with_artifact(tmp_path: Path, store: SqliteStateStore) -> None:
    """Happy path with 1 artifact materialized, reserved, executed, and consumed."""
    workflows, dispatch = _workflows(store)
    cmd_id = _admit_and_prepare(workflows, _envelope())

    workspace_root = tmp_path / "ws"
    workspace_root.mkdir()

    content = b"artifact body data"
    digest = hashlib.sha256(content).hexdigest()
    length = len(content)

    spec = ArtifactSpec(
        artifact_id="art-1",
        placeholder="__ART1__",
        content_bytes=content,
        content_reference=None,
        sha256_hex=digest,
        expected_length=length,
        access_mode="READ_ONLY",
        lifecycle="EPHEMERAL",
    )

    materializer = ArtifactMaterializer(
        unit_of_work_factory=store.unit_of_work,
        workspace_root=workspace_root,
    )

    contract = _completion_contract()
    adapter_req = AdapterRequest(
        request_id="req-01",
        prompt_content="hello",
        prompt_reference=None,
        workspace_scope="ws-1",
        profile_id="ag.deepthink",
        requested_session_action=SessionAction.NONE,
        completion_contract=contract,
    )
    inv_plan = InvocationPlan(
        argv=(sys.executable, "-c", "print('art-ok')", "__ART1__"),
        cwd_reference=".",
        environment_delta={},
        transport=TransportKind.PIPE,
        stdin_payload=None,
        limits=TransportLimits(process_timeout_ms=5000, silence_timeout_ms=5000, max_output_bytes=65536),
        redacted_display="python -c art",
        artifacts=(spec,),
        session_action=SessionAction.NONE,
    )
    protocol_assessment = ProtocolAssessment(
        parsed=True,
        response_present=True,
        vendor_completion_marker=True,
        suspected_truncation=False,
        protocol_failure=None,
    )

    res = workflows.dispatch_and_execute(
        cmd_id,
        materializer=materializer,
        adapter_request=adapter_req,
        invocation_plan=inv_plan,
        workspace_roots={"ws-1": workspace_root},
        content_providers={"art-1": lambda: content},
        completion_contract=contract,
        protocol_assessment=protocol_assessment,
        heartbeat_timeout_ms=10000,
    )

    assert res.request.state is RequestState.SUCCEEDED_VERIFIED
    assert res.attempt.state is RequestState.SUCCEEDED_VERIFIED
    assert res.lease is not None
    assert res.lease.state is LeaseState.RELEASED
    assert res.materialization_results is not None
    assert len(res.materialization_results) == 1
    assert res.materialization_results[0].status in (MaterializationStatus.SUCCESS, MaterializationStatus.CONFLICT_WINNER)
    assert res.process_outcome is not None
    assert res.completion_assessment is not None
    assert res.completion_assessment.state is CompletionAssessmentState.VERIFIED


def test_dispatch_and_execute_materialization_failure(tmp_path: Path, store: SqliteStateStore) -> None:
    """Materialization failure aborts via fail_pre_dispatch, no process spawns."""
    workflows, dispatch = _workflows(store)
    cmd_id = _admit_and_prepare(workflows, _envelope())

    workspace_root = tmp_path / "ws"
    workspace_root.mkdir()

    content = b"correct content"
    digest = hashlib.sha256(content).hexdigest()

    spec = ArtifactSpec(
        artifact_id="art-1",
        placeholder="__ART1__",
        content_bytes=content,
        content_reference=None,
        sha256_hex=digest,
        expected_length=len(content),
        access_mode="READ_ONLY",
        lifecycle="EPHEMERAL",
    )

    materializer = ArtifactMaterializer(
        unit_of_work_factory=store.unit_of_work,
        workspace_root=workspace_root,
    )

    contract = _completion_contract()
    adapter_req = AdapterRequest(
        request_id="req-01",
        prompt_content="hello",
        prompt_reference=None,
        workspace_scope="ws-1",
        profile_id="ag.deepthink",
        requested_session_action=SessionAction.NONE,
        completion_contract=contract,
    )
    inv_plan = InvocationPlan(
        argv=(sys.executable, "-c", "print('should not run')"),
        cwd_reference=".",
        environment_delta={},
        transport=TransportKind.PIPE,
        stdin_payload=None,
        limits=TransportLimits(process_timeout_ms=5000, silence_timeout_ms=5000, max_output_bytes=65536),
        redacted_display="python -c test",
        artifacts=(spec,),
        session_action=SessionAction.NONE,
    )
    protocol_assessment = ProtocolAssessment(
        parsed=True,
        response_present=True,
        vendor_completion_marker=True,
        suspected_truncation=False,
        protocol_failure=None,
    )

    # Provider returns wrong content -> digest mismatch -> HARD_FAILURE
    res = workflows.dispatch_and_execute(
        cmd_id,
        materializer=materializer,
        adapter_request=adapter_req,
        invocation_plan=inv_plan,
        workspace_roots={"ws-1": workspace_root},
        content_providers={"art-1": lambda: b"CORRUPTED CONTENT"},
        completion_contract=contract,
        protocol_assessment=protocol_assessment,
        heartbeat_timeout_ms=10000,
    )

    assert res.attempt.state is RequestState.FAILED_PRE_DISPATCH
    assert res.process_outcome is None


def test_dispatch_and_execute_reservation_failure(tmp_path: Path, store: SqliteStateStore) -> None:
    """Reservation failure triggers fail_pre_dispatch and orphans artifacts."""
    workflows, dispatch = _workflows(store)
    cmd_id = _admit_and_prepare(workflows, _envelope())

    workspace_root = tmp_path / "ws"
    workspace_root.mkdir()

    materializer = ArtifactMaterializer(
        unit_of_work_factory=store.unit_of_work,
        workspace_root=workspace_root,
    )

    contract = _completion_contract()
    adapter_req = AdapterRequest(
        request_id="req-01",
        prompt_content="hello",
        prompt_reference=None,
        workspace_scope="ws-1",
        profile_id="ag.deepthink",
        requested_session_action=SessionAction.NONE,
        completion_contract=contract,
    )
    content = b"artifact content"
    digest = hashlib.sha256(content).hexdigest()

    spec = ArtifactSpec(
        artifact_id="art-1",
        placeholder="__ART1__",
        content_bytes=content,
        content_reference=None,
        sha256_hex=digest,
        expected_length=len(content),
        access_mode="READ_ONLY",
        lifecycle="EPHEMERAL",
    )

    inv_plan = InvocationPlan(
        argv=(sys.executable, "-c", "print('hello')"),
        cwd_reference=".",
        environment_delta={},
        transport=TransportKind.PIPE,
        stdin_payload=None,
        limits=TransportLimits(process_timeout_ms=5000, silence_timeout_ms=5000, max_output_bytes=65536),
        redacted_display="python -c print",
        artifacts=(spec,),
        session_action=SessionAction.NONE,
    )
    protocol_assessment = ProtocolAssessment(
        parsed=True,
        response_present=True,
        vendor_completion_marker=True,
        suspected_truncation=False,
        protocol_failure=None,
    )

    # Monkeypatch service method to simulate reservation failure
    def mock_reserve(*args, **kwargs):
        raise InvalidMutationError("simulated reservation failure")

    dispatch.record_dispatch_intent_and_reserve_artifacts = mock_reserve

    res = workflows.dispatch_and_execute(
        cmd_id,
        materializer=materializer,
        adapter_request=adapter_req,
        invocation_plan=inv_plan,
        workspace_roots={"ws-1": workspace_root},
        content_providers={"art-1": lambda: content},
        completion_contract=contract,
        protocol_assessment=protocol_assessment,
        heartbeat_timeout_ms=10000,
        service=dispatch,
    )

    assert res.attempt.state is RequestState.FAILED_PRE_DISPATCH
    assert res.process_outcome is None


def test_dispatch_and_execute_nonzero_exit(tmp_path: Path, store: SqliteStateStore) -> None:
    """Trivial subprocess exiting nonzero -> completion assessment reflects failure."""
    workflows, dispatch = _workflows(store)
    cmd_id = _admit_and_prepare(workflows, _envelope())

    workspace_root = tmp_path / "ws"
    workspace_root.mkdir()

    materializer = ArtifactMaterializer(
        unit_of_work_factory=store.unit_of_work,
        workspace_root=workspace_root,
    )

    contract = _completion_contract()
    adapter_req = AdapterRequest(
        request_id="req-01",
        prompt_content="hello",
        prompt_reference=None,
        workspace_scope="ws-1",
        profile_id="ag.deepthink",
        requested_session_action=SessionAction.NONE,
        completion_contract=contract,
    )
    inv_plan = InvocationPlan(
        argv=(sys.executable, "-c", "import sys; sys.exit(1)"),
        cwd_reference=".",
        environment_delta={},
        transport=TransportKind.PIPE,
        stdin_payload=None,
        limits=TransportLimits(process_timeout_ms=5000, silence_timeout_ms=5000, max_output_bytes=65536),
        redacted_display="python exit 1",
        artifacts=(),
        session_action=SessionAction.NONE,
    )
    protocol_assessment = ProtocolAssessment(
        parsed=True,
        response_present=True,
        vendor_completion_marker=True,
        suspected_truncation=False,
        protocol_failure=None,
    )

    res = workflows.dispatch_and_execute(
        cmd_id,
        materializer=materializer,
        adapter_request=adapter_req,
        invocation_plan=inv_plan,
        workspace_roots={"ws-1": workspace_root},
        content_providers={},
        completion_contract=contract,
        protocol_assessment=protocol_assessment,
        heartbeat_timeout_ms=10000,
    )

    assert res.process_outcome is not None
    assert res.process_outcome.execution_outcome.exit_code == 1
    assert res.completion_assessment is not None
    assert res.completion_assessment.state is CompletionAssessmentState.NOT_APPLICABLE
