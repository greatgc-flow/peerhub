from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, TYPE_CHECKING

if TYPE_CHECKING:
    from peerhub.dispatch.process import ProcessSupervisor
    from peerhub.runtime import Runtime

import hashlib

from peerhub.adapters.prompt_transport import (
    remove_staged_prompt,
    stage_prompt,
    sweep_stale_staged_prompts,
)
from peerhub.application import config_paths
from peerhub.adapters.registry import resolve_peer_target, ResolvedPeerTarget
from peerhub.adapters.contract import (
    AdapterRequest,
    SessionAction,
    PromptPolicy,
    SessionHint,
    DecoderEventKind,
)
from peerhub.application.ask_config import AskConfig, load_ask_config
from peerhub.application.bootstrap import build_direct_ask_admission_config
from peerhub.application.continuity import (
    render_continuity_block,
    resolve_continuity,
)
from peerhub.application.model_config import ModelConfigService, ResolvedModelBinding
from peerhub.application.retry import (
    AttemptDispatchPlan,
    ResolvedRetryTarget,
    RetryLoopStopReason,
)
from peerhub.core.context import Clock, IdSource, RuntimeContext, PathLayout
from peerhub.core.execution import TransportLimits, ExecutionCertainty
from peerhub.core.identity import AuthenticatedSubject
from peerhub.core.protocol import ErrorCode
from peerhub.dispatch.capability import CapabilityTier
from peerhub.dispatch.contract import (
    CommandEnvelope,
    CompletionContract,
    CompletionContractKind,
    RequestState,
    SessionBindingKey,
    SessionBindingSnapshot,
    SessionBindingState,
)
from peerhub.health.contract import (
    EvidenceSubject,
    PolicyScope,
    PolicyReceipt,
    HealthStageObservation,
    HealthStage,
    HealthStageStatus,
)
from peerhub.routing.contract import (
    RouteCandidateInput, 
    RouteRequest, 
    AdmissionSnapshot,
    ConfigurationSnapshot,
)
from peerhub.core.evidence import EvidenceValue, EvidenceState, EvidenceRef
from peerhub.telemetry.contract import UsageMeasurement
from peerhub.dispatch.materializer import ArtifactMaterializer


@dataclass(frozen=True)
class DirectAskRequest:
    workspace_root: Path
    peer_name: str
    prompt: str
    required_capability_tier: CapabilityTier
    profile_id: str | None
    limits: TransportLimits
    room_id: str | None = None
    task_id: str | None = None
    session_id: str | None = None
    resume: bool = False
    session_action: SessionAction | None = None
    max_attempts: int = 3


class DirectAskRetryTargetResolver:
    """Resolver for failover peer targets in direct ask."""

    def __init__(self, workspace_root: Path | None = None) -> None:
        self.workspace_root = workspace_root

    def __call__(
        self,
        peer_kind: str,
        instance_id: str,
        profile_id: str,
    ) -> ResolvedRetryTarget | None:
        try:
            target = resolve_peer_target(instance_id, profile_id=profile_id)
            return ResolvedRetryTarget(
                peer_adapter=target.adapter,
                profile=target.profile,
            )
        except Exception:
            try:
                target = resolve_peer_target(peer_kind, profile_id=profile_id)
                return ResolvedRetryTarget(
                    peer_adapter=target.adapter,
                    profile=target.profile,
                )
            except Exception:
                return None


@dataclass(frozen=True)
class DirectAskResult:
    command_id: str | None
    attempt_id: str | None
    peer_kind: str
    profile_id: str
    response_text: str | None
    request_state: RequestState | None
    error_code: ErrorCode | None
    execution_certainty: ExecutionCertainty | None


