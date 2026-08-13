"""Unit tests for ApplicationWorkflows.dispatch_and_execute."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
import hashlib
from pathlib import Path
import sqlite3
import sys
import pytest

from peerhub.adapters.contract import (
    AdapterRequest,
    ArtifactSpec,
    Capability,
    DecoderEvent,
    DecoderEventKind,
    InvocationPlan,
    PeerAdapter,
    ProfileDescriptor,
    SessionAction,
    SessionHint,
)
from peerhub.adapters.codex_adapter import RealCodexAdapter
from peerhub.application.workflows import ApplicationWorkflows
from peerhub.builtins.fake_adapter import (
    FakePeerAdapter,
    _FAKE_DESCRIPTOR,
    _FAKE_PROFILE,
)
from peerhub.application import workflows as workflows_module
from peerhub.application.workflows import ApplicationWorkflows
from peerhub.core.errors import InvalidMutationError, UnsupportedCapabilityError
from peerhub.core.evidence import EvidenceRef, EvidenceState, EvidenceValue
from peerhub.core.execution import TransportKind, TransportLimits
from peerhub.core.identity import AuthenticatedSubject
from peerhub.core.protocol import (
    PROTOCOL_MAJOR,
    PROTOCOL_MINOR,
    SCHEMA_VERSION,
    CommandEnvelope,
    ErrorCode,
    RevisionValue,
)
from peerhub.dispatch.contract import (
    CompletionAssessmentState,
    CompletionContract,
    CompletionContractKind,
    LeaseState,
    RequestState,
    TerminalClassification,
)
from peerhub.dispatch.capability import (
    CapabilityLeaseViolation,
    CapabilityPolicy,
    CapabilityTier,
    EnforcementLevel,
    InvocationEnforcementReceipt,
    PeerEnforcementEvidence,
    PeerEnforcementEvidenceProvider,
    ValidatedCapabilityBinding,
    ValidatedCapabilityLease,
)
from peerhub.dispatch.capability_policy import (
    StaticCapabilityPolicy,
    StaticPeerEnforcementEvidenceProvider,
    default_enforcement_evidence_provider,
)
from peerhub.dispatch.materializer import (
    ArtifactMaterializer,
    MaterializationStatus,
)
from peerhub.dispatch.service import DispatchService
from peerhub.governance.contract import OutboxEvent
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


def _route_request_factory(
    client_request_id: str = "client-request-01",
    configuration_revision: int = 11,
    eligible: bool = True,
    required_capability_tier: CapabilityTier = CapabilityTier.READ_ONLY,
):
    def factory(admission_snapshot: AdmissionSnapshot) -> RouteRequest:
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


# The route names instance "ag" while these tests dispatch through
# FakePeerAdapter (peer_kind "fake").  Increment 4 resolves peer kind from a
# machine-owned evidence provider and requires it to match the adapter, so the
# fixture states that mapping explicitly instead of letting the two disagree.
_CONTROLLED_FAKE_EVIDENCE = StaticPeerEnforcementEvidenceProvider(
    {
        "ag": PeerEnforcementEvidence(
            peer_instance_id="ag",
            peer_kind="fake",
            enforcement_ceiling=None,
            source_tag="absent",
        )
    }
)


# dispatch_and_execute() must be handed the descriptor for the profile that
# routing actually selected: increment 4's gate rejects a dispatch whose
# profile differs from the admitted one, exactly as production does where the
# route candidate and the dispatched profile come from the same resolved
# target.  FakePeerAdapter.plan_invocation() ignores the profile itself.
_ROUTED_PROFILE = ProfileDescriptor(
    profile_id="ag.deepthink",
    profile_class=_FAKE_PROFILE.profile_class,
    supports_reasoning_effort=_FAKE_PROFILE.supports_reasoning_effort,
)


def _workflows(
    store: SqliteStateStore,
    *,
    start: int = 200,
    peer_adapter: PeerAdapter | None = None,
    enforcement_evidence: PeerEnforcementEvidenceProvider = (
        _CONTROLLED_FAKE_EVIDENCE
    ),
    capability_policy: CapabilityPolicy | None = None,
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
        capability_policy=capability_policy,
        enforcement_evidence=enforcement_evidence,
    )
    workflows = ApplicationWorkflows(
        telemetry=telemetry,
        health=health,
        routing=routing,
        dispatch=dispatch,
        peer_adapter=peer_adapter,
    )
    return workflows, dispatch


def _admit_and_prepare(
    workflows: ApplicationWorkflows,
    envelope: CommandEnvelope,
    *,
    required_capability_tier: CapabilityTier = CapabilityTier.READ_ONLY,
) -> tuple[str, str, str]:
    """Admit and prepare, returning (command_id, capability_lease_id, instance)."""
    factory = _route_request_factory(
        client_request_id=envelope.client_request_id,
        required_capability_tier=required_capability_tier,
    )
    contract = _completion_contract()
    adm = workflows.admit_request(
        envelope,
        route_request_factory=factory,
        required_capability_tier=required_capability_tier,
        authenticated_subject=AuthenticatedSubject(
            "actor-01",
            "test",
        ),
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
    capability_lease = adm.dispatch_admission[3]
    prep = workflows.prepare_for_dispatch(
        req.command_id,
        route_decision_id=adm.route.decision.decision_id,
        route_request_factory=factory,
    )
    return (
        str(req.command_id),
        capability_lease.capability_lease_id,
        req.selected_peer_instance_id,
    )


def test_dispatch_and_execute_happy_path_zero_artifacts(tmp_path: Path, store: SqliteStateStore) -> None:
    """Trivial subprocess exiting 0 cleanly with zero artifacts."""
    adapter = FakePeerAdapter(stdout="hello\n")
    workflows, dispatch = _workflows(store, peer_adapter=adapter)
    cmd_id, cap_lease_id, peer_instance = _admit_and_prepare(workflows, _envelope())

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
    limits = TransportLimits(process_timeout_ms=5000, silence_timeout_ms=5000, max_output_bytes=65536)
    res = workflows.dispatch_and_execute(
        cmd_id,
        capability_lease_id=cap_lease_id,
        peer_instance_id=peer_instance,
        current_policy_revision=7,
        materializer=materializer,
        adapter_request=adapter_req,
        profile=_ROUTED_PROFILE,
        limits=limits,
        workspace_roots={"ws-1": workspace_root},
        content_providers={},
        completion_contract=contract,
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


def test_dispatch_and_execute_streams_ordered_decoder_events(
    tmp_path: Path,
    store: SqliteStateStore,
) -> None:
    workflows, _dispatch = _workflows(store)
    cmd_id, cap_lease_id, peer_instance = _admit_and_prepare(
        workflows, _envelope()
    )
    workspace_root = tmp_path / "ws"
    workspace_root.mkdir()
    materializer = ArtifactMaterializer(
        unit_of_work_factory=store.unit_of_work,
        workspace_root=workspace_root,
    )
    contract = _completion_contract()
    adapter_req = AdapterRequest(
        request_id="req-stream-01",
        prompt_content="hello",
        prompt_reference=None,
        workspace_scope="ws-1",
        profile_id="ag.deepthink",
        requested_session_action=SessionAction.NONE,
        completion_contract=contract,
    )
    adapter = FakePeerAdapter(
        chunks=("first", "second"),
        chunk_delay=0.2,
        stderr="diagnostic",
    )
    streamed_events: list[DecoderEvent] = []

    res = workflows.dispatch_and_execute(
        cmd_id,
        capability_lease_id=cap_lease_id,
        peer_instance_id=peer_instance,
        current_policy_revision=7,
        materializer=materializer,
        adapter_request=adapter_req,
        peer_adapter=adapter,
        profile=_ROUTED_PROFILE,
        limits=TransportLimits(
            process_timeout_ms=5000,
            silence_timeout_ms=5000,
            max_output_bytes=65536,
        ),
        workspace_roots={"ws-1": workspace_root},
        content_providers={},
        completion_contract=contract,
        heartbeat_timeout_ms=10000,
        event_sink=streamed_events.append,
    )

    assert res.decoded_output is not None
    assert tuple(streamed_events) == res.decoded_output.events
    assert "".join(
        str(event.payload["text"])
        for event in streamed_events
        if event.payload["channel"] == "STDOUT"
    ) == "firstsecond"
    assert "".join(
        str(event.payload["text"])
        for event in streamed_events
        if event.payload["channel"] == "STDERR"
    ) == "diagnostic"


def test_dispatch_and_execute_streams_codex_event_before_process_exit(
    tmp_path: Path,
    store: SqliteStateStore,
) -> None:
    workflows, _dispatch = _workflows(store)
    cmd_id, cap_lease_id, peer_instance = _admit_and_prepare(
        workflows, _envelope()
    )
    workspace_root = tmp_path / "ws"
    workspace_root.mkdir()
    completion_marker = workspace_root / "process-finished"
    first_line = '{"type":"thread.started","thread_id":"thread-1"}\n'
    final_line = (
        '{"type":"item.completed","item":'
        '{"type":"agent_message","text":"done"}}\n'
    )

    class HermeticStreamingCodexAdapter(RealCodexAdapter):
        descriptor = replace(
            RealCodexAdapter.descriptor,
            peer_kind="fake",
            profiles=(_ROUTED_PROFILE,),
        )

        def plan_invocation(
            self,
            request: AdapterRequest,
            profile: ProfileDescriptor,
            session: SessionHint | None,
            limits: TransportLimits,
        ) -> InvocationPlan:
            script = (
                "import pathlib, sys, time; "
                f"sys.stdout.write({first_line!r}); sys.stdout.flush(); "
                "time.sleep(0.5); "
                f"pathlib.Path({str(completion_marker)!r}).write_text('done'); "
                f"sys.stdout.write({final_line!r}); sys.stdout.flush()"
            )
            return InvocationPlan(
                argv=(sys.executable, "-c", script),
                cwd_reference=request.workspace_scope,
                environment_delta={},
                transport=TransportKind.PIPE,
                stdin_payload=None,
                limits=limits,
                redacted_display="python -c <redacted>",
                artifacts=(),
                session_action=request.requested_session_action,
            )

    materializer = ArtifactMaterializer(
        unit_of_work_factory=store.unit_of_work,
        workspace_root=workspace_root,
    )
    contract = _completion_contract()
    adapter_req = AdapterRequest(
        request_id="req-stream-codex-01",
        prompt_content="hello",
        prompt_reference=None,
        workspace_scope="ws-1",
        profile_id="ag.deepthink",
        requested_session_action=SessionAction.NONE,
        completion_contract=contract,
    )
    streamed_events: list[DecoderEvent] = []
    session_event_arrived_before_exit: list[bool] = []

    def event_sink(event: DecoderEvent) -> None:
        streamed_events.append(event)
        if event.kind is DecoderEventKind.SESSION_IDENTITY:
            session_event_arrived_before_exit.append(
                not completion_marker.exists()
            )

    res = workflows.dispatch_and_execute(
        cmd_id,
        capability_lease_id=cap_lease_id,
        peer_instance_id=peer_instance,
        current_policy_revision=7,
        materializer=materializer,
        adapter_request=adapter_req,
        peer_adapter=HermeticStreamingCodexAdapter(),
        profile=_ROUTED_PROFILE,
        limits=TransportLimits(
            process_timeout_ms=5000,
            silence_timeout_ms=5000,
            max_output_bytes=65536,
        ),
        workspace_roots={"ws-1": workspace_root},
        content_providers={},
        completion_contract=contract,
        heartbeat_timeout_ms=10000,
        event_sink=event_sink,
    )

    assert session_event_arrived_before_exit == [True]
    assert res.decoded_output is not None
    assert tuple(streamed_events) == res.decoded_output.events
    assert [event.kind for event in streamed_events] == [
        DecoderEventKind.SESSION_IDENTITY,
        DecoderEventKind.ASSISTANT_TEXT,
    ]
    assert res.decoded_output.canonical_text == "done"


def test_dispatch_and_execute_happy_path_with_artifact(tmp_path: Path, store: SqliteStateStore) -> None:
    """Happy path with 1 artifact materialized, reserved, executed, and consumed."""
    workflows, dispatch = _workflows(store)
    cmd_id, cap_lease_id, peer_instance = _admit_and_prepare(workflows, _envelope())

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
    limits = TransportLimits(process_timeout_ms=5000, silence_timeout_ms=5000, max_output_bytes=65536)
    adapter = FakePeerAdapter(stdout="art-ok\n", artifacts=(spec,))

    res = workflows.dispatch_and_execute(
        cmd_id,
        capability_lease_id=cap_lease_id,
        peer_instance_id=peer_instance,
        current_policy_revision=7,
        materializer=materializer,
        adapter_request=adapter_req,
        peer_adapter=adapter,
        profile=_ROUTED_PROFILE,
        limits=limits,
        workspace_roots={"ws-1": workspace_root},
        content_providers={"art-1": lambda: content},
        completion_contract=contract,
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
    cmd_id, cap_lease_id, peer_instance = _admit_and_prepare(workflows, _envelope())

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
    limits = TransportLimits(process_timeout_ms=5000, silence_timeout_ms=5000, max_output_bytes=65536)
    adapter = FakePeerAdapter(stdout="should not run\n", artifacts=(spec,))

    # Provider returns wrong content -> digest mismatch -> HARD_FAILURE
    res = workflows.dispatch_and_execute(
        cmd_id,
        capability_lease_id=cap_lease_id,
        peer_instance_id=peer_instance,
        current_policy_revision=7,
        materializer=materializer,
        adapter_request=adapter_req,
        peer_adapter=adapter,
        profile=_ROUTED_PROFILE,
        limits=limits,
        workspace_roots={"ws-1": workspace_root},
        content_providers={"art-1": lambda: b"CORRUPTED CONTENT"},
        completion_contract=contract,
        heartbeat_timeout_ms=10000,
    )

    assert res.attempt.state is RequestState.FAILED_PRE_DISPATCH
    assert res.process_outcome is None


def test_dispatch_and_execute_reservation_failure(tmp_path: Path, store: SqliteStateStore) -> None:
    """Reservation failure triggers fail_pre_dispatch and orphans artifacts."""
    workflows, dispatch = _workflows(store)
    cmd_id, cap_lease_id, peer_instance = _admit_and_prepare(workflows, _envelope())

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

    limits = TransportLimits(process_timeout_ms=5000, silence_timeout_ms=5000, max_output_bytes=65536)
    adapter = FakePeerAdapter(stdout="hello\n", artifacts=(spec,))

    # Monkeypatch service method to simulate reservation failure
    def mock_reserve(*args, **kwargs):
        raise InvalidMutationError("simulated reservation failure")

    dispatch.record_dispatch_intent_and_reserve_artifacts = mock_reserve

    res = workflows.dispatch_and_execute(
        cmd_id,
        capability_lease_id=cap_lease_id,
        peer_instance_id=peer_instance,
        current_policy_revision=7,
        materializer=materializer,
        adapter_request=adapter_req,
        peer_adapter=adapter,
        profile=_ROUTED_PROFILE,
        limits=limits,
        workspace_roots={"ws-1": workspace_root},
        content_providers={"art-1": lambda: content},
        completion_contract=contract,
        heartbeat_timeout_ms=10000,
        service=dispatch,
    )

    assert res.attempt.state is RequestState.FAILED_PRE_DISPATCH
    assert res.request.terminal_error_code is ErrorCode.ARTIFACT_RESERVATION_FAILED
    assert res.process_outcome is None


def test_dispatch_and_execute_nonzero_exit(tmp_path: Path, store: SqliteStateStore) -> None:
    """Trivial subprocess exiting nonzero -> completion assessment reflects failure."""
    workflows, dispatch = _workflows(store)
    cmd_id, cap_lease_id, peer_instance = _admit_and_prepare(workflows, _envelope())

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
    limits = TransportLimits(process_timeout_ms=5000, silence_timeout_ms=5000, max_output_bytes=65536)
    adapter = FakePeerAdapter(exit_code=1)

    res = workflows.dispatch_and_execute(
        cmd_id,
        capability_lease_id=cap_lease_id,
        peer_instance_id=peer_instance,
        current_policy_revision=7,
        materializer=materializer,
        adapter_request=adapter_req,
        peer_adapter=adapter,
        profile=_ROUTED_PROFILE,
        limits=limits,
        workspace_roots={"ws-1": workspace_root},
        content_providers={},
        completion_contract=contract,
        heartbeat_timeout_ms=10000,
    )

    assert res.process_outcome is not None
    assert res.process_outcome.execution_outcome.exit_code == 1
    assert res.completion_assessment is not None
    assert res.completion_assessment.state is CompletionAssessmentState.NOT_APPLICABLE
    assert res.attempt.result is not None
    assert res.attempt.result.terminal_classification is TerminalClassification.EXIT_NON_ZERO
    assert res.attempt.result.failure_classification is not None
    assert res.attempt.result.failure_classification.code is ErrorCode.INTERNAL_ERROR


def test_dispatch_and_execute_nonzero_exit_with_vendor_error(tmp_path: Path, store: SqliteStateStore) -> None:
    """Trivial subprocess exiting nonzero with VENDOR_ERROR yields classified failure."""
    from peerhub.adapters.contract import OutputDecoder, DecodedOutput, DecoderEvent, DecoderEventKind, InvocationPlan
    from peerhub.builtins.fake_adapter import FakeOutputDecoder

    class VendorErrorAdapter(FakePeerAdapter):
        def new_decoder(self, plan: InvocationPlan) -> OutputDecoder:
            class ErrorDecoder(FakeOutputDecoder):
                def finalize(self) -> DecodedOutput:
                    out = super().finalize()
                    ev = DecoderEvent(
                        kind=DecoderEventKind.VENDOR_ERROR,
                        payload={"normalized_kind": "session_invalid", "evidence_source": "known_terminal_pattern"}
                    )
                    return DecodedOutput(
                        canonical_text=out.canonical_text,
                        canonical_lines=out.canonical_lines,
                        events=out.events + (ev,)
                    )
            return ErrorDecoder()

    workflows, dispatch = _workflows(store)
    cmd_id, cap_lease_id, peer_instance = _admit_and_prepare(workflows, _envelope())

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
    limits = TransportLimits(process_timeout_ms=5000, silence_timeout_ms=5000, max_output_bytes=65536)
    adapter = VendorErrorAdapter(exit_code=1)

    res = workflows.dispatch_and_execute(
        cmd_id,
        capability_lease_id=cap_lease_id,
        peer_instance_id=peer_instance,
        current_policy_revision=7,
        materializer=materializer,
        adapter_request=adapter_req,
        peer_adapter=adapter,
        profile=_ROUTED_PROFILE,
        limits=limits,
        workspace_roots={"ws-1": workspace_root},
        content_providers={},
        completion_contract=contract,
        heartbeat_timeout_ms=10000,
    )

    assert res.process_outcome is not None
    assert res.process_outcome.execution_outcome.exit_code == 1
    assert res.attempt.result is not None
    assert res.attempt.result.failure_classification is not None
    assert res.attempt.result.failure_classification.code is ErrorCode.SESSION_INVALID


def test_dispatch_and_execute_success_clears_classifications(tmp_path: Path, store: SqliteStateStore) -> None:
    """Successful attempt results in None for both classifications."""
    workflows, dispatch = _workflows(store)
    cmd_id, cap_lease_id, peer_instance = _admit_and_prepare(workflows, _envelope())

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
    limits = TransportLimits(process_timeout_ms=5000, silence_timeout_ms=5000, max_output_bytes=65536)
    adapter = FakePeerAdapter(exit_code=0)

    res = workflows.dispatch_and_execute(
        cmd_id,
        capability_lease_id=cap_lease_id,
        peer_instance_id=peer_instance,
        current_policy_revision=7,
        materializer=materializer,
        adapter_request=adapter_req,
        peer_adapter=adapter,
        profile=_ROUTED_PROFILE,
        limits=limits,
        workspace_roots={"ws-1": workspace_root},
        content_providers={},
        completion_contract=contract,
        heartbeat_timeout_ms=10000,
    )

    assert res.process_outcome is not None
    assert res.process_outcome.execution_outcome.exit_code == 0
    assert res.attempt.result is not None
    assert res.attempt.result.terminal_classification is None
    assert res.attempt.result.failure_classification is None


# --- Enforcement-gate security properties (errata 7.2 point 3 / 7.4) -------
#
# The tests below assert that require_dispatch_capability() actually BLOCKS,
# not merely that it raises: a gate that raised after planning or spawning
# would still have leaked authority to a subprocess.  They therefore spy on
# plan_invocation() and run_process() and assert neither was ever entered.


class _RecordingFakePeerAdapter(FakePeerAdapter):
    """``FakePeerAdapter`` with a chosen ``peer_kind`` that records planning.

    The stock fake declares ``peer_kind="fake"``.  The gate compares the
    adapter's own declared kind against the machine-resolved kind for the
    admitted instance, so a test that wants to reach the enforcement check
    (rather than stopping at the kind mismatch) has to declare the same kind
    the evidence provider resolves.
    """

    def __init__(self, *, peer_kind: str, **kwargs: object) -> None:
        super().__init__(**kwargs)  # pyright: ignore[reportArgumentType]
        self.descriptor = replace(_FAKE_DESCRIPTOR, peer_kind=peer_kind)
        self.plan_invocation_calls = 0
        self.recorded_session = None

    def plan_invocation(
        self,
        request: AdapterRequest,
        profile: ProfileDescriptor,
        session,  # pyright: ignore[reportMissingParameterType]
        limits: TransportLimits,
    ):
        self.plan_invocation_calls += 1
        self.recorded_session = session
        return super().plan_invocation(request, profile, session, limits)  # pyright: ignore[reportUnknownArgumentType]


# Admission-time evidence stating a measured CONFINED ceiling for instance
# "ag" under peer kind "ag".  This exists ONLY so a WORKTREE_WRITE lease can
# be minted at all: without it, admission itself fails closed and the dispatch
# gate under test is never reached.  source_tag is "controlled_fake", never
# "empirical_probe" -- no real peer has DIR-004-qualifying enforcement
# evidence today, and this fixture must never be mistaken for one.
_MEASURED_AG_EVIDENCE = StaticPeerEnforcementEvidenceProvider(
    {
        "ag": PeerEnforcementEvidence(
            peer_instance_id="ag",
            peer_kind="ag",
            enforcement_ceiling=EnforcementLevel.CONFINED,
            source_tag="controlled_fake",
        )
    }
)


class _MutableRevisionCapabilityPolicy(StaticCapabilityPolicy):
    """Test policy whose machine-owned current revision can advance."""

    def __init__(self, current_revision: RevisionValue = 7) -> None:
        super().__init__()
        self.current_revision = current_revision
        self.revalidation_calls: list[RevisionValue] = []

    def revalidate(
        self,
        binding: ValidatedCapabilityBinding,
        *,
        current_policy_revision: RevisionValue,
        now: int,
    ) -> None:
        self.revalidation_calls.append(current_policy_revision)
        if current_policy_revision != self.current_revision:
            raise CapabilityLeaseViolation(
                "current policy revision changed after pre-plan validation"
            )
        super().revalidate(
            binding,
            current_policy_revision=current_policy_revision,
            now=now,
        )


def _invocation_receipt(
    validated: ValidatedCapabilityLease,
) -> InvocationEnforcementReceipt:
    return InvocationEnforcementReceipt(
        capability_lease_id=validated.capability_lease_id,
        command_id=validated.command_id,
        realized_enforcement=validated.satisfied_floor,
        controls_description="controlled fake",
        evidence_source_tag="controlled_fake",
        plan_digest="sha256:" + "d" * 64,
    )


def _command_events(
    store: SqliteStateStore,
    command_id: str,
) -> tuple[OutboxEvent, ...]:
    with store.unit_of_work() as unit:
        return unit.list_outbox_events_by_command(command_id)


def test_dispatch_intent_revalidates_policy_after_pre_plan_gate(
    store: SqliteStateStore,
) -> None:
    """A policy change after planning cannot cross the intent commit."""

    policy = _MutableRevisionCapabilityPolicy()
    workflows, dispatch = _workflows(
        store,
        capability_policy=policy,
    )
    command_id, capability_lease_id, peer_instance = _admit_and_prepare(
        workflows,
        _envelope(),
    )
    validated = dispatch.require_dispatch_capability(
        command_id,
        capability_lease_id=capability_lease_id,
        peer_instance_id=peer_instance,
        adapter_peer_kind="fake",
        profile=_ROUTED_PROFILE,
        current_policy_revision=7,
    )
    receipt = _invocation_receipt(validated)
    attempt = dispatch.create_attempt(command_id)
    before_request, before_attempt = dispatch.get_request_and_attempt(
        command_id,
        attempt.attempt_id,
    )
    before_lease = dispatch.get_lease(before_request.lease_id)
    before_events = _command_events(store, str(command_id))

    policy.current_revision = 8
    with pytest.raises(CapabilityLeaseViolation) as exc_info:
        dispatch.record_dispatch_intent(
            command_id,
            attempt.attempt_id,
            validated_lease=validated,
            enforcement_receipt=receipt,
        )

    assert exc_info.value.invariant == (
        "current policy revision changed after pre-plan validation"
    )
    after_request, after_attempt = dispatch.get_request_and_attempt(
        command_id,
        attempt.attempt_id,
    )
    after_lease = dispatch.get_lease(after_request.lease_id)
    after_events = _command_events(store, str(command_id))
    assert after_request == before_request
    assert after_request.state is RequestState.PREPARED
    assert after_attempt == before_attempt
    assert after_attempt.state is RequestState.PREPARED
    assert after_lease == before_lease
    assert after_events == before_events
    assert all(
        event.event_kind != "DISPATCH_INTENT"
        for event in after_events
    )
    assert policy.revalidation_calls == [7, 7]


def test_dispatch_intent_post_plan_revalidation_happy_path(
    store: SqliteStateStore,
) -> None:
    """An unchanged policy permits the normal durable intent transition."""

    policy = _MutableRevisionCapabilityPolicy()
    workflows, dispatch = _workflows(
        store,
        capability_policy=policy,
    )
    command_id, capability_lease_id, peer_instance = _admit_and_prepare(
        workflows,
        _envelope(),
    )
    validated = dispatch.require_dispatch_capability(
        command_id,
        capability_lease_id=capability_lease_id,
        peer_instance_id=peer_instance,
        adapter_peer_kind="fake",
        profile=_ROUTED_PROFILE,
        current_policy_revision=7,
    )
    receipt = _invocation_receipt(validated)
    attempt = dispatch.create_attempt(command_id)
    before_events = _command_events(store, str(command_id))

    request, persisted_attempt, lease = dispatch.record_dispatch_intent(
        command_id,
        attempt.attempt_id,
        validated_lease=validated,
        enforcement_receipt=receipt,
    )

    after_events = _command_events(store, str(command_id))
    assert request.state is RequestState.DISPATCH_INTENT
    assert persisted_attempt.state is RequestState.DISPATCH_INTENT
    assert lease.fence.attempt_id == attempt.attempt_id
    assert len(after_events) == len(before_events) + 1
    assert after_events[-1].event_kind == "DISPATCH_INTENT"
    assert policy.revalidation_calls == [7, 7]


def _count_attempts(database_path: Path) -> int:
    connection = sqlite3.connect(database_path)
    try:
        row = connection.execute(
            "SELECT COUNT(*) FROM dispatch_attempts"
        ).fetchone()
    finally:
        connection.close()
    return int(row[0])


def test_mutating_dispatch_without_enforcement_evidence_is_denied_before_spawn(
    tmp_path: Path,
    store: SqliteStateStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A WORKTREE_WRITE dispatch to an unenforced peer never reaches planning.

    The lease is minted under evidence that claims a measured CONFINED
    ceiling, then dispatched through a service carrying the SHIPPED
    ``default_enforcement_evidence_provider()`` -- for which every built-in
    peer resolves to ``enforcement_ceiling=None``.  That is the production
    configuration, and it is also the revocation shape errata 7.2.3 exists
    for: the gate must re-derive the floor from machine-owned evidence at
    dispatch time rather than trusting the grant admission already made.
    """

    adapter = _RecordingFakePeerAdapter(peer_kind="ag", stdout="MUST NOT RUN\n")
    workflows, _ = _workflows(
        store,
        peer_adapter=adapter,
        enforcement_evidence=_MEASURED_AG_EVIDENCE,
    )
    cmd_id, cap_lease_id, peer_instance = _admit_and_prepare(
        workflows,
        _envelope(),
        required_capability_tier=CapabilityTier.WORKTREE_WRITE,
    )

    shipped_dispatch = DispatchService(
        store,
        ids=SequentialIdSource(),
        clock=DeterministicClock(start=400),
        enforcement_evidence=default_enforcement_evidence_provider(),
    )

    run_process_calls: list[object] = []

    def _spy_run_process(*args: object, **kwargs: object) -> object:
        # Recorded BEFORE raising: dispatch_and_execute wraps run_process in
        # `except Exception`, so a bare raise here would be swallowed and the
        # test would pass vacuously.
        run_process_calls.append(kwargs)
        raise AssertionError("run_process must not be reached by a denied dispatch")

    monkeypatch.setattr(workflows_module, "run_process", _spy_run_process)

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
    limits = TransportLimits(
        process_timeout_ms=5000,
        silence_timeout_ms=5000,
        max_output_bytes=65536,
    )

    with pytest.raises(CapabilityLeaseViolation) as exc_info:
        workflows.dispatch_and_execute(
            cmd_id,
            capability_lease_id=cap_lease_id,
            peer_instance_id=peer_instance,
            current_policy_revision=7,
            materializer=materializer,
            adapter_request=adapter_req,
            peer_adapter=adapter,
            profile=_ROUTED_PROFILE,
            limits=limits,
            workspace_roots={"ws-1": workspace_root},
            content_providers={},
            completion_contract=contract,
            heartbeat_timeout_ms=10000,
            service=shipped_dispatch,
        )

    assert exc_info.value.invariant == (
        "selected adapter has no measured enforcement evidence for the "
        "mandatory enforcement floor"
    )
    # The security property itself: nothing downstream of the gate ran.
    assert adapter.plan_invocation_calls == 0
    assert run_process_calls == []
    assert _count_attempts(tmp_path / "workflows_exec.sqlite3") == 0
    still_prepared = shipped_dispatch.get_request(cmd_id)
    assert still_prepared is not None
    assert still_prepared.state is RequestState.PREPARED


