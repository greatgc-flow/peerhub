"""Cross-feature Slice 4 admission and dispatch workflows."""

from __future__ import annotations



from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path
import subprocess
from typing import Protocol, TypeAlias, assert_never

from peerhub.adapters.contract import (
    AdapterRequest,
    Capability,
    PeerAdapter,
    ProfileDescriptor,
    DecodedOutput,
    DecoderEvent,
    OutputChannel,
    OutputDecoder,
    SessionHint,
)
from peerhub.core.errors import (
    ConcurrentAttemptClaimError,
    InvalidMutationError,
    RecordNotFoundError,
    UnsupportedCapabilityError,
)
from peerhub.core.identity import AuthenticatedSubject
from peerhub.core.protocol import (
    CommandEnvelope,
    CommandID,
    ErrorCode,
    RevisionValue,
)
from peerhub.core.execution import (
    ProcessTerminalEvidence,
    TransportLimits,
)
from peerhub.dispatch.artifacts import (
    generate_materialization_manifest,
    resolve_workspace_paths,
)
from peerhub.dispatch.completion import assess_completion
from peerhub.dispatch.capability import (
    CapabilityLease,
    CapabilityTier,
    InvocationEnforcementReceipt,
)
from peerhub.dispatch.contract import (
    AdmissionReceipt,
    ArtifactManifestRecord,
    ArtifactMetadata,
    ArtifactState,
    AskResult,
    AttemptSnapshot,
    CompletionAssessment,
    CompletionContract,
    LeaseSnapshot,
    ProcessBirthIdentity,
    RequestSnapshot,
    RequestState,
    RetryLoopState,
    SessionBindingKey,
)
from peerhub.dispatch.heartbeat import HeartbeatWorker, HeartbeatFailure
from peerhub.dispatch.materializer import (
    ArtifactMaterializer,
    MaterializationItemRequest,
    MaterializationResult,
    MaterializationSource,
    MaterializationStatus,
    compute_manifest_digest,
)
from peerhub.dispatch.pipe import (
    PipeOutputChannel,
    PipeProcessChunk,
    PipeRunnerConfig,
    run_process,
)
from peerhub.dispatch.process import (
    ProcessSupervisionOutcome,
    ProcessSupervisor,
)
from peerhub.dispatch.model import classify_attempt_failure
from peerhub.dispatch.service import DispatchService
from peerhub.dispatch.retry_authorization import (
    FAILED_TARGET_EXCLUDED_BY_RETRY,
    FailoverRoute,
    RetryAuthorizationBundle,
    RetryRouteIntent,
    SameTargetRoute,
)
from peerhub.application.retry import (
    AttemptDispatchPlan,
    AttemptExecutionRecord,
    AuthorizationErrorSignal,
    ConcurrentClaimOutcome,
    MultiAttemptExecutionResult,
    RetryAction,
    RetryConditionEvidenceProvider,
    RetryDecision,
    RetryDecisionReason,
    RetryLoopStopReason,
    RetryTargetResolver,
    ResolvedRetryTarget,
    adjudicate_retry,
    build_retry_dispatch_plan,
    classify_authorization_error,
    classify_concurrent_claim,
    evaluate_retry_condition_evidence,
    read_fresh_retry_condition_evidence,
    transition_retry_route,
)
from peerhub.health.contract import AdmissionSnapshot
from peerhub.health.service import HealthService
from peerhub.routing.contract import (
    RouteCandidateDecision,
    RouteDecision,
    RoutePlanResult,
    RoutePreDispatchResult,
    RouteRequest,
    canonical_route_decision_digest,
)
from peerhub.routing.service import RoutingService
from peerhub.telemetry.projections import TelemetryProjector


@dataclass(frozen=True)
class ExecutionWorkflowResult:
    """Result of dispatch and execution orchestration."""

    request: RequestSnapshot
    attempt: AttemptSnapshot
    lease: LeaseSnapshot | None = None
    materialization_results: tuple[MaterializationResult, ...] | None = None
    process_outcome: ProcessSupervisionOutcome | None = None
    completion_assessment: CompletionAssessment | None = None
    decoded_output: DecodedOutput | None = None


DispatchAdmission: TypeAlias = tuple[
    RequestSnapshot,
    AdmissionReceipt,
    LeaseSnapshot,
    CapabilityLease,
]
RetryAdmission: TypeAlias = RetryAuthorizationBundle


class RouteRequestFactory(Protocol):
    """Compose current injected routing inputs around one health freeze."""

    def __call__(
        self,
        admission_snapshot: AdmissionSnapshot,
        /,
    ) -> RouteRequest:
        """Return the complete immutable routing request."""

        ...


@dataclass(frozen=True)
class AdmissionWorkflowResult:
    """Result of projection, health freeze, routing, and admission.

    ``admission_snapshot`` and ``route`` are ``None`` whenever
    ``dispatch_admission`` is an idempotent replay of a prior attempt
    (peeked before this call ran, or discovered after a concurrent
    admission race) -- the original ``RouteDecision`` used at first
    admission is not retrievable from ``dispatch_admission`` alone,
    since ``RequestSnapshot`` durably records only its digest, never
    its ``decision_id``.
    """

    projected_terminal_events: int
    admission_snapshot: AdmissionSnapshot | None
    route: RoutePlanResult | None
    dispatch_admission: DispatchAdmission | None


@dataclass(frozen=True)
class PreDispatchWorkflowResult:
    """Result of RT-06 validation and preparation or rejection."""

    projected_terminal_events: int
    admission_snapshot: AdmissionSnapshot
    route_recheck: RoutePreDispatchResult
    request: RequestSnapshot


@dataclass(frozen=True)
class RetryWorkflowResult:
    """Result of preparatory projection and atomic retry authorization."""

    projected_terminal_events: int
    admission_snapshot: AdmissionSnapshot
    request: RequestSnapshot
    retry_admission: RetryAdmission