class _DirectAskRouteRequestFactory:
    def __init__(
        self,
        target: ResolvedPeerTarget,
        clock: Clock,
        ids: IdSource,
        client_request_id: str,
        policy_id: str,
        policy_revision: int,
        required_capability_tier: CapabilityTier,
    ) -> None:
        self.target = target
        self.clock = clock
        self.ids = ids
        self.client_request_id = client_request_id
        self.policy_id = policy_id
        self.policy_revision = policy_revision
        self.required_capability_tier = required_capability_tier

    def __call__(self, admission_snapshot: AdmissionSnapshot, /) -> RouteRequest:
        now = self.clock.now()
        
        # Check eligibility from admission_snapshot -- both admission and
        # availability must be acceptable (bug #1: previously only checked
        # admission_state, meaning stale evidence had no effect on routing).
        eligible = False
        for entry in admission_snapshot.entries:
            if (
                entry.instance_id == self.target.peer_kind 
                and entry.profile_id == self.target.profile.profile_id
                and entry.admission_state.value in ("OPEN", "PROBE_AUTHORIZED")
                and entry.availability_state.value not in ("STALE", "UNAVAILABLE")
                and not entry.profile_gate_backed_off
            ):
                eligible = True
                break

        usage_evidence = EvidenceValue[UsageMeasurement](
            state=EvidenceState.ABSENT,
            source_tag="cli",
            provider_id="cli",
            provider_version="1",
            observed_at=now,
            captured_at=now,
            freshness_ttl=0,
            evidence_ref=EvidenceRef("sha256:" + "0" * 64),
            value=None,
        )

        candidate = RouteCandidateInput(
            candidate_id=self.ids.new_id("route-cand"),
            instance_id=self.target.peer_kind,
            representative_profile_id=self.target.profile.profile_id,
            eligible=eligible,
            exclusion_reason=None if eligible else "admission_closed",
            usage_evidence=usage_evidence,
            in_flight_reservations=0,
            evidence_refs=(),
        )
        
        configuration = ConfigurationSnapshot(
            revision=admission_snapshot.configuration_revision,
            digest=admission_snapshot.configuration_digest,
        )

        return RouteRequest(
            client_request_id=self.client_request_id,
            configuration=configuration,
            admission_snapshot=admission_snapshot,
            required_capability_tier=self.required_capability_tier,
            requested_capabilities=(),
            profile_constraints={},
            required_readiness_binding=None,
            candidates=(candidate,),
            routing_policy_id=self.policy_id,
            routing_policy_revision=self.policy_revision,
        )


