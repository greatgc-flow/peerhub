"""Direct-ask command orchestration."""

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, TYPE_CHECKING

if TYPE_CHECKING:
    from peerhub.dispatch.process import ProcessSupervisor

from peerhub.adapters.registry import resolve_peer_target, ResolvedPeerTarget
from peerhub.adapters.contract import AdapterRequest, SessionAction
from peerhub.application.bootstrap import build_direct_ask_admission_config
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
                and entry.admission_state.value == "OPEN"
                and entry.availability_state.value not in ("STALE", "UNAVAILABLE")
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
    prompt_bytes = len(request.prompt.encode("utf-8"))
    if prompt_bytes > policy.max_inline_utf8_bytes:
        raise ValueError(f"prompt invalid: exceeds {policy.max_inline_utf8_bytes} bytes")

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
            replay_safe=False,
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

        adapter_request = AdapterRequest(
            request_id=client_request_id,
            prompt_content=request.prompt,
            prompt_reference=None,
            workspace_scope="default",
            profile_id=target.profile.profile_id,
            requested_session_action=SessionAction.NONE,
            completion_contract=completion_contract,
        )

        materializer = ArtifactMaterializer(
            unit_of_work_factory=runtime.state_store.unit_of_work,
            workspace_root=request.workspace_root,
            clock=clock.now,
        )
        
        execution_result = runtime.application_workflows.dispatch_and_execute(
            command_id,
            capability_lease_id=capability_lease_id,
            peer_instance_id=admitted_request.selected_peer_instance_id,
            current_policy_revision=1,
            materializer=materializer,
            adapter_request=adapter_request,
            peer_adapter=target.adapter,
            profile=target.profile,
            limits=request.limits,
            workspace_roots={"default": request.workspace_root},
            content_providers={}, 
            completion_contract=completion_contract,
            heartbeat_timeout_ms=30000,
            cancellation_hook=cancellation_hook,
        )

        response_text = None
        if execution_result.decoded_output:
            response_text = execution_result.decoded_output.canonical_text

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
