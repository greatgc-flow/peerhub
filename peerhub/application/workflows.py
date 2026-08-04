"""Cross-feature Slice 4 admission and dispatch workflows."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
import subprocess
from typing import Protocol, TypeAlias

from peerhub.adapters.contract import (
    AdapterRequest,
    InvocationPlan,
    ProtocolAssessment,
)
from peerhub.core.errors import (
    InvalidMutationError,
    RecordNotFoundError,
)
from peerhub.core.protocol import (
    CommandEnvelope,
    CommandID,
    ErrorCode,
    RevisionValue,
)
from peerhub.dispatch.artifacts import (
    WorkspacePaths,
    generate_materialization_manifest,
    resolve_workspace_paths,
)
from peerhub.dispatch.completion import assess_completion
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
    SessionBindingKey,
)
from peerhub.dispatch.heartbeat import HeartbeatWorker
from peerhub.dispatch.materializer import (
    ArtifactMaterializer,
    MaterializationItemRequest,
    MaterializationResult,
    MaterializationSource,
    MaterializationStatus,
    compute_manifest_digest,
)
from peerhub.dispatch.pipe import PipeRunnerConfig, run_process
from peerhub.dispatch.process import (
    ProcessSupervisionOutcome,
    ProcessSupervisor,
)
from peerhub.dispatch.service import DispatchService
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


DispatchAdmission: TypeAlias = tuple[
    RequestSnapshot,
    AdmissionReceipt,
    LeaseSnapshot,
]
RetryAdmission: TypeAlias = tuple[
    RequestSnapshot,
    AttemptSnapshot,
    LeaseSnapshot,
]


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
    """Result of validating a route before authorizing one retry."""

    projected_terminal_events: int
    admission_snapshot: AdmissionSnapshot
    route_recheck: RoutePreDispatchResult
    request: RequestSnapshot
    retry_admission: RetryAdmission | None


class ApplicationWorkflows:
    """Coordinate Slice 4 feature services without owning their stores."""

    def __init__(
        self,
        *,
        telemetry: TelemetryProjector,
        health: HealthService,
        routing: RoutingService,
        dispatch: DispatchService,
    ) -> None:
        self._telemetry = telemetry
        self._health = health
        self._routing = routing
        self._dispatch = dispatch

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
        if not isinstance(request, RouteRequest):
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
            selected.instance_id,
            selected.representative_profile_id,
            digest,
        )
        actual_binding = (
            request.client_request_id,
            request.configuration_revision,
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
        authenticated_principal: str,
        actor_authorized: bool,
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
            authenticated_principal=authenticated_principal,
            actor_authorized=actor_authorized,
            completion_contract=completion_contract,
        )
        if existing is not None:
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

        route = self._routing.select_route(route_request)
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
            authenticated_principal=authenticated_principal,
            actor_authorized=actor_authorized,
            completion_contract=completion_contract,
            policy_revision=dispatch_policy_revision,
            configuration_revision=(
                route.decision.configuration.revision
            ),
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
        route_decision_id: str,
        route_request_factory: RouteRequestFactory,
        reconciliation_complete: bool,
        heartbeat_timeout_ms: int,
        telemetry_limit: int = 100,
    ) -> RetryWorkflowResult:
        """Apply RT-06 before moving a retry directly to PREPARED."""

        current, _, _ = self._require_bound_route(
            command_id,
            route_decision_id,
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

        if not recheck.validation.dispatch_permitted:
            return RetryWorkflowResult(
                projected_terminal_events=projected,
                admission_snapshot=admission_snapshot,
                route_recheck=recheck,
                request=current,
                retry_admission=None,
            )

        retry_admission = self._dispatch.authorize_retry(
            command_id,
            previous_attempt_id,
            reconciliation_complete=reconciliation_complete,
            heartbeat_timeout_ms=heartbeat_timeout_ms,
        )
        return RetryWorkflowResult(
            projected_terminal_events=projected,
            admission_snapshot=admission_snapshot,
            route_recheck=recheck,
            request=retry_admission[0],
            retry_admission=retry_admission,
        )

    def dispatch_and_execute(
        self,
        command_id: CommandID | str,
        *,
        materializer: ArtifactMaterializer,
        adapter_request: AdapterRequest,
        invocation_plan: InvocationPlan,
        workspace_roots: Mapping[str, Path],
        content_providers: Mapping[str, Callable[[], bytes]],
        completion_contract: CompletionContract,
        protocol_assessment: ProtocolAssessment,
        heartbeat_timeout_ms: int,
        transport: str = "pipe",
        service: DispatchService | None = None,
    ) -> ExecutionWorkflowResult:
        """Dispatch and execute an admitted/prepared command through process supervision."""

        dispatch_service = service if service is not None else self._dispatch

        # Step 1: Create attempt under PREPARED request
        attempt = dispatch_service.create_attempt(command_id)

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
                target_path=Path(first_item.staging_path.relative_to(workspace.workspace_root)),
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
            )

        # Step 4: Record dispatch intent and reserve artifacts atomically if artifacts exist
        try:
            if manifest.items:
                req, att, lease = (
                    dispatch_service.record_dispatch_intent_and_reserve_artifacts(
                        command_id,
                        attempt.attempt_id,
                        expected_manifest_digest=manifest_digest,
                    )
                )
            else:
                req, att, lease = dispatch_service.record_dispatch_intent(
                    command_id,
                    attempt.attempt_id,
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
            )

        # Step 5: Spawn process and drive supervisor + heartbeat
        supervisor = ProcessSupervisor()
        heartbeat_worker: HeartbeatWorker | None = None
        running_lease: LeaseSnapshot = lease
        on_spawned_called = False
        record_running_error: BaseException | None = None

        def _on_spawned(
            proc: subprocess.Popen,
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
                heartbeat_worker = HeartbeatWorker(
                    process=proc,
                    identity=identity,
                    initial_lease=running_lease,
                    renewer=dispatch_service,
                    heartbeat_timeout_ms=heartbeat_timeout_ms,
                )
                heartbeat_worker.start()
            except Exception as exc:
                record_running_error = exc

        config = PipeRunnerConfig(
            argv=manifest.substituted_argv,
            cwd=workspace.workspace_root,
            env=dict(invocation_plan.environment_delta)
            if invocation_plan.environment_delta
            else None,
            stdin_data=invocation_plan.stdin_payload,
        )

        spawn_error: BaseException | None = None
        process_outcome: ProcessSupervisionOutcome | None = None

        try:
            process_outcome = run_process(
                config,
                supervisor,
                on_spawned=_on_spawned,
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
            )

        assert process_outcome is not None

        # Step 7: Assess completion and begin assessment
        assessment = assess_completion(
            completion_contract,
            process_outcome.execution_outcome,
            protocol_assessment,
        )
        dispatch_service.begin_assessment(command_id, attempt.attempt_id)

        # Step 8: Terminalize attempt (complete + consume + close lease) if lease owned
        if lease_owned:
            started_at = dispatch_service.now()
            updated_req, updated_att = (
                dispatch_service.complete_attempt_with_artifacts_and_lease(
                    command_id,
                    attempt.attempt_id,
                    result=AskResult(
                        execution=process_outcome.execution_outcome,
                        protocol=protocol_assessment,
                        completion=assessment,
                        policy_revision=req.policy_revision,
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
        )