def assemble_ask_prompt(
    request: DirectAskRequest,
    target: ResolvedPeerTarget,
    runtime: "Runtime",
    policy: PromptPolicy,
    config: AskConfig,
) -> str:
    user_directives_lines: list[str] = []
    runtime_directives_lines: list[str] = []
    lessons_lines: list[str] = []
    hub_context_lines: list[str] = []
    handoff_lines: list[str] = []
    task_context_lines: list[str] = []
    continuity_lines: list[str] = []

    # 1. User Directives from _sys/ai/user-directives.md
    user_dir_path = request.workspace_root / "_sys" / "ai" / "user-directives.md"
    if user_dir_path.exists():
        text = user_dir_path.read_text(encoding="utf-8", errors="replace").strip()
        if text:
            user_directives_lines.extend(["[USER DIRECTIVES]", text])

    # 2. Runtime Directives from DirectiveService
    max_rd_count = 10
    max_rd_chars = 2000
    try:
        active_directives = [
            t for t in runtime.directive_service.list_all()
            if t.state.get("lifecycle") == "ACTIVE"
        ]
    except Exception:
        active_directives = []

    if active_directives:
        rd_entries: list[str] = []
        rd_chars = 0
        for target_state in active_directives:
            content = target_state.state.get("content", {})
            if isinstance(content, Mapping):
                rule = content.get("rule") or content.get("title") or ""
            else:
                rule = ""
            dir_id = target_state.state.get("directive_id") or target_state.target_id.split(":")[-1]
            entry = f"- [{dir_id}] {rule}"
            if rd_chars + len(entry) > max_rd_chars or len(rd_entries) >= max_rd_count:
                rd_entries.append(f"- [...{len(active_directives) - len(rd_entries)} more directives omitted]")
                break
            rd_entries.append(entry)
            rd_chars += len(entry)
        if rd_entries:
            runtime_directives_lines.extend(["[RUNTIME DIRECTIVES]", "\n".join(rd_entries)])

    # 3. Peer Lessons via inject_lessons
    try:
        from peerhub.application.lesson_inject import (
            inject_lessons,
            LessonInjectionContext,
            LessonInjectionPolicy,
        )
        lessons_block = inject_lessons(
            runtime.governance_broker,
            target_peer_id=target.peer_kind,
            workspace_id=str(request.workspace_root),
            context=LessonInjectionContext(),
            policy=LessonInjectionPolicy(),
        )
        if lessons_block:
            lessons_lines.append(lessons_block)
    except Exception:
        pass

    # 4. Room Context & Handoff via RoomsService
    if request.room_id:
        room_summary = runtime.rooms_service.get_room_summary(request.room_id)
        try:
            participants = runtime.rooms_service.list_participants(request.room_id)
            members = [
                str(p.get("instance_id") or p)
                for p in participants
            ]
            members_str = ", ".join(members) if members else "none"
        except Exception:
            members_str = "none"

        if room_summary is not None:
            s = room_summary.state
            hub_context_lines.extend([
                "[HUB CONTEXT]",
                f"Room ID: {request.room_id}",
                f"Members: {members_str}",
                f"Mission: {s.get('mission') or 'none'}",
                f"Blocked: {s.get('blocked') or 'none'}",
                f"Phase: {s.get('phase') or 'none'}",
            ])
        else:
            hub_context_lines.extend([
                "[HUB CONTEXT]",
                f"Room ID: {request.room_id}",
                f"Members: {members_str}",
            ])

        try:
            continuity = runtime.rooms_service.context_fill(
                request.room_id,
                session_id=request.session_id or "direct_ask",
            )
            sections = continuity.get("sections")
            if isinstance(sections, Mapping):
                sec_lines: list[str] = []
                goal_val = sections.get("GOAL")
                if isinstance(goal_val, Mapping) and goal_val.get("goal"):
                    sec_lines.extend(["## GOAL", str(goal_val["goal"])])
                for sec_name, sec_val in sections.items():
                    if sec_name != "GOAL" and isinstance(sec_val, Mapping):
                        items = sec_val.get("items")
                        if isinstance(items, (list, tuple)) and items:
                            sec_lines.append(f"## {sec_name}")
                            sec_lines.extend(f"- {item}" for item in items)
                if sec_lines:
                    handoff_lines.extend(["[HANDOFF]", "\n".join(sec_lines)])
        except Exception:
            pass

    # 5. Task Context via TaskService
    if request.task_id:
        task_target = runtime.task_service.get_target(request.task_id)
        if task_target is not None:
            ts = task_target.state
            task_context_lines.extend([
                "[TASK CONTEXT]",
                f"Task ID: {request.task_id}",
                f"Summary: {ts.get('summary') or 'none'}",
                f"Phase: {ts.get('phase') or 'none'}",
                f"State: {ts.get('state') or 'none'}",
            ])

    # 6. Cross-dispatch continuity: durable room/task checkpoints recorded by
    # an EARLIER dispatch (item G). Distinct from [HANDOFF] above, which is
    # the room's live continuity-note projection rather than a recorded
    # checkpoint, and available even when this ask names no room or task.
    try:
        snapshot = resolve_continuity(
            runtime.governance_broker,
            peer_kind=target.peer_kind,
            room_id=request.room_id,
            task_id=request.task_id,
            config=config.continuity,
        )
        continuity_block = render_continuity_block(
            snapshot,
            config=config.continuity,
            include_room_markdown=not handoff_lines,
        )
        if continuity_block:
            continuity_lines.append(continuity_block)
    except Exception:
        pass

    has_context = bool(
        user_directives_lines
        or runtime_directives_lines
        or lessons_lines
        or hub_context_lines
        or handoff_lines
        or task_context_lines
        or continuity_lines
    )
    if not has_context:
        return request.prompt

    query_first = bool(getattr(policy, "query_first", False) or target.peer_kind in ("ag", "agy"))
    blocks: list[str] = []
    if query_first:
        blocks.extend(["[USER QUERY]\n" + request.prompt])
        if user_directives_lines:
            blocks.append("\n".join(user_directives_lines))
        if runtime_directives_lines:
            blocks.append("\n".join(runtime_directives_lines))
        if lessons_lines:
            blocks.append("\n".join(lessons_lines))
        if hub_context_lines:
            blocks.append("\n".join(hub_context_lines))
        if handoff_lines:
            blocks.append("\n".join(handoff_lines))
        if task_context_lines:
            blocks.append("\n".join(task_context_lines))
        if continuity_lines:
            blocks.append("\n".join(continuity_lines))
    else:
        if hub_context_lines:
            blocks.append("\n".join(hub_context_lines))
        if user_directives_lines:
            blocks.append("\n".join(user_directives_lines))
        if runtime_directives_lines:
            blocks.append("\n".join(runtime_directives_lines))
        if lessons_lines:
            blocks.append("\n".join(lessons_lines))
        if handoff_lines:
            blocks.append("\n".join(handoff_lines))
        if task_context_lines:
            blocks.append("\n".join(task_context_lines))
        if continuity_lines:
            blocks.append("\n".join(continuity_lines))
        blocks.extend(["[USER QUERY]\n" + request.prompt])

    return "\n\n".join(blocks)