class ApplicationWorkflows:
    """Coordinate Slice 4 feature services without owning their stores."""

    def __init__(
        self,
        *,
        telemetry: TelemetryProjector,
        health: HealthService,
        routing: RoutingService,
        dispatch: DispatchService,
        peer_adapter: PeerAdapter | None = None,
    ) -> None:
        self._telemetry = telemetry
        self._health = health
        self._routing = routing
        self._dispatch = dispatch
        self._peer_adapter = peer_adapter

    @staticmethod
    def _selected_candidate(
        decision: RouteDecision,
    ) -> RouteCandidateDecision:
        selected_id = decision.selected_candidate_id
        if selected_id is None:
            raise InvalidMutationError(
                "route decision has no selected candidate"
            )

        matches = tuple(
            candidate
            for candidate in decision.candidates
            if candidate.candidate_id == selected_id
        )
        if len(matches) != 1:
            raise InvalidMutationError(
                "route decision does not contain exactly one "
                "selected candidate"
            )
        return matches[0]

    @staticmethod
    def _require_route_request(
        factory: RouteRequestFactory,
        admission_snapshot: AdmissionSnapshot,
        *,
        client_request_id: str,
    ) -> RouteRequest:
        request = factory(admission_snapshot)
        if not isinstance(request, RouteRequest):  # pyright: ignore[reportUnnecessaryIsInstance]
            raise InvalidMutationError(
                "route request factory must return RouteRequest"
            )
        if request.admission_snapshot != admission_snapshot:
            raise InvalidMutationError(
                "route request must use the newly frozen "
                "admission snapshot"
            )
        if request.client_request_id != client_request_id:
            raise InvalidMutationError(
                "route request client_request_id differs from "
                "the dispatch request"
            )
        return request

    def _project_freeze_and_build(
        self,
        *,
        client_request_id: str,
        route_request_factory: RouteRequestFactory,
        telemetry_limit: int,
    ) -> tuple[int, AdmissionSnapshot, RouteRequest]:
        projected = self._telemetry.project_pending(
            limit=telemetry_limit
        )
        snapshot = self._health.freeze_admission_snapshot()
        route_request = self._require_route_request(
            route_request_factory,
            snapshot,
            client_request_id=client_request_id,
        )
        return projected, snapshot, route_request

    def _require_bound_route(
        self,
        command_id: CommandID | str,
        route_decision_id: str,
    ) -> tuple[
        RequestSnapshot,
        RouteDecision,
        RouteCandidateDecision,
    ]:
        request = self._dispatch.get_request(command_id)
        if request is None:
            raise RecordNotFoundError(
                "dispatch_request",
                str(command_id),
            )

        decision = self._routing.get_route_decision(
            route_decision_id
        )
        if decision is None:
            raise RecordNotFoundError(
                "route_decision",
                route_decision_id,
            )

        selected = self._selected_candidate(decision)
        digest = canonical_route_decision_digest(decision)
        expected_binding = (
            decision.client_request_id,
            decision.configuration.revision,
            decision.required_capability_tier,
            selected.instance_id,
            selected.representative_profile_id,
            digest,
        )
        actual_binding = (
            request.client_request_id,
            request.configuration_revision,
            request.required_capability_tier,
            request.selected_peer_instance_id,
            request.selected_profile_id,
            request.route_decision_digest,
        )
        if actual_binding != expected_binding:
            raise InvalidMutationError(
                "dispatch request is not bound to the supplied "
                "route decision"
            )

        return request, decision, selected

    def admit_request(
        self,
        envelope: CommandEnvelope,
        *,
        route_request_factory: RouteRequestFactory,
        required_capability_tier: CapabilityTier,
        authenticated_subject: AuthenticatedSubject,
        completion_contract: CompletionContract,
        dispatch_policy_revision: RevisionValue,
        session_id: str,
        owner_principal_id: str,
        owner_instance_id: str,
        authority_epoch: int,
        heartbeat_timeout_ms: int,
        owner_peer_id: str = "",
        telemetry_limit: int = 100,
    ) -> AdmissionWorkflowResult:
        """Project, freeze health, route, and admit one request.

        Checks for an existing idempotent admission first, before doing
        any telemetry/health/routing work: ``canonical_route_decision_
        digest`` embeds each ``RouteDecision``'s freshly-minted
        ``decision_id``, so a second call can never reproduce the exact
        digest dispatch already recorded on a first, successful call --
        comparing them would incorrectly reject a legitimate retry of
        the identical envelope. A concurrent race (two identical
        envelopes admitted around the same time, both missing the
        up-front peek) is handled the same way after the fact: if
        dispatch's returned digest doesn't match what this call just
        computed, that is by construction an idempotent replay, not a
        corruption -- never a caller error to raise on.
        """

        existing = self._dispatch.peek_idempotent_admission(
            envelope,
            authenticated_subject=authenticated_subject,
            completion_contract=completion_contract,
        )
        if existing is not None:
            if (
                existing[0].required_capability_tier
                is not required_capability_tier
            ):
                raise InvalidMutationError(
                    "idempotent admission capability tier mismatch"
                )
            return AdmissionWorkflowResult(
                projected_terminal_events=0,
                admission_snapshot=None,
                route=None,
                dispatch_admission=existing,
            )

        (
            projected,
            admission_snapshot,
            route_request,
        ) = self._project_freeze_and_build(
            client_request_id=envelope.client_request_id,
            route_request_factory=route_request_factory,
            telemetry_limit=telemetry_limit,
        )

        if (
            route_request.required_capability_tier
            is not required_capability_tier
        ):
            raise InvalidMutationError(
                "route request capability tier does not match admission"
            )

        route = self._routing.select_route(route_request)
        if (
            route.decision.required_capability_tier
            is not required_capability_tier
        ):
            raise InvalidMutationError(
                "route decision capability tier does not match admission"
            )
        if route.error_code is ErrorCode.ROUTE_EXHAUSTED:
            return AdmissionWorkflowResult(
                projected_terminal_events=projected,
                admission_snapshot=admission_snapshot,
                route=route,
                dispatch_admission=None,
            )

        if route.error_code is not None:
            raise InvalidMutationError(
                "routing returned an unsupported error code"
            )

        selected = self._selected_candidate(route.decision)
        route_digest = canonical_route_decision_digest(
            route.decision
        )
        dispatch_admission = self._dispatch.admit_request(
            envelope,
            authenticated_subject=authenticated_subject,
            completion_contract=completion_contract,
            policy_revision=dispatch_policy_revision,
            configuration_revision=(
                route.decision.configuration.revision
            ),
            required_capability_tier=required_capability_tier,
            selected_peer_instance_id=selected.instance_id,
            selected_profile_id=(
                selected.representative_profile_id
            ),
            route_decision_digest=route_digest,
            session_id=session_id,
            owner_principal_id=owner_principal_id,
            owner_instance_id=owner_instance_id,
            authority_epoch=authority_epoch,
            heartbeat_timeout_ms=heartbeat_timeout_ms,
            owner_peer_id=owner_peer_id,
        )
        if dispatch_admission[0].route_decision_digest != route_digest:
            return AdmissionWorkflowResult(
                projected_terminal_events=projected,
                admission_snapshot=None,
                route=None,
                dispatch_admission=dispatch_admission,
            )

        return AdmissionWorkflowResult(
            projected_terminal_events=projected,
            admission_snapshot=admission_snapshot,
            route=route,
            dispatch_admission=dispatch_admission,
        )

    def prepare_for_dispatch(
        self,
        command_id: CommandID | str,
        *,
        route_decision_id: str,
        route_request_factory: RouteRequestFactory,
        session_key: SessionBindingKey | None = None,
        telemetry_limit: int = 100,
    ) -> PreDispatchWorkflowResult:
        """Apply RT-06 immediately before entering PREPARED."""

        current, _, _ = self._require_bound_route(
            command_id,
            route_decision_id,
        )
        if current.state is not RequestState.ADMITTED:
            raise InvalidMutationError(
                "pre-dispatch route validation requires an "
                "ADMITTED request"
            )

        (
            projected,
            admission_snapshot,
            current_route_request,
        ) = self._project_freeze_and_build(
            client_request_id=current.client_request_id,
            route_request_factory=route_request_factory,
            telemetry_limit=telemetry_limit,
        )
        recheck = self._routing.validate_route_for_dispatch(
            route_decision_id,
            current_request=current_route_request,
        )

        if recheck.validation.dispatch_permitted:
            updated = self._dispatch.prepare_request(
                command_id,
                session_key=session_key,
            )
        else:
            updated = self._dispatch.reject_policy(
                command_id,
                error_code=ErrorCode.CONFIGURATION_STALE,
            )

        return PreDispatchWorkflowResult(
            projected_terminal_events=projected,
            admission_snapshot=admission_snapshot,
            route_recheck=recheck,
            request=updated,
        )

    def authorize_retry(
        self,
        command_id: CommandID | str,
        previous_attempt_id: str,
        *,
        route_intent: RetryRouteIntent,
        route_request_factory: RouteRequestFactory,
        expected_request_revision: int,
        expected_previous_attempt_revision: int,
        expected_highest_attempt_number: int,
        frozen_max_attempts: int,
        current_policy_revision: RevisionValue,
        reconciliation_complete: bool,
        heartbeat_timeout_ms: int,
        telemetry_limit: int = 100,
    ) -> RetryWorkflowResult:
        """Prepare fresh route facts, then call the atomic authority boundary."""

        current = self._dispatch.get_request(command_id)
        if current is None:
            raise RecordNotFoundError(
                "dispatch_request",
                str(command_id),
            )
        (
            projected,
            admission_snapshot,
            current_route_request,
        ) = self._project_freeze_and_build(
            client_request_id=current.client_request_id,
            route_request_factory=route_request_factory,
            telemetry_limit=telemetry_limit,
        )
        match route_intent:
            case SameTargetRoute():
                prepared_route_intent: RetryRouteIntent = SameTargetRoute(
                    route_decision_id=route_intent.route_decision_id,
                    current_route_request=current_route_request,
                )
            case FailoverRoute():
                failed_instance_id = current.selected_peer_instance_id
                failover_candidates = tuple(
                    replace(
                        candidate,
                        eligible=False,
                        exclusion_reason=FAILED_TARGET_EXCLUDED_BY_RETRY,
                    )
                    if candidate.instance_id == failed_instance_id
                    else candidate
                    for candidate in current_route_request.candidates
                )
                prepared_route_intent = FailoverRoute(
                    failed_route_decision_id=(
                        route_intent.failed_route_decision_id
                    ),
                    failover_route_request=replace(
                        current_route_request,
                        candidates=failover_candidates,
                    ),
                )
            case _ as unreachable:
                assert_never(unreachable)

        retry_admission = self._dispatch.authorize_retry(
            command_id,
            previous_attempt_id,
            route_intent=prepared_route_intent,
            expected_request_revision=expected_request_revision,
            expected_previous_attempt_revision=(
                expected_previous_attempt_revision
            ),
            expected_highest_attempt_number=(
                expected_highest_attempt_number
            ),
            frozen_max_attempts=frozen_max_attempts,
            current_policy_revision=current_policy_revision,
            reconciliation_complete=reconciliation_complete,
            heartbeat_timeout_ms=heartbeat_timeout_ms,
        )
        return RetryWorkflowResult(
            projected_terminal_events=projected,
            admission_snapshot=admission_snapshot,
            request=retry_admission.request,
            retry_admission=retry_admission,
        )

    def dispatch_and_execute(
        self,
        command_id: CommandID | str,
        *,
        capability_lease_id: str,
        peer_instance_id: str,
        current_policy_revision: RevisionValue,
        materializer: ArtifactMaterializer,
        adapter_request: AdapterRequest,
        peer_adapter: PeerAdapter | None = None,
        profile: ProfileDescriptor,
        limits: TransportLimits,
        workspace_roots: Mapping[str, Path],
        content_providers: Mapping[str, Callable[[], bytes]],
        completion_contract: CompletionContract,
        heartbeat_timeout_ms: int,
        transport: str = "pipe",
        service: DispatchService | None = None,
        session: SessionHint | None = None,
        event_sink: Callable[[DecoderEvent], None] | None = None,
    ) -> ExecutionWorkflowResult:
        """Dispatch and execute an admitted/prepared command through process supervision."""

        dispatch_service = service if service is not None else self._dispatch
        selected_peer_adapter = (
            peer_adapter if peer_adapter is not None else self._peer_adapter
        )
        if selected_peer_adapter is None:
            raise ValueError("peer_adapter is required")

        # Pre-spawn enforcement gate (errata 7.2 point 3 / 7.4).  This runs
        # between adapter selection and planning, so a denied dispatch never
        # reaches plan_invocation(), attempt creation, or run_process().
        # CapabilityLeaseViolation propagates to the caller by design.
        _validated_capability = dispatch_service.require_dispatch_capability(
            command_id,
            capability_lease_id=capability_lease_id,
            peer_instance_id=peer_instance_id,
            adapter_peer_kind=selected_peer_adapter.descriptor.peer_kind,
            profile=profile,
            current_policy_revision=current_policy_revision,
        )
        if session is not None:
            if Capability.SESSION not in selected_peer_adapter.descriptor.capabilities:
                raise UnsupportedCapabilityError(
                    adapter_id=selected_peer_adapter.descriptor.adapter_id,
                    capability=Capability.SESSION,
                )


        invocation_plan = selected_peer_adapter.plan_invocation(
            request=adapter_request,
            profile=profile,
            session=session,
            limits=limits,
        )

        # Produce the enforcement receipt for this invocation plan.
        # Increment 4 scope: adapters emit "unverified" enforcement tags;
        # increment 5 will extend each adapter to report its actual vector.
        _enforcement_receipt = InvocationEnforcementReceipt(
            capability_lease_id=_validated_capability.capability_lease_id,
            command_id=_validated_capability.command_id,
            realized_enforcement=_validated_capability.satisfied_floor,
            controls_description="unverified",
            evidence_source_tag="unverified",
            plan_digest="unverified",
        )

        # Step 1: Create attempt under PREPARED request
        attempt = dispatch_service.create_attempt(
            command_id,
            expected_authorized_attempt_number=_validated_capability.authorized_attempt_number,
        )

        # Step 2: Resolve workspace paths and generate manifest
        workspace = resolve_workspace_paths(
            adapter_request,
            invocation_plan,
            workspace_roots=workspace_roots,
        )
        manifest = generate_materialization_manifest(
            invocation_plan,
            workspace,
            attempt_id=attempt.attempt_id,
        )

        if manifest.items:
            now = dispatch_service.now()
            staging_root_rel = str(
                workspace.staging_dir.relative_to(workspace.workspace_root)
            )
            # Compute manifest digest matching MaterializationItemRequest
            first_item = manifest.items[0]
            item_req = MaterializationItemRequest(
                artifact_id=first_item.artifact_id,
                source=MaterializationSource.BYTES_INLINE,
                target_path=Path(first_item.staging_path.relative_to(workspace.workspace_root)),  # pyright: ignore[reportArgumentType]
                expected_digest=(
                    f"sha256:{first_item.sha256_hex}"
                    if not first_item.sha256_hex.startswith("sha256:")
                    else first_item.sha256_hex
                ),
                expected_length=first_item.expected_length,
                attempt_id=attempt.attempt_id,
                placeholder=first_item.placeholder,
                workspace_scope_id=workspace.scope_id,
                staging_ref=str(first_item.staging_path),
                access_mode=first_item.access_mode,
                declared_lifecycle=first_item.lifecycle,
            )
            manifest_digest = compute_manifest_digest(item_req)

            manifest_record = ArtifactManifestRecord(
                attempt_id=attempt.attempt_id,
                workspace_scope_id=workspace.scope_id,
                staging_root_ref=staging_root_rel,
                manifest_digest=manifest_digest,
                item_count=len(manifest.items),
                created_at=now,
                revision=1,
            )
            item_records: list[ArtifactMetadata] = []
            for item in manifest.items:
                staging_ref = str(
                    item.staging_path.relative_to(workspace.workspace_root)
                )
                item_records.append(
                    ArtifactMetadata(
                        attempt_id=attempt.attempt_id,
                        artifact_id=item.artifact_id,
                        placeholder=item.placeholder,
                        workspace_scope_id=workspace.scope_id,
                        staging_ref=staging_ref,
                        access_mode=item.access_mode,
                        declared_lifecycle=item.lifecycle,
                        state=ArtifactState.DECLARED,
                        declared_at=now,
                        revision=1,
                        expected_sha256_hex=item.sha256_hex,
                        expected_length=item.expected_length,
                    )
                )
            dispatch_service.record_artifact_manifest(
                manifest_record,
                item_records,
            )
        else:
            manifest_digest = ""

        # Step 3: Materialize artifacts
        mat_results = materializer.materialize_manifest(manifest, content_providers)
        has_mat_failure = any(
            res.status in (MaterializationStatus.HARD_FAILURE, MaterializationStatus.RETRYABLE_FAILURE)
            or res.error is not None
            for res in mat_results
        )

        if has_mat_failure:
            dispatch_service.mark_artifacts_orphaned_if_manifest_exists(
                attempt.attempt_id,
                failure_code="PRE_DISPATCH_FAILED",
            )
            updated_req, updated_att = dispatch_service.fail_pre_dispatch(
                command_id,
                attempt.attempt_id,
                error_code=ErrorCode.ARTIFACT_IDENTITY_UNPROVABLE,
                transport=transport,
            )
            return ExecutionWorkflowResult(
                request=updated_req,
                attempt=updated_att,
                lease=None,
                materialization_results=mat_results,
                process_outcome=None,
                completion_assessment=None,
                decoded_output=None,
            )

        # Step 4: Record dispatch intent and reserve artifacts atomically if artifacts exist
        try:
            if manifest.items:
                req, att, lease = (  # pyright: ignore[reportUnusedVariable]
                    dispatch_service.record_dispatch_intent_and_reserve_artifacts(
                        command_id,
                        attempt.attempt_id,
                        expected_manifest_digest=manifest_digest,
                        validated_lease=_validated_capability,
                        enforcement_receipt=_enforcement_receipt,
                    )
                )
            else:
                req, att, lease = dispatch_service.record_dispatch_intent(  # pyright: ignore[reportUnusedVariable]
                    command_id,
                    attempt.attempt_id,
                    validated_lease=_validated_capability,
                    enforcement_receipt=_enforcement_receipt,
                )
        except Exception:
            dispatch_service.mark_artifacts_orphaned_if_manifest_exists(
                attempt.attempt_id,
                failure_code="RESERVATION_FAILED",
            )
            updated_req, updated_att = dispatch_service.fail_pre_dispatch(
                command_id,
                attempt.attempt_id,
                error_code=ErrorCode.ARTIFACT_RESERVATION_FAILED,
                transport=transport,
            )
            return ExecutionWorkflowResult(
                request=updated_req,
                attempt=updated_att,
                lease=None,
                materialization_results=mat_results,
                process_outcome=None,
                completion_assessment=None,
                decoded_output=None,
            )

        # Step 5: Spawn process and drive supervisor + heartbeat
        supervisor = ProcessSupervisor()
        heartbeat_worker: HeartbeatWorker | None = None
        running_lease: LeaseSnapshot = lease
        on_spawned_called = False
        record_running_error: BaseException | None = None
        streaming_enabled = (
            Capability.STREAM in selected_peer_adapter.descriptor.capabilities
        )
        live_decoder: OutputDecoder | None = None
        streamed_event_count = 0

        def _on_process_chunk(chunk: PipeProcessChunk) -> None:
            nonlocal streamed_event_count
            if live_decoder is None:
                return
            channel = (
                OutputChannel.STDOUT
                if chunk.channel is PipeOutputChannel.STDOUT
                else OutputChannel.STDERR
            )
            events = live_decoder.feed(chunk.data, channel=channel)
            streamed_event_count += len(events)
            if event_sink is not None:
                for event in events:
                    event_sink(event)

        def _on_spawned(
            proc: subprocess.Popen,  # pyright: ignore[reportMissingTypeArgument, reportUnknownParameterType]
            identity: ProcessBirthIdentity,
        ) -> None:
            nonlocal heartbeat_worker, running_lease, on_spawned_called, record_running_error
            on_spawned_called = True
            try:
                _, _, running_lease = dispatch_service.record_running(
                    command_id,
                    attempt.attempt_id,
                    process_identity=identity,
                )

                def _on_heartbeat_failure(failure: HeartbeatFailure) -> None:
                    """Trigger the cancellation ladder on heartbeat failure.

                    This callback runs on the heartbeat background thread.
                    ``supervisor.begin_cancellation()`` is thread-safe (protected
                    by the supervisor's internal lock).  The actual ladder steps
                    are driven synchronously on ``run_process``'s main thread
                    (Decision A).
                    """
                    supervisor.begin_cancellation()

                heartbeat_worker = HeartbeatWorker(
                    process=proc,
                    identity=identity,
                    initial_lease=running_lease,
                    renewer=dispatch_service,
                    heartbeat_timeout_ms=heartbeat_timeout_ms,
                    on_failure=_on_heartbeat_failure,  # pyright: ignore[reportUnknownArgumentType]
                )
                heartbeat_worker.start()
            except Exception as exc:
                record_running_error = exc

        config = PipeRunnerConfig(
            argv=manifest.substituted_argv,  # pyright: ignore[reportCallIssue]
            cwd=workspace.workspace_root,  # pyright: ignore[reportCallIssue]
            env=dict(invocation_plan.environment_delta)  # pyright: ignore[reportCallIssue]
            if invocation_plan.environment_delta
            else None,
            stdin_data=invocation_plan.stdin_payload,  # pyright: ignore[reportCallIssue]
            process_timeout_ms=invocation_plan.limits.process_timeout_ms,  # pyright: ignore[reportCallIssue]
            silence_timeout_ms=invocation_plan.limits.silence_timeout_ms,  # pyright: ignore[reportCallIssue]
            max_output_bytes=invocation_plan.limits.max_output_bytes,  # pyright: ignore[reportCallIssue]
        )

        spawn_error: BaseException | None = None
        process_outcome: ProcessSupervisionOutcome | None = None

        try:
            if streaming_enabled:
                live_decoder = selected_peer_adapter.new_decoder(invocation_plan)
            process_outcome = run_process(
                config,
                supervisor,
                on_spawned=_on_spawned,  # pyright: ignore[reportUnknownArgumentType]
                on_chunk=_on_process_chunk if streaming_enabled else None,
            )
        except Exception as exc:
            spawn_error = exc

        # Step 6: Stop heartbeat and capture latest fence / ownership
        if heartbeat_worker is not None:
            heartbeat_worker.stop(timeout=10.0)
            latest_fence = heartbeat_worker.latest_fence
            lease_owned = heartbeat_worker.lease_owned
        else:
            latest_fence = running_lease.fence
            lease_owned = False

        if not on_spawned_called and spawn_error is not None:
            updated_req, updated_att = dispatch_service.fail_pre_dispatch(
                command_id,
                attempt.attempt_id,
                error_code=ErrorCode.SPAWN_FAILED,
                transport=transport,
            )
            return ExecutionWorkflowResult(
                request=updated_req,
                attempt=updated_att,
                lease=running_lease,
                materialization_results=mat_results,
                process_outcome=None,
                completion_assessment=None,
                decoded_output=None,
            )

        if record_running_error is not None or (spawn_error is not None and on_spawned_called):
            updated_req, updated_att = dispatch_service.record_start_uncertain(
                command_id,
                attempt.attempt_id,
            )
            return ExecutionWorkflowResult(
                request=updated_req,
                attempt=updated_att,
                lease=running_lease,
                materialization_results=mat_results,
                process_outcome=process_outcome,
                completion_assessment=None,
                decoded_output=None,
            )

        assert process_outcome is not None

        # Step 7: Assess completion and begin assessment
        raw_chunks = (process_outcome.canonical_stream,) if process_outcome.canonical_stream else ()
        execution_outcome = process_outcome.execution_outcome
        terminal_evidence = ProcessTerminalEvidence(
            exit_code=execution_outcome.exit_code,
            timed_out=execution_outcome.timed_out,
            cancelled=execution_outcome.cancelled,
        )
        protocol_assessment = selected_peer_adapter.interpret_output(
            invocation_plan,
            terminal_evidence,
            raw_chunks,
        )
        
        decoder = (
            live_decoder
            if live_decoder is not None
            else selected_peer_adapter.new_decoder(invocation_plan)
        )
        if live_decoder is None and process_outcome.canonical_stream:
            decoder.feed(
                process_outcome.canonical_stream,
                channel=OutputChannel.STDOUT,
            )
        decoded_output = decoder.finalize()
        if event_sink is not None:
            for event in decoded_output.events[streamed_event_count:]:
                event_sink(event)
        
        assessment = assess_completion(
            completion_contract,
            execution_outcome,
            protocol_assessment,
        )
        dispatch_service.begin_assessment(command_id, attempt.attempt_id)

        # Step 8: Terminalize attempt (complete + consume + close lease) if lease owned
        if lease_owned:
            started_at = dispatch_service.now()
            terminal_classification = process_outcome.terminal_classification
            failure_classification = classify_attempt_failure(
                terminal_classification=terminal_classification,
                execution=execution_outcome,
                protocol=protocol_assessment,
                decoded_output=decoded_output,
            )
            updated_req, updated_att = (
                dispatch_service.complete_attempt_with_artifacts_and_lease(
                    command_id,
                    attempt.attempt_id,
                    result=AskResult(
                        execution=process_outcome.execution_outcome,
                        protocol=protocol_assessment,
                        completion=assessment,
                        policy_revision=req.policy_revision,
                        terminal_classification=terminal_classification,
                        failure_classification=failure_classification,
                    ),
                    transport=transport,
                    started_at=started_at,
                    final_fence=latest_fence,
                    process_integrity=process_outcome.stream_events_ordered,
                )
            )
            closed_lease = dispatch_service.get_lease(req.lease_id)
            return ExecutionWorkflowResult(
                request=updated_req,
                attempt=updated_att,
                lease=closed_lease,
                materialization_results=mat_results,
                process_outcome=process_outcome,
                completion_assessment=assessment,
                decoded_output=decoded_output,
            )

        # Lease ownership was lost during execution: leave attempt for conservative recovery
        latest_req, latest_att = dispatch_service.get_request_and_attempt(
            command_id, attempt.attempt_id
        )

        return ExecutionWorkflowResult(
            request=latest_req,
            attempt=latest_att,
            lease=running_lease,
            materialization_results=mat_results,
            process_outcome=process_outcome,
            completion_assessment=assessment,
            decoded_output=decoded_output,
        )

    # -- T1 increment 5C-2b: bounded outer retry loop --

    def _adjudicate_with_fresh_evidence(
        self,
        execution: ExecutionWorkflowResult,
        *,
        durable_attempt_number: int,
        frozen_max_attempts: int,
        reconciliation_complete: bool,
        condition_evidence_provider: RetryConditionEvidenceProvider | None,
    ) -> tuple[RetryDecision, RetryLoopStopReason | None]:
        """Parent steps 5-8: adjudicate, sourcing CONDITIONAL evidence fresh.

        The first adjudication deliberately carries no evidence, because that
        is how the pure 5A policy reports *which* condition it needs. Only
        then is fresh evidence read and evaluated, and only a satisfied
        non-session condition is re-adjudicated. Evidence is never cached.
        """

        decision = adjudicate_retry(
            execution,
            durable_attempt_number=durable_attempt_number,
            max_attempts=frozen_max_attempts,
            reconciliation_complete=reconciliation_complete,
        )
        if (
            decision.action is not RetryAction.DEFER
            or not decision.required_conditions
        ):
            return decision, None

        condition = decision.required_conditions[0]
        evidence = (
            None
            if condition_evidence_provider is None
            else read_fresh_retry_condition_evidence(
                condition_evidence_provider,
                latest_attempt=execution.attempt,
                condition=condition,
            )
        )
        resolution = evaluate_retry_condition_evidence(condition, evidence)
        if resolution.stop_reason is not None:
            return decision, resolution.stop_reason

        decision = adjudicate_retry(
            execution,
            durable_attempt_number=durable_attempt_number,
            max_attempts=frozen_max_attempts,
            reconciliation_complete=reconciliation_complete,
            condition_evidence=resolution.evidence_for_adjudication,
        )
        if decision.action is RetryAction.DEFER:
            return decision, RetryLoopStopReason.CONDITION_DEFERRED
        return decision, None

    def _concurrency_stop_result(
        self,
        command_id: CommandID | str,
        fresh_state: RetryLoopState,
        *,
        stop_reason: RetryLoopStopReason,
        decision_reason: RetryDecisionReason,
        records: list[AttemptExecutionRecord],
    ) -> MultiAttemptExecutionResult:
        """Build the terminal aggregate for a concurrency-derived stop.

        Only durably reconstructable facts from ``fresh_state`` are used;
        nothing about the outcome is guessed. The decision itself is
        synthesized (there was no adjudication for this observation) using
        the reason vocabulary reserved for concurrency outcomes.
        """

        observed = ExecutionWorkflowResult(
            request=fresh_state.request,
            attempt=fresh_state.attempts[-1],
            lease=fresh_state.current_lease,
        )
        decision = RetryDecision(
            disposition=None,
            action=RetryAction.STOP,
            reason=decision_reason,
            required_conditions=(),
            not_before=None,
        )
        records.append(
            AttemptExecutionRecord(
                execution=observed,
                error_detail=None,
                retry_decision=decision,
                retry_authorization=None,
            )
        )
        return MultiAttemptExecutionResult(
            command_id=CommandID(str(command_id)),
            attempts=tuple(records),
            stop_reason=stop_reason,
        )

    def _resolve_concurrent_conflict(
        self,
        dispatch_service: DispatchService,
        command_id: CommandID | str,
        *,
        target_attempt_number: int,
        records: list[AttemptExecutionRecord],
    ) -> MultiAttemptExecutionResult | ExecutionWorkflowResult:
        """Step 12: translate a typed concurrency conflict into an outcome.

        Both the ``StaleRevisionError`` boundary (authorization) and the
        ``ConcurrentAttemptClaimError`` boundary (attempt creation) route
        through this one classify-and-branch implementation, so same-target
        and failover races share identical loser semantics.

        Returns a terminal ``MultiAttemptExecutionResult`` when the loop
        must stop, or a durably rebuilt ``ExecutionWorkflowResult`` when the
        caller should resume ordinary adjudication using that rebuilt
        execution instead of re-executing.
        """

        spins_used = 0
        while True:
            fresh_state = dispatch_service.load_retry_loop_state(command_id)
            resolution = classify_concurrent_claim(
                fresh_state,
                target_attempt_number,
            )

            if resolution.outcome is ConcurrentClaimOutcome.TERMINAL_STATE:
                return self._concurrency_stop_result(
                    command_id,
                    fresh_state,
                    stop_reason=RetryLoopStopReason.CONCURRENT_TERMINAL_STATE,
                    decision_reason=RetryDecisionReason.CONCURRENT_TERMINAL_STATE,
                    records=records,
                )

            if resolution.outcome is ConcurrentClaimOutcome.ATTEMPT_IN_PROGRESS:
                return self._concurrency_stop_result(
                    command_id,
                    fresh_state,
                    stop_reason=RetryLoopStopReason.CONCURRENT_ATTEMPT_IN_PROGRESS,
                    decision_reason=RetryDecisionReason.CONCURRENT_ATTEMPT_IN_PROGRESS,
                    records=records,
                )

            if resolution.outcome is ConcurrentClaimOutcome.ATTEMPT_TERMINAL_REBUILD:
                rebuild_attempt_number = resolution.rebuild_attempt_number
                rebuilt_attempt = next(
                    a
                    for a in fresh_state.attempts
                    if a.attempt_number == rebuild_attempt_number
                )
                return ExecutionWorkflowResult(
                    request=fresh_state.request,
                    attempt=rebuilt_attempt,
                    lease=fresh_state.current_lease,
                )

            # ConcurrentClaimOutcome.NO_ADVANCEMENT_READJUDICATE: bounded
            # reload-and-reclassify so repeated conflicts cannot spin
            # forever (verification target item 9). There is no dedicated
            # stop-reason for spin exhaustion; failing closed to
            # CONCURRENT_ATTEMPT_IN_PROGRESS reports that authoritative
            # advancement could not be observed rather than guessing
            # further.
            assert resolution.readjudicate_retry_limit is not None
            spins_used += 1
            if spins_used >= resolution.readjudicate_retry_limit:
                return self._concurrency_stop_result(
                    command_id,
                    fresh_state,
                    stop_reason=RetryLoopStopReason.CONCURRENT_ATTEMPT_IN_PROGRESS,
                    decision_reason=RetryDecisionReason.CONCURRENT_ATTEMPT_IN_PROGRESS,
                    records=records,
                )

    def dispatch_with_retries(
        self,
        command_id: CommandID | str,
        *,
        initial_attempt: AttemptDispatchPlan,
        route_request_factory: RouteRequestFactory,
        current_policy_revision: RevisionValue,
        materializer: ArtifactMaterializer,
        limits: TransportLimits,
        workspace_roots: Mapping[str, Path],
        content_providers: Mapping[str, Callable[[], bytes]],
        completion_contract: CompletionContract,
        heartbeat_timeout_ms: int,
        max_attempts: int,
        condition_evidence_provider: RetryConditionEvidenceProvider | None = None,
        retry_target_resolver: RetryTargetResolver | None = None,
        transport: str = "pipe",
        service: DispatchService | None = None,
        event_sink: Callable[[DecoderEvent], None] | None = None,
    ) -> MultiAttemptExecutionResult:
        """Run the bounded outer retry loop for one command.

        Implements all 14 parent steps. Step 12 (typed conflict reload and
        concurrency outcome) is handled by ``_resolve_concurrent_conflict``,
        invoked from both the ``StaleRevisionError`` authorization boundary
        and the ``ConcurrentAttemptClaimError`` attempt-creation boundary so
        same-target and failover races share identical loser semantics.
        """

        dispatch_service = service if service is not None else self._dispatch

        # Step 2: freeze the command-global bound before attempt 1. This is
        # idempotent, and rejects a conflicting caller value by raising
        # RetryPolicyConflictError, which propagates per Section 2.2.
        frozen_max_attempts = dispatch_service.freeze_retry_policy(
            command_id,
            max_attempts,
        )

        # Step 1: one consistent durable snapshot with validated history.
        state = dispatch_service.load_retry_loop_state(command_id)

        current_plan = initial_attempt
        records: list[AttemptExecutionRecord] = []
        placeholder_route_request: RouteRequest | None = None

        if state.attempts:
            # Step 3 (resume): a durable attempt already represents the
            # initial plan, so it is adjudicated from durable facts instead
            # of being executed again. Only durably recoverable fields are
            # populated; no process/decoder detail is fabricated.
            execution = ExecutionWorkflowResult(
                request=state.request,
                attempt=state.attempts[-1],
                lease=state.current_lease,
            )
        else:
            # Step 3 (fresh) plus step 13 for attempt 1.
            try:
                execution = self.dispatch_and_execute(
                    command_id,
                    capability_lease_id=current_plan.capability_lease_id,
                    peer_instance_id=current_plan.peer_instance_id,
                    current_policy_revision=current_policy_revision,
                    materializer=materializer,
                    adapter_request=current_plan.adapter_request,
                    peer_adapter=current_plan.peer_adapter,
                    profile=current_plan.profile,
                    limits=limits,
                    workspace_roots=workspace_roots,
                    content_providers=content_providers,
                    completion_contract=completion_contract,
                    heartbeat_timeout_ms=heartbeat_timeout_ms,
                    transport=transport,
                    service=service,
                    session=current_plan.session,
                    event_sink=event_sink,
                )
            except ConcurrentAttemptClaimError as error:
                # Step 12 (attempt-creation boundary): another caller already
                # won the durable claim for the attempt this caller expected.
                conflict_result = self._resolve_concurrent_conflict(
                    dispatch_service,
                    command_id,
                    target_attempt_number=error.expected_attempt_number,
                    records=records,
                )
                if isinstance(conflict_result, MultiAttemptExecutionResult):
                    return conflict_result
                execution = conflict_result

        while True:
            # Step 1 (per iteration) and step 14: re-read one consistent
            # durable snapshot so the route binding and history match the
            # attempt that was just observed, never a pre-execution copy.
            state = dispatch_service.load_retry_loop_state(command_id)
            attempt = execution.attempt
            reconciliation_complete = attempt.reconciliation_complete

            # Steps 5 through 8.
            (
                decision,
                condition_stop,
            ) = self._adjudicate_with_fresh_evidence(
                execution,
                durable_attempt_number=attempt.attempt_number,
                frozen_max_attempts=frozen_max_attempts,
                reconciliation_complete=reconciliation_complete,
                condition_evidence_provider=condition_evidence_provider,
            )

            # Step 4: every observed attempt joins the aggregate exactly once.
            def _finish(
                reason: RetryLoopStopReason,
                *,
                authorization: RetryWorkflowResult | None = None,
                observed: ExecutionWorkflowResult = execution,
                observed_decision: RetryDecision = decision,
            ) -> MultiAttemptExecutionResult:
                records.append(
                    AttemptExecutionRecord(
                        execution=observed,
                        error_detail=None,
                        retry_decision=observed_decision,
                        retry_authorization=authorization,
                    )
                )
                return MultiAttemptExecutionResult(
                    command_id=CommandID(str(command_id)),
                    attempts=tuple(records),
                    stop_reason=reason,
                )

            if condition_stop is not None:
                # Step 8: a potentially satisfiable condition is unproven.
                # No wait is guessed and nothing is replayed.
                return _finish(condition_stop)
            if decision.action is RetryAction.STOP:
                # Step 6.
                return _finish(_stop_reason_for(decision))
            if decision.action is RetryAction.DEFER:
                return _finish(RetryLoopStopReason.CONDITION_DEFERRED)

            # Step 9: the bounded same-target-then-one-failover sequence.
            transition = transition_retry_route(decision)
            route_decision_id = state.route_decision.decision_id
            if placeholder_route_request is None:
                # authorize_retry() composes and marks the authoritative
                # route request from its own health freeze and only reads the
                # decision ID off this intent. One placeholder is built lazily
                # and reused so the loop never freezes health twice per retry.
                (
                    _projected,
                    _snapshot,
                    placeholder_route_request,
                ) = self._project_freeze_and_build(
                    client_request_id=state.request.client_request_id,
                    route_request_factory=route_request_factory,
                    telemetry_limit=100,
                )

            authorization: RetryWorkflowResult | None = None
            stop_reason: RetryLoopStopReason | None = None
            rebuilt_execution: ExecutionWorkflowResult | None = None
            while authorization is None:
                next_action = transition.next_action
                if next_action is RetryAction.RETRY_SAME_TARGET:
                    route_intent: RetryRouteIntent = SameTargetRoute(
                        route_decision_id=route_decision_id,
                        current_route_request=placeholder_route_request,
                    )
                elif next_action is RetryAction.FAILOVER:
                    route_intent = FailoverRoute(
                        failed_route_decision_id=route_decision_id,
                        failover_route_request=placeholder_route_request,
                    )
                else:
                    raise InvalidMutationError(
                        "retry route transition produced no route action"
                    )

                try:
                    # Steps 10 and 11: the atomic 5B authority boundary.
                    authorization = self.authorize_retry(
                        command_id,
                        attempt.attempt_id,
                        route_intent=route_intent,
                        route_request_factory=route_request_factory,
                        expected_request_revision=(
                            execution.request.revision
                        ),
                        expected_previous_attempt_revision=attempt.revision,
                        expected_highest_attempt_number=attempt.attempt_number,
                        frozen_max_attempts=frozen_max_attempts,
                        current_policy_revision=current_policy_revision,
                        reconciliation_complete=reconciliation_complete,
                        heartbeat_timeout_ms=heartbeat_timeout_ms,
                    )
                except Exception as error:
                    # Only the ratified typed outcomes are translated. Every
                    # other exception is re-raised by the classifier itself,
                    # so nothing arbitrary is ever treated as retryable.
                    outcome = classify_authorization_error(error)
                    if outcome is AuthorizationErrorSignal.RELOAD_DURABLE_STATE:
                        # Step 12 (authorization boundary): a stale decision
                        # is never resubmitted. Reload and classify the
                        # authoritative outcome instead.
                        conflict_result = self._resolve_concurrent_conflict(
                            dispatch_service,
                            command_id,
                            target_attempt_number=attempt.attempt_number + 1,
                            records=records,
                        )
                        if isinstance(conflict_result, MultiAttemptExecutionResult):
                            return conflict_result
                        rebuilt_execution = conflict_result
                        break
                    if outcome is AuthorizationErrorSignal.PREPARE_FAILOVER:
                        # At most one failover: transition_retry_route()
                        # raises if a second one is ever requested.
                        transition = transition_retry_route(
                            transition.decision,
                            attempted_action=next_action,
                            error=error,
                        )
                        continue
                    stop_reason = outcome
                    break

            if rebuilt_execution is not None:
                # Resume ordinary adjudication from the durably rebuilt
                # attempt instead of re-executing; never fabricate a
                # decision directly.
                execution = rebuilt_execution
                continue

            if authorization is None:
                if stop_reason is None:
                    raise InvalidMutationError(
                        "retry authorization produced no outcome"
                    )
                return _finish(
                    stop_reason,
                    observed_decision=transition.decision,
                )

            # Step 11 (materialization): build the next plan only from the
            # committed bundle's own machine-owned binding.
            next_plan = build_retry_dispatch_plan(
                bundle=authorization.retry_admission,
                route_action=transition.decision.action,
                current_plan=current_plan,
                resolver=(
                    retry_target_resolver
                    if retry_target_resolver is not None
                    else _unresolvable_retry_target
                ),
            )
            if isinstance(next_plan, RetryLoopStopReason):
                return _finish(
                    next_plan,
                    authorization=authorization,
                    observed_decision=transition.decision,
                )

            records.append(
                AttemptExecutionRecord(
                    execution=execution,
                    error_detail=None,
                    retry_decision=transition.decision,
                    retry_authorization=authorization,
                )
            )

            # Step 13: exactly one execution per authorized attempt.
            current_plan = next_plan
            try:
                execution = self.dispatch_and_execute(
                    command_id,
                    capability_lease_id=current_plan.capability_lease_id,
                    peer_instance_id=current_plan.peer_instance_id,
                    current_policy_revision=current_policy_revision,
                    materializer=materializer,
                    adapter_request=current_plan.adapter_request,
                    peer_adapter=current_plan.peer_adapter,
                    profile=current_plan.profile,
                    limits=limits,
                    workspace_roots=workspace_roots,
                    content_providers=content_providers,
                    completion_contract=completion_contract,
                    heartbeat_timeout_ms=heartbeat_timeout_ms,
                    transport=transport,
                    service=service,
                    session=current_plan.session,
                    event_sink=event_sink,
                )
            except ConcurrentAttemptClaimError as error:
                # Step 12 (attempt-creation boundary): another caller already
                # won the durable claim for the attempt this caller expected.
                conflict_result = self._resolve_concurrent_conflict(
                    dispatch_service,
                    command_id,
                    target_attempt_number=error.expected_attempt_number,
                    records=records,
                )
                if isinstance(conflict_result, MultiAttemptExecutionResult):
                    return conflict_result
                execution = conflict_result

            # Step 14: iterate; the next pass re-reads durable state.