def test_read_only_dispatch_to_the_same_unenforced_peer_is_authorized(
    tmp_path: Path,
    store: SqliteStateStore,
) -> None:
    """READ_ONLY needs no enforcement measurement and reaches execution.

    Same peer, same shipped evidence provider (``enforcement_ceiling=None``)
    on BOTH admission and dispatch -- only the tier differs from the denial
    test above.  The mandatory floor for a READ_ONLY dispatch is ADVISORY,
    which errata 7.4 says is satisfiable without any measurement.
    """

    adapter = _RecordingFakePeerAdapter(peer_kind="ag", stdout="read-only-ok\n")
    workflows, dispatch = _workflows(
        store,
        peer_adapter=adapter,
        enforcement_evidence=default_enforcement_evidence_provider(),
    )
    cmd_id, cap_lease_id, peer_instance = _admit_and_prepare(
        workflows,
        _envelope(),
        required_capability_tier=CapabilityTier.READ_ONLY,
    )

    validated = dispatch.require_dispatch_capability(
        cmd_id,
        capability_lease_id=cap_lease_id,
        peer_instance_id=peer_instance,
        adapter_peer_kind="ag",
        profile=_ROUTED_PROFILE,
        current_policy_revision=7,
    )
    assert validated.capability_lease_id == cap_lease_id
    assert validated.authorized_tier is CapabilityTier.READ_ONLY
    assert validated.satisfied_floor is EnforcementLevel.ADVISORY
    assert validated.minimum_enforcement is EnforcementLevel.ADVISORY

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
    limits = TransportLimits(
        process_timeout_ms=5000,
        silence_timeout_ms=5000,
        max_output_bytes=65536,
    )

    res = workflows.dispatch_and_execute(
        cmd_id,
        capability_lease_id=cap_lease_id,
        peer_instance_id=peer_instance,
        current_policy_revision=7,
        materializer=materializer,
        adapter_request=adapter_req,
        peer_adapter=adapter,
        profile=_ROUTED_PROFILE,
        limits=limits,
        workspace_roots={"ws-1": workspace_root},
        content_providers={},
        completion_contract=contract,
        heartbeat_timeout_ms=10000,
    )

    assert adapter.plan_invocation_calls == 1
    assert res.request.state is RequestState.SUCCEEDED_VERIFIED
    assert res.process_outcome is not None
    assert res.process_outcome.execution_outcome.exit_code == 0