def compute_session_adapter_fingerprint(
    peer_kind: str,
    profile_id: str,
    model_binding: ResolvedModelBinding,
) -> str:
    """Derive deterministic session fingerprint folding in model binding (contract.py:278-291)."""
    components = (
        peer_kind,
        profile_id,
        model_binding.model_id or "default",
        model_binding.selection_mode.value,
        model_binding.reasoning_effort or "none",
    )
    return hashlib.sha256(":".join(components).encode("utf-8")).hexdigest()


def resolve_session_lifecycle(
    request: DirectAskRequest,
    target: ResolvedPeerTarget,
    model_binding: ResolvedModelBinding,
    runtime: "Runtime",
) -> tuple[SessionAction, SessionHint | None, SessionBindingKey | None]:
    """Resolve SessionAction, SessionHint, and SessionBindingKey adhering to Model-Config Invariants."""
    from peerhub.adapters.contract import Capability
    if Capability.SESSION not in target.adapter.descriptor.capabilities:
        return SessionAction.NONE, None, None

    conversation_scope = request.session_id or request.room_id
    if not conversation_scope and not request.resume and request.session_action is None:
        return SessionAction.NONE, None, None

    fingerprint = compute_session_adapter_fingerprint(
        target.peer_kind,
        target.profile.profile_id,
        model_binding,
    )

    scope_id = conversation_scope or "direct_ask"
    key = SessionBindingKey(
        workspace_scope_id=str(request.workspace_root),
        instance_id=target.peer_kind,
        profile_id=target.profile.profile_id,
        conversation_scope=scope_id,
    )

    with runtime.state_store.read_unit_of_work() as unit:
        existing = unit.get_session_binding(key)

    should_resume = bool(
        request.resume
        or request.session_action == SessionAction.RESUME
        or (existing is not None and request.session_action is None)
    )

    if should_resume and existing is not None:
        if existing.adapter_fingerprint == fingerprint:
            hint = SessionHint(
                external_session_id=existing.session_id,
                adapter_fingerprint=fingerprint,
                session_generation=existing.session_generation,
            )
            return SessionAction.RESUME, hint, key
        else:
            # Model-Config Invariant: changed model binding forces fresh session
            hint = SessionHint(
                external_session_id=None,
                adapter_fingerprint=fingerprint,
                session_generation=existing.session_generation + 1,
            )
            return SessionAction.CREATE, hint, key

    if should_resume and request.session_id:
        hint = SessionHint(
            external_session_id=request.session_id,
            adapter_fingerprint=fingerprint,
            session_generation=1,
        )
        return SessionAction.RESUME, hint, key

    action = request.session_action or SessionAction.CREATE
    hint = SessionHint(
        external_session_id=None,
        adapter_fingerprint=fingerprint,
        session_generation=1,
    )
    return action, hint, key


