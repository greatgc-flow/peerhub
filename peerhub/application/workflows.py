"""Cross-feature Slice 4 admission and dispatch workflows."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, TypeAlias

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
from peerhub.dispatch.contract import (
    AdmissionReceipt,
    AttemptSnapshot,
    CompletionContract,
    LeaseSnapshot,
    RequestSnapshot,
    RequestState,
    SessionBindingKey,
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
    """Result of projection, health freeze, routing, and admission."""

    projected_terminal_events: int
    admission_snapshot: AdmissionSnapshot
    route: RoutePlanResult
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

    @staticmethod
    def _require_admission_binding(
        dispatch_admission: DispatchAdmission,
        decision: RouteDecision,
        selected: RouteCandidateDecision,
        *,
        route_digest: str,
    ) -> None:
        request = dispatch_admission[0]
        expected_binding = (
            decision.client_request_id,
            decision.configuration.revision,
            selected.instance_id,
            selected.representative_profile_id,
            route_digest,
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
                "idempotent dispatch admission is bound to a "
                "different route decision"
            )

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
        """Project, freeze health, route, and admit one request."""

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
        self._require_admission_binding(
            dispatch_admission,
            route.decision,
            selected,
            route_digest=route_digest,
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