def test_mutating_admission_to_an_unenforced_peer_never_mints_a_lease(
    store: SqliteStateStore,
) -> None:
    """Defense in depth: the shipped provider also fails closed at admission.

    The gate above is the last line; this is the first.  Under the shipped
    evidence provider a WORKTREE_WRITE admission cannot produce a capability
    lease at all, so there is nothing for a later dispatch to present.
    """

    adapter = _RecordingFakePeerAdapter(peer_kind="ag", stdout="MUST NOT RUN\n")
    workflows, _ = _workflows(
        store,
        peer_adapter=adapter,
        enforcement_evidence=default_enforcement_evidence_provider(),
    )

    with pytest.raises(CapabilityLeaseViolation) as exc_info:
        _admit_and_prepare(
            workflows,
            _envelope(),
            required_capability_tier=CapabilityTier.WORKTREE_WRITE,
        )

    assert exc_info.value.invariant == (
        "selected adapter has no measured enforcement evidence for the "
        "mandatory enforcement floor"
    )


def test_dispatch_and_execute_rejects_session_without_capability(
    tmp_path: Path, store: SqliteStateStore
) -> None:
    adapter = _RecordingFakePeerAdapter(peer_kind="ag")
    workflows, dispatch = _workflows(
        store,
        peer_adapter=adapter,
        enforcement_evidence=default_enforcement_evidence_provider(),
    )

    cmd_id, cap_lease_id, peer_instance = _admit_and_prepare(
        workflows,
        _envelope(),
        required_capability_tier=CapabilityTier.READ_ONLY,
    )

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
    limits = TransportLimits(
        process_timeout_ms=5000,
        silence_timeout_ms=5000,
        max_output_bytes=65536,
    )

    session_hint = SessionHint(
        external_session_id="sess-123",
        adapter_fingerprint="test-fingerprint",
        session_generation=1,
    )

    with pytest.raises(UnsupportedCapabilityError) as exc_info:
        workflows.dispatch_and_execute(
            cmd_id,
            capability_lease_id=cap_lease_id,
            peer_instance_id=peer_instance,
            current_policy_revision=7,
            materializer=materializer,
            adapter_request=adapter_req,
            peer_adapter=adapter,
            profile=_ROUTED_PROFILE,
            limits=limits,
            workspace_roots={"ws-1": workspace_root},
            content_providers={},
            completion_contract=contract,
            heartbeat_timeout_ms=10000,
            session=session_hint,
        )

    assert exc_info.value.adapter_id == "fake-peer"
    assert exc_info.value.capability == Capability.SESSION
    assert adapter.plan_invocation_calls == 0