def execute_direct_ask(
    request: DirectAskRequest,
    *,
    clock: Clock,
    ids: IdSource,
    authenticated_subject: AuthenticatedSubject,
    cancellation_hook: "Callable[[ProcessSupervisor], None] | None" = None,
) -> DirectAskResult:
    """Execute a single peerhub ask command pipeline."""
    
    # Let errors propagate naturally (NOT_STARTED handling belongs to CLI)
    target = resolve_peer_target(
        request.peer_name,
        profile_id=request.profile_id,
    )
    
    policy = target.adapter.prompt_policy(target.profile)
    ask_config = load_ask_config()

    paths = PathLayout.for_workspace(request.workspace_root)

    context = RuntimeContext(
        workspace_home_id="cli",
        paths=paths,
        clock=clock,
        ids=ids,
    )
    
    admission_config = build_direct_ask_admission_config(target, clock=clock, ids=ids)

    from peerhub.runtime import create_runtime  # local: avoids a runtime<->arbiter_review<->direct_ask import cycle

    runtime = create_runtime(
        context,
        admission_config=admission_config,
    )
    
    try:
        now = clock.now()
        client_request_id = ids.new_id("req")
        
        policy_id = admission_config.health_policy.policy_id
        policy_revision = admission_config.health_policy.revision

        route_request_factory = _DirectAskRouteRequestFactory(
            target=target,
            clock=clock,
            ids=ids,
            client_request_id=client_request_id,
            policy_id=policy_id,
            policy_revision=policy_revision,
            required_capability_tier=request.required_capability_tier,
        )

        envelope = CommandEnvelope(
            protocol_major=1,
            protocol_minor=0,
            schema_version="1.0.0",
            client_request_id=client_request_id,
            correlation_id=ids.new_id("corr"),
            client_id="peerhub-cli",
            actor_id="cli-direct-ask",
            scope={"workspace_root": str(request.workspace_root)},
            method="peer.ask",
            params={
                "required_capability_tier": (
                    request.required_capability_tier.name
                )
            },
            idempotency_key=ids.new_id("idemp"),
            expected_policy_revision=policy_revision,
            expected_configuration_revision=1,
            client_timestamp=now,
        )

        completion_contract = CompletionContract(
            contract_id=ids.new_id("contract"),
            kind=CompletionContractKind.DELIVERY_ONLY,
            requirements=(),
            replay_safe=(
                request.required_capability_tier is CapabilityTier.READ_ONLY
            ),
        )

        admission_result = runtime.application_workflows.admit_request(
            envelope,
            route_request_factory=route_request_factory,
            required_capability_tier=request.required_capability_tier,
            authenticated_subject=authenticated_subject,
            completion_contract=completion_contract,
            dispatch_policy_revision=1,
            session_id=ids.new_id("session"),
            owner_principal_id=authenticated_subject.principal_id,
            owner_instance_id="cli-instance",
            authority_epoch=1,
            heartbeat_timeout_ms=30000,
        )

        if not admission_result.dispatch_admission:
            raise RuntimeError("request was not admitted")
            
        admitted_request = admission_result.dispatch_admission[0]
        command_id = admitted_request.command_id
        capability_lease_id = (
            admission_result.dispatch_admission[3].capability_lease_id
        )
        route = admission_result.route
        if route is None:
            raise RuntimeError("route plan was missing")

        runtime.application_workflows.prepare_for_dispatch(
            command_id,
            route_decision_id=route.decision.decision_id,
            route_request_factory=route_request_factory,
            telemetry_limit=100,
        )

        model_binding = ModelConfigService(runtime.peer_registry_service).resolve(
            node_id=target.peer_kind,
            profile_id=target.profile.profile_id,
        )

        assembled_prompt = assemble_ask_prompt(
            request,
            target=target,
            runtime=runtime,
            policy=policy,
            config=ask_config,
        )
        # Item H: past the profile's inline ceiling, stage the payload
        # verbatim and carry a reference rather than failing the dispatch.
        # AdapterRequest requires exactly one of content/reference, so these
        # two are always mutually exclusive.
        prompt_bytes = len(assembled_prompt.encode("utf-8"))
        prompt_content: str | None = assembled_prompt
        prompt_reference: str | None = None
        if prompt_bytes > policy.max_inline_utf8_bytes:
            if not ask_config.prompt_staging.enabled:
                raise ValueError(f"prompt invalid: exceeds {policy.max_inline_utf8_bytes} bytes")
            staging_root = (
                config_paths.resolve_workspace_temp(request.workspace_root).path
                if ask_config.prompt_staging.location == "temp"
                else request.workspace_root
            )
            # Bounded startup janitor (item 3): reclaim any staged file left
            # behind by a prior dispatch whose outcome was genuinely
            # uncertain, before staging this one. Synchronous, on-demand --
            # never a background daemon.
            sweep_stale_staged_prompts(
                staging_root,
                ask_config.prompt_staging.relative_dir,
                max_age_seconds=ask_config.prompt_staging.janitor_max_age_seconds,
            )
            staged = stage_prompt(
                assembled_prompt,
                root=staging_root,
                relative_dir=ask_config.prompt_staging.relative_dir,
                request_id=client_request_id,
            )
            prompt_content = None
            prompt_reference = str(staged.path)

        session_action, session_hint, session_key = resolve_session_lifecycle(
            request,
            target=target,
            model_binding=model_binding,
            runtime=runtime,
        )

        adapter_request = AdapterRequest(
            request_id=client_request_id,
            prompt_content=prompt_content,
            prompt_reference=prompt_reference,
            workspace_scope="default",
            profile_id=target.profile.profile_id,
            requested_session_action=session_action,
            completion_contract=completion_contract,
            model_binding=model_binding,
        )

        materializer = ArtifactMaterializer(
            unit_of_work_factory=runtime.state_store.unit_of_work,
            workspace_root=request.workspace_root,
            clock=clock.now,
        )

        initial_attempt = AttemptDispatchPlan(
            route_decision_id=route.decision.decision_id,
            capability_lease_id=capability_lease_id,
            peer_instance_id=admitted_request.selected_peer_instance_id,
            adapter_request=adapter_request,
            peer_adapter=target.adapter,
            profile=target.profile,
            session=session_hint if session_action is SessionAction.RESUME else None,
        )

        retry_target_resolver = DirectAskRetryTargetResolver(
            workspace_root=request.workspace_root,
        )

        multi_result = runtime.application_workflows.dispatch_with_retries(
            command_id,
            initial_attempt=initial_attempt,
            route_request_factory=route_request_factory,
            current_policy_revision=policy_revision,
            materializer=materializer,
            limits=request.limits,
            workspace_roots={"default": request.workspace_root},
            content_providers={},
            completion_contract=completion_contract,
            heartbeat_timeout_ms=30000,
            max_attempts=request.max_attempts,
            retry_target_resolver=retry_target_resolver,
            cancellation_hook=cancellation_hook,
        )

        last_record = multi_result.attempts[-1]
        execution_result = last_record.execution

        is_failed = (
            execution_result.attempt.state in (
                RequestState.FAILED,
                RequestState.INCOMPLETE,
                RequestState.INTERRUPTED,
            )
            or execution_result.request.state in (
                RequestState.FAILED,
                RequestState.FAILED_PRE_DISPATCH,
                RequestState.START_UNCERTAIN,
                RequestState.INCOMPLETE,
            )
            or multi_result.stop_reason in (
                RetryLoopStopReason.ATTEMPT_LIMIT_REACHED,
                RetryLoopStopReason.ROUTE_EXHAUSTED,
            )
        )
        # Item 3 (dotdir consolidation, ratified 2026-09-09): remove a
        # staged prompt once its dispatch reaches a definite outcome
        # (success, definite failure, or cancellation). NEVER for
        # START_UNCERTAIN specifically -- the supervised process might
        # still be running and reading the file; the startup janitor sweep
        # (above) reclaims that case later, bounded by age, not this call.
        if prompt_reference is not None and (
            execution_result.request.state is not RequestState.START_UNCERTAIN
        ):
            remove_staged_prompt(prompt_reference)
        if is_failed:
            runtime.health_service.classify_and_open_circuit(
                attempted_trace=(
                    HealthStageObservation(
                        stage=HealthStage.CALL_PROVIDER,
                        status=HealthStageStatus.FAILED,
                    ),
                ),
                evidence_subject=EvidenceSubject(
                    scope=PolicyScope.PROFILE,
                    subject=target.profile.profile_id,
                ),
                receipt=PolicyReceipt(
                    incident=f"direct-ask-{command_id}",
                    gate_generation=1,
                    timestamp=clock.now(),
                    fingerprint=f"direct_ask_failure:{command_id}",
                ),
            )

        if session_key is not None and session_action in (SessionAction.CREATE, SessionAction.RESUME):
            ext_sess_id = request.session_id or command_id
            if execution_result.decoded_output:
                for event in execution_result.decoded_output.events:
                    if event.kind == DecoderEventKind.SESSION_IDENTITY:
                        payload = event.payload
                        val = payload.get("session_id") or payload.get("session_identity") or payload.get("id")
                        if isinstance(val, str) and val:
                            ext_sess_id = val
                            break

            with runtime.state_store.read_unit_of_work() as unit:
                existing_binding = unit.get_session_binding(session_key)
            new_gen = session_hint.session_generation if session_hint and session_hint.session_generation else 1
            new_rev = (existing_binding.revision + 1) if existing_binding else 1
            new_fp = (
                session_hint.adapter_fingerprint
                if session_hint and session_hint.adapter_fingerprint
                else compute_session_adapter_fingerprint(target.peer_kind, target.profile.profile_id, model_binding)
            )

            new_binding = SessionBindingSnapshot(
                key=session_key,
                session_id=ext_sess_id,
                current_lease_id=None,
                adapter_fingerprint=new_fp,
                readiness_binding=f"direct-ask-{command_id}",
                session_generation=new_gen,
                revision=new_rev,
                state=SessionBindingState.ACTIVE,
                updated_at=clock.now(),
            )
            with runtime.state_store.unit_of_work() as unit:
                if existing_binding is not None:
                    unit.cas_update_session_binding(existing_binding, new_binding)
                else:
                    unit.add_session_binding(new_binding)
                unit.commit()

        response_text = None
        if execution_result.decoded_output:
            response_text = execution_result.decoded_output.canonical_text

            if ask_config.transcript_storage.enabled:
                with runtime.state_store.unit_of_work() as unit:
                    unit.add_dispatch_transcript(
                        attempt_id=execution_result.attempt.attempt_id,
                        peer_kind=target.peer_kind,
                        profile_id=target.profile.profile_id,
                        transcript_text=response_text,
                        created_at=now,
                    )
                    unit.commit()


        return DirectAskResult(
            command_id=command_id,
            attempt_id=execution_result.attempt.attempt_id,
            peer_kind=target.peer_kind,
            profile_id=target.profile.profile_id,
            response_text=response_text,
            request_state=execution_result.request.state,
            error_code=None,  # let exceptions bubble for cli logic
            execution_certainty=None,
        )
    finally:
        runtime.close()
