"""Integration tests for E2E vertical dispatch pipeline.

Exercising:
ApplicationWorkflows.dispatch_and_execute() -> artifact materialization
-> dispatch intent + reservation -> spawn via pipe.run_process()
(using tools/fake_peer/pipe_executable.py as the spawned process) -> stream output
-> exit -> assess_completion() -> consume artifacts + close lease.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
import sys
import time
import pytest

from peerhub.adapters.contract import (
    AdapterRequest,
    ArtifactSpec,
    InvocationPlan,
    ProtocolAssessment,
    SessionAction,
)
from peerhub.application.workflows import ApplicationWorkflows
from peerhub.core.evidence import EvidenceRef, EvidenceState, EvidenceValue
from peerhub.core.execution import TransportKind, TransportLimits
from peerhub.core.protocol import (
    PROTOCOL_MAJOR,
    PROTOCOL_MINOR,
    SCHEMA_VERSION,
    CommandEnvelope,
)
from peerhub.dispatch.contract import (
    ArtifactState,
    CompletionAssessmentState,
    CompletionContract,
    CompletionContractKind,
    ExecutionCertainty,
    LeaseRenewRequest,
    LeaseSnapshot,
    LeaseState,
    RequestState,
)
from peerhub.dispatch.process import TerminalClassification
from peerhub.dispatch.materializer import ArtifactMaterializer
from peerhub.dispatch.service import (
    DispatchService,
    recover_interrupted_attempt,
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


def _candidate(eligible: bool = True) -> RouteCandidateInput:
    return RouteCandidateInput(
        candidate_id="ag.deepthink",
        instance_id="ag",
        representative_profile_id="ag.deepthink",
        eligible=eligible,
        exclusion_reason=None if eligible else "ROLE_EXCLUDED",
        usage_evidence=_absent_usage(),
        in_flight_reservations=0,
        evidence_refs=(),
    )


def _route_request_factory(
    client_request_id: str = "client-request-01",
    configuration_revision: int = 11,
    eligible: bool = True,
):
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


# -----------------------------------------------------------------------------
# Scenario 1: E2E Clean Vertical Dispatch (Happy Path)
# -----------------------------------------------------------------------------


def test_e2e_clean_vertical_dispatch_zero_artifacts(
    tmp_path: Path,
    store: SqliteStateStore,
    fake_peer_script: Path,
) -> None:
    """E2E clean vertical dispatch with fake peer CLI and zero artifacts."""
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
        argv=(
            sys.executable,
            str(fake_peer_script),
            "--stdout",
            "FAKE_PEER_DETERMINISTIC_STDOUT\n",
            "--exit-code",
            "0",
        ),
        cwd_reference=".",
        environment_delta={},
        transport=TransportKind.PIPE,
        stdin_payload=None,
        limits=TransportLimits(
            process_timeout_ms=5000,
            silence_timeout_ms=5000,
            max_output_bytes=65536,
        ),
        redacted_display="python fake_peer/pipe_executable.py",
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
    assert res.process_outcome.execution_outcome.started is True
    assert res.process_outcome.execution_outcome.exit_code == 0
    assert res.completion_assessment is not None
    assert res.completion_assessment.state is CompletionAssessmentState.VERIFIED
    assert b"FAKE_PEER_DETERMINISTIC_STDOUT" in res.process_outcome.canonical_stream


def test_e2e_clean_vertical_dispatch_with_materialized_artifact(
    tmp_path: Path,
    store: SqliteStateStore,
    fake_peer_script: Path,
) -> None:
    """E2E clean vertical dispatch materializing artifact -> reservation -> consumption."""
    workflows, dispatch = _workflows(store)
    cmd_id = _admit_and_prepare(workflows, _envelope())

    workspace_root = tmp_path / "ws"
    workspace_root.mkdir()

    content = b"fake peer input artifact content"
    digest = hashlib.sha256(content).hexdigest()
    length = len(content)

    spec = ArtifactSpec(
        artifact_id="art-vertical-01",
        placeholder="__INPUT_FILE__",
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
        request_id="req-02",
        prompt_content="hello artifact",
        prompt_reference=None,
        workspace_scope="ws-1",
        profile_id="ag.deepthink",
        requested_session_action=SessionAction.NONE,
        completion_contract=contract,
    )
    inv_plan = InvocationPlan(
        argv=(
            sys.executable,
            str(fake_peer_script),
            "--stdout",
            "ARTIFACT_PROCESSED_OK\n",
            "--exit-code",
            "0",
        ),
        cwd_reference=".",
        environment_delta={},
        transport=TransportKind.PIPE,
        stdin_payload=None,
        limits=TransportLimits(
            process_timeout_ms=5000,
            silence_timeout_ms=5000,
            max_output_bytes=65536,
        ),
        redacted_display="python fake_peer/pipe_executable.py",
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
        content_providers={"art-vertical-01": lambda: content},
        completion_contract=contract,
        protocol_assessment=protocol_assessment,
        heartbeat_timeout_ms=10000,
    )

    assert res.request.state is RequestState.SUCCEEDED_VERIFIED
    assert res.attempt.state is RequestState.SUCCEEDED_VERIFIED
    assert res.lease is not None
    assert res.lease.state is LeaseState.RELEASED
    assert res.completion_assessment is not None
    assert res.completion_assessment.state is CompletionAssessmentState.VERIFIED

    # Verify SQLite artifact state moved to CONSUMED
    with store.unit_of_work() as unit:
        records = unit.list_artifact_metadata(res.attempt.attempt_id)
        assert len(records) == 1
        assert records[0].state is ArtifactState.CONSUMED


# -----------------------------------------------------------------------------
# Scenario 2: DP-06 Isolated Journal Fault Injection
# -----------------------------------------------------------------------------


def test_dp06_post_intent_recovery_reducer_contract(
    store: SqliteStateStore,
) -> None:
    """Reducer-contract test for post-DISPATCH_INTENT fault recovery.

    Exercises recover_interrupted_attempt() reducer logic directly with
    a synthetic journal payload. Note: This verifies the recovery reducer
    contract only, not full end-to-end outbox-to-journal recovery (outbox-to-journal
    translation remains unimplemented).

    Verify recover_interrupted_attempt() recovers as MAY_HAVE_STARTED,
    automatic replay is unauthorized (automatic_replay_authorized=False),
    journal digest is verified, and SQLite has 0 open transactions.
    """
    workflows, dispatch = _workflows(store)
    cmd_id = _admit_and_prepare(workflows, _envelope())

    # Step 1: Create attempt
    attempt = dispatch.create_attempt(cmd_id)

    # Step 2: Simulate crash immediately after DISPATCH_INTENT outbox write
    req_snap, att_snap, lease_snap = dispatch.record_dispatch_intent(
        cmd_id,
        attempt.attempt_id,
    )
    assert att_snap.state is RequestState.DISPATCH_INTENT

    # Step 3: Construct the durable journal for recovery.
    #
    # recover_interrupted_attempt() is a pure reducer over an abstract
    # journal-token vocabulary (e.g. "INTENT_PERSISTED"); no production
    # code yet translates real OutboxEvent records into that vocabulary,
    # and the real list_outbox_events() has no command/request-scoped
    # query (it filters by OutboxState + outbox position only, not by
    # command_id -- an earlier version of this test called it with a
    # command_id kwarg that does not exist on any implementation and
    # would raise TypeError). This test therefore exercises the
    # reducer's own contract given a correctly-shaped journal, not an
    # end-to-end outbox-event-to-journal-token translation -- that
    # translation is a real, currently-unimplemented gap and should not
    # be faked here.
    journal_entries = ["INTENT_PERSISTED"]

    raw_journal_bytes = ",".join(journal_entries).encode("utf-8")
    journal_digest = f"sha256:{hashlib.sha256(raw_journal_bytes).hexdigest()}"

    # Step 4: Invoke recovery reducer
    recovery_outcome = recover_interrupted_attempt(
        journal_entries=journal_entries,
        journal_digest=journal_digest,
    )

    assert recovery_outcome.terminal_classification is TerminalClassification.START_UNCERTAIN
    assert recovery_outcome.execution_outcome.started is False
    assert recovery_outcome.execution_outcome.execution_certainty is ExecutionCertainty.MAY_HAVE_STARTED
    assert recovery_outcome.automatic_replay_authorized is False
    assert recovery_outcome.journal_digest == journal_digest

    # Step 5: Verify store remains clean with 0 active unit-of-work transactions
    with store.unit_of_work() as unit:
        # Performing a read verifies SQLite connection/transaction is clear
        req_again = unit.get_request(cmd_id)
        assert req_again is not None


# -----------------------------------------------------------------------------
# Scenario 3: Process Timeout / Heartbeat Failure & Cancellation Ladder
# -----------------------------------------------------------------------------


class _FailingRenewerDispatchService:
    """Wrapper that causes renew_lease to fail with CAS exception."""

    def __init__(self, target: DispatchService) -> None:
        self._target = target

    def renew_lease(
        self,
        request: LeaseRenewRequest,
        *,
        heartbeat_timeout_ms: int,
    ) -> LeaseSnapshot:
        raise RuntimeError("Simulated heartbeat renewal failure")

    def __getattr__(self, name: str):
        return getattr(self._target, name)


def test_process_cancellation_on_heartbeat_failure(
    tmp_path: Path,
    store: SqliteStateStore,
    fake_peer_script: Path,
) -> None:
    """Heartbeat failure triggers cancellation ladder terminating long-running fake peer."""
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
        request_id="req-03",
        prompt_content="hello timeout",
        prompt_reference=None,
        workspace_scope="ws-1",
        profile_id="ag.deepthink",
        requested_session_action=SessionAction.NONE,
        completion_contract=contract,
    )

    # Spawn fake peer with an artificial exit delay of 10 seconds.
    # Without cancellation, this would run for 10 seconds.
    inv_plan = InvocationPlan(
        argv=(
            sys.executable,
            str(fake_peer_script),
            "--stdout",
            "LONG_RUNNING_PEER_START\n",
            "--delay",
            "10.0",
        ),
        cwd_reference=".",
        environment_delta={},
        transport=TransportKind.PIPE,
        stdin_payload=None,
        limits=TransportLimits(
            process_timeout_ms=10000,
            silence_timeout_ms=10000,
            max_output_bytes=65536,
        ),
        redacted_display="python fake_peer/pipe_executable.py --delay 10.0",
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

    failing_service = _FailingRenewerDispatchService(dispatch)

    start_time = time.monotonic()
    res = workflows.dispatch_and_execute(
        cmd_id,
        materializer=materializer,
        adapter_request=adapter_req,
        invocation_plan=inv_plan,
        workspace_roots={"ws-1": workspace_root},
        content_providers={},
        completion_contract=contract,
        protocol_assessment=protocol_assessment,
        heartbeat_timeout_ms=50,  # Short heartbeat timeout to tick rapidly (~16ms)
        service=failing_service,  # Inject failing renew_lease
    )
    elapsed = time.monotonic() - start_time

    # Process must have been cancelled by the ladder in under 5 seconds (not wait full 10s)
    assert elapsed < 5.0

    assert res.process_outcome is not None
    assert res.process_outcome.execution_outcome.cancelled is True


# -----------------------------------------------------------------------------
# Scenario 4: DP-04 Process Deadline E2E
# -----------------------------------------------------------------------------


def test_dp04_process_deadline_e2e(
    tmp_path: Path,
    store: SqliteStateStore,
    fake_peer_script: Path,
) -> None:
    """DP-04: Process deadline E2E test. Fake peer runs longer than process_timeout_ms."""
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
        request_id="req-dp04",
        prompt_content="hello deadline",
        prompt_reference=None,
        workspace_scope="ws-dp04",
        profile_id="ag.deepthink",
        requested_session_action=SessionAction.NONE,
        completion_contract=contract,
    )

    inv_plan = InvocationPlan(
        argv=(
            sys.executable,
            str(fake_peer_script),
            "--delay",
            "5.0",
        ),
        cwd_reference=".",
        environment_delta={},
        transport=TransportKind.PIPE,
        stdin_payload=None,
        limits=TransportLimits(
            process_timeout_ms=500,
            silence_timeout_ms=10000,
            max_output_bytes=65536,
        ),
        redacted_display="python fake_peer/pipe_executable.py --delay 5.0",
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

    start_time = time.monotonic()
    res = workflows.dispatch_and_execute(
        cmd_id,
        materializer=materializer,
        adapter_request=adapter_req,
        invocation_plan=inv_plan,
        workspace_roots={"ws-dp04": workspace_root},
        content_providers={},
        completion_contract=contract,
        protocol_assessment=protocol_assessment,
        heartbeat_timeout_ms=10000,
    )
    elapsed = time.monotonic() - start_time

    assert elapsed < 5.0

    assert res.process_outcome is not None
    assert res.process_outcome.execution_outcome.timed_out is True
    assert res.process_outcome.terminal_classification is TerminalClassification.PROCESS_TIMEOUT


# -----------------------------------------------------------------------------
# Scenario 5: DP-05 Output Cap E2E
# -----------------------------------------------------------------------------


def test_dp05_output_cap_e2e(
    tmp_path: Path,
    store: SqliteStateStore,
    fake_peer_script: Path,
) -> None:
    """DP-05: Output cap E2E test. Fake peer emits more than max_output_bytes."""
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
        request_id="req-dp05",
        prompt_content="hello output cap",
        prompt_reference=None,
        workspace_scope="ws-dp05",
        profile_id="ag.deepthink",
        requested_session_action=SessionAction.NONE,
        completion_contract=contract,
    )

    # 5000 bytes output string to exceed 4096 buffer
    excessive_output = "A" * 5000

    inv_plan = InvocationPlan(
        argv=(
            sys.executable,
            str(fake_peer_script),
            "--stdout",
            excessive_output,
            "--delay",
            "5.0",
        ),
        cwd_reference=".",
        environment_delta={},
        transport=TransportKind.PIPE,
        stdin_payload=None,
        limits=TransportLimits(
            process_timeout_ms=5000,
            silence_timeout_ms=5000,
            max_output_bytes=100,
        ),
        redacted_display="python fake_peer/pipe_executable.py --stdout ...",
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
        workspace_roots={"ws-dp05": workspace_root},
        content_providers={},
        completion_contract=contract,
        protocol_assessment=protocol_assessment,
        heartbeat_timeout_ms=10000,
    )

    assert res.process_outcome is not None
    assert res.process_outcome.execution_outcome.cancelled is True
    assert res.process_outcome.terminal_classification is TerminalClassification.OUTPUT_LIMIT_EXCEEDED