def test_dispatch_and_execute_accepts_session_with_capability(
    tmp_path: Path, store: SqliteStateStore
) -> None:
    class CapableFakePeerAdapter(_RecordingFakePeerAdapter):
        def __init__(self, *, peer_kind: str, **kwargs: object) -> None:
            super().__init__(peer_kind=peer_kind, **kwargs)
            self.descriptor = replace(self.descriptor, capabilities=frozenset({Capability.SESSION}))

    adapter = CapableFakePeerAdapter(peer_kind="ag", stdout="mocked\n")
    workflows, dispatch = _workflows(
        store,
        peer_adapter=adapter,
        enforcement_evidence=default_enforcement_evidence_provider(),
    )

    cmd_id, cap_lease_id, peer_instance = _admit_and_prepare(
        workflows,
        _envelope(),
        required_capability_tier=CapabilityTier.READ_ONLY,
    )

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
    limits = TransportLimits(
        process_timeout_ms=5000,
        silence_timeout_ms=5000,
        max_output_bytes=65536,
    )

    session_hint = SessionHint(
        external_session_id="sess-123",
        adapter_fingerprint="test-fingerprint",
        session_generation=1,
    )

    res = workflows.dispatch_and_execute(
        cmd_id,
        capability_lease_id=cap_lease_id,
        peer_instance_id=peer_instance,
        current_policy_revision=7,
        materializer=materializer,
        adapter_request=adapter_req,
        peer_adapter=adapter,
        profile=_ROUTED_PROFILE,
        limits=limits,
        workspace_roots={"ws-1": workspace_root},
        content_providers={},
        completion_contract=contract,
        heartbeat_timeout_ms=10000,
        session=session_hint,
    )

    assert adapter.plan_invocation_calls == 1
    assert adapter.recorded_session is session_hint
    assert res.process_outcome is not None
    assert res.process_outcome.execution_outcome.exit_code == 0