_STOP_REASON_BY_DECISION: Mapping[
    RetryDecisionReason,
    RetryLoopStopReason,
] = {
    RetryDecisionReason.VERIFIED_SUCCESS: (
        RetryLoopStopReason.VERIFIED_SUCCESS
    ),
    RetryDecisionReason.DELIVERED_UNVERIFIED: (
        RetryLoopStopReason.DELIVERED_UNVERIFIED
    ),
    RetryDecisionReason.AUTHORITATIVE_CANCELLATION: (
        RetryLoopStopReason.AUTHORITATIVE_CANCELLATION
    ),
    RetryDecisionReason.NEVER_DISPOSITION: (
        RetryLoopStopReason.NEVER_DISPOSITION
    ),
    RetryDecisionReason.UNSAFE_NO_EVIDENCE: (
        RetryLoopStopReason.UNSAFE_NO_EVIDENCE
    ),
    RetryDecisionReason.ATTEMPT_LIMIT_REACHED: (
        RetryLoopStopReason.ATTEMPT_LIMIT_REACHED
    ),
    RetryDecisionReason.LEGACY_CLASSIFICATION_UNKNOWN: (
        RetryLoopStopReason.LEGACY_CLASSIFICATION_UNKNOWN
    ),
}


def _stop_reason_for(decision: RetryDecision) -> RetryLoopStopReason:
    """Map one STOP decision onto the exact ratified aggregate reason."""

    mapped = _STOP_REASON_BY_DECISION.get(decision.reason)
    if mapped is None:
        raise InvalidMutationError(
            "retry loop cannot map decision reason "
            f"{decision.reason.value!r} to a stop reason"
        )
    return mapped


def _unresolvable_retry_target(
    peer_kind: str,
    instance_id: str,
    profile_id: str,
) -> ResolvedRetryTarget | None:
    """Fail closed when no replacement-target resolver was injected."""

    return None
