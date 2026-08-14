"""Atomic same-target retry authority coordination."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from peerhub.core.context import Clock, IdSource
from peerhub.core.errors import (
    AttemptLimitReachedError,
    CapabilityAuthorizationDeniedError,
    InvalidMutationError,
    PolicyStaleError,
    RecordNotFoundError,
    RetryPolicyConflictError,
    RetryRouteUnavailableError,
    StaleRevisionError,
)
from peerhub.core.protocol import CommandID, ErrorCode, RevisionValue, require_text
from peerhub.routing.contract import (
    RouteCandidateDecision,
    RouteDecision,
    RouteRequest,
    canonical_route_decision_digest,
)
from peerhub.routing.model import validate_route_for_dispatch
from peerhub.state.contract import StateStore

from .capability import (
    CapabilityLease,
    CapabilityLeaseViolation,
    CapabilityPolicy,
    CapabilityTier,
    EnforcementLevel,
    PeerEnforcementEvidenceProvider,
    require_enforcement_floor,
    validate_capability_binding,
)
from .contract import (
    AdmissionReceipt,
    AttemptSnapshot,
    LeaseReservationRequest,
    LeaseSnapshot,
    RequestSnapshot,
)
from .helpers import (
    raise_attempt_cas,
    raise_request_cas,
    require_attempt,
    require_lease,
    require_request,
)
from .model import (
    ValidatedRetryRouteBinding,
    authorize_retry as reduce_authorize_retry,
    reserve_lease,
    validate_retry_authorizable,
)
from .unit_of_work import (
    DispatchReadUnitOfWork,
    DispatchUnitOfWork,
    FaultInjector,
    FaultPoint,
    RetryRoutingReadUnitOfWork,
    _NoFaultInjector,  # pyright: ignore[reportPrivateUsage]
)


@dataclass(frozen=True)
class SameTargetRoute:
    """Fresh route facts used to reauthorize the currently selected target."""

    route_decision_id: str
    current_route_request: RouteRequest

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "route_decision_id",
            require_text(self.route_decision_id, "route_decision_id"),
        )
        if not isinstance(self.current_route_request, RouteRequest):  # pyright: ignore[reportUnnecessaryIsInstance]
            raise ValueError("current_route_request must be RouteRequest")


@dataclass(frozen=True)
class RetryAuthorizationBundle:
    """The records committed by one retry-authorization transaction."""

    request: RequestSnapshot
    previous_attempt: AttemptSnapshot
    session_lease: LeaseSnapshot
    capability_lease: CapabilityLease
    route_decision: RouteDecision


class RetryAuthorizationUnitOfWork(
    DispatchUnitOfWork,
    RetryRoutingReadUnitOfWork,
    Protocol,
):
    """One transaction spanning dispatch authority and routing audit reads."""


@dataclass(frozen=True)
class _LoadedRetryContext:
    request: RequestSnapshot
    previous_attempt: AttemptSnapshot
    attempts: tuple[AttemptSnapshot, ...]
    highest_attempt_number: int
    receipt: AdmissionReceipt
    current_lease: LeaseSnapshot
    current_capability: CapabilityLease


class RetryAuthorizationCoordinator:
    """Authorize one same-target retry without a partial durable state."""

    def __init__(
        self,
        store: StateStore[
            RetryAuthorizationUnitOfWork,
            DispatchReadUnitOfWork,
        ],
        *,
        clock: Clock,
        ids: IdSource,
        capability_policy: CapabilityPolicy,
        enforcement_evidence: PeerEnforcementEvidenceProvider,
        fault_injector: FaultInjector | None = None,
    ) -> None:
        self._store = store
        self._clock = clock
        self._ids = ids
        self._capability_policy = capability_policy
        self._enforcement_evidence = enforcement_evidence
        self._faults = fault_injector or _NoFaultInjector()

    @staticmethod
    def _selected_candidate(decision: RouteDecision) -> RouteCandidateDecision:
        selected_id = decision.selected_candidate_id
        if selected_id is None:
            raise InvalidMutationError(
                "bound route decision has no selected candidate"
            )
        matches = tuple(
            candidate
            for candidate in decision.candidates
            if candidate.candidate_id == selected_id
        )
        if len(matches) != 1:
            raise InvalidMutationError(
                "bound route decision does not contain exactly one selected candidate"
            )
        return matches[0]

    @staticmethod
    def _load_common_context(
        unit: RetryAuthorizationUnitOfWork,
        command_id: CommandID | str,
        previous_attempt_id: str,
        *,
        expected_request_revision: int,
        expected_previous_attempt_revision: int,
        expected_highest_attempt_number: int,
        frozen_max_attempts: int,
        reconciliation_complete: bool,
    ) -> _LoadedRetryContext:
        request = require_request(unit, command_id)
        previous_attempt = require_attempt(unit, previous_attempt_id)
        if previous_attempt.command_id != request.command_id:
            raise InvalidMutationError(
                "previous retry attempt belongs to a different command"
            )
        if request.revision != expected_request_revision:
            raise StaleRevisionError(
                str(request.command_id),
                expected_request_revision,
                request.revision,
            )
        if previous_attempt.revision != expected_previous_attempt_revision:
            raise StaleRevisionError(
                previous_attempt.attempt_id,
                expected_previous_attempt_revision,
                previous_attempt.revision,
            )

        attempts = unit.list_attempts(request.command_id)
        if not attempts:
            raise InvalidMutationError("retry requires nonempty attempt history")
        if any(attempt.command_id != request.command_id for attempt in attempts):
            raise InvalidMutationError(
                "retry attempt history contains a different command"
            )
        actual_numbers = tuple(attempt.attempt_number for attempt in attempts)
        expected_numbers = tuple(range(1, len(attempts) + 1))
        if actual_numbers != expected_numbers:
            raise InvalidMutationError(
                "retry attempt history must be the exact sequence 1..N"
            )
        highest_attempt_number = attempts[-1].attempt_number
        if highest_attempt_number != expected_highest_attempt_number:
            raise StaleRevisionError(
                f"{request.command_id}:attempt-history",
                expected_highest_attempt_number,
                highest_attempt_number,
            )
        if attempts[-1].attempt_id != previous_attempt.attempt_id:
            raise InvalidMutationError(
                "previous_attempt_id must identify the highest durable attempt"
            )

        durable_max_attempts = unit.get_retry_policy_max_attempts(
            request.command_id
        )
        if durable_max_attempts is None:
            raise RecordNotFoundError("retry_policy", str(request.command_id))
        if durable_max_attempts != frozen_max_attempts:
            raise RetryPolicyConflictError(
                str(request.command_id),
                frozen_max_attempts,
                durable_max_attempts,
            )
        if highest_attempt_number + 1 > durable_max_attempts:
            raise AttemptLimitReachedError(
                str(request.command_id),
                highest_attempt_number,
                durable_max_attempts,
            )

        validate_retry_authorizable(
            request,
            previous_attempt,
            reconciliation_complete=reconciliation_complete,
        )

        current_lease = require_lease(unit, request.lease_id)
        current_capability = unit.get_capability_lease_by_session_lease_id(
            current_lease.lease_id
        )
        if current_capability is None:
            raise CapabilityLeaseViolation(
                "retry request's current session lease has no capability"
            )
        if (
            current_capability.authorized_attempt_number
            != highest_attempt_number
        ):
            raise CapabilityLeaseViolation(
                "active capability does not authorize the highest durable attempt"
            )
        receipt = unit.get_admission_receipt(
            current_capability.admission_receipt_id
        )
        if receipt is None:
            raise RecordNotFoundError(
                "admission_receipt",
                current_capability.admission_receipt_id,
            )
        capability_previous_attempt = (
            None
            if current_capability.authorized_attempt_number == 1
            else attempts[-2]
        )
        validate_capability_binding(
            request,
            receipt,
            current_lease,
            current_capability,
            expected_peer_kind=current_capability.selected_peer_kind,
            previous_attempt=capability_previous_attempt,
        )
        return _LoadedRetryContext(
            request=request,
            previous_attempt=previous_attempt,
            attempts=attempts,
            highest_attempt_number=highest_attempt_number,
            receipt=receipt,
            current_lease=current_lease,
            current_capability=current_capability,
        )

    @staticmethod
    def _validate_same_target_route(
        unit: RetryAuthorizationUnitOfWork,
        context: _LoadedRetryContext,
        route_intent: SameTargetRoute,
    ) -> tuple[RouteDecision, RouteCandidateDecision]:
        request = context.request
        decision = unit.get_route_decision(route_intent.route_decision_id)
        if decision is None:
            raise RecordNotFoundError(
                "route_decision", route_intent.route_decision_id
            )
        selected = RetryAuthorizationCoordinator._selected_candidate(decision)
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
                "dispatch request is not bound to the supplied route decision"
            )

        current_route_request = route_intent.current_route_request
        if current_route_request.client_request_id != request.client_request_id:
            raise InvalidMutationError(
                "current route request belongs to a different client request"
            )
        if (
            current_route_request.required_capability_tier
            is not request.required_capability_tier
        ):
            raise InvalidMutationError(
                "current route request changes the frozen capability tier"
            )
        persisted_snapshot = unit.get_admission_snapshot(
            current_route_request.admission_snapshot.snapshot_id
        )
        if persisted_snapshot is None:
            raise RecordNotFoundError(
                "admission_snapshot",
                current_route_request.admission_snapshot.snapshot_id,
            )
        if persisted_snapshot != current_route_request.admission_snapshot:
            raise InvalidMutationError(
                "current route request admission snapshot differs from its durable audit"
            )

        validation = validate_route_for_dispatch(
            decision,
            current_configuration=current_route_request.configuration,
        )
        if not validation.dispatch_permitted:
            raise RetryRouteUnavailableError(
                str(request.command_id),
                ErrorCode.CONFIGURATION_STALE,
                "same-target retry route uses a stale configuration",
            )

        same_instance = tuple(
            candidate
            for candidate in current_route_request.candidates
            if candidate.instance_id == selected.instance_id
        )
        same_profile = tuple(
            candidate
            for candidate in same_instance
            if (
                candidate.representative_profile_id
                == selected.representative_profile_id
            )
        )
        if not same_instance:
            raise RetryRouteUnavailableError(
                str(request.command_id),
                ErrorCode.PEER_UNAVAILABLE,
                "same-target retry peer is absent from the current route input",
            )
        if not same_profile:
            raise RetryRouteUnavailableError(
                str(request.command_id),
                ErrorCode.PROFILE_UNAVAILABLE,
                "same-target retry profile is absent from the current route input",
            )
        if len(same_profile) != 1:
            raise InvalidMutationError(
                "current route request contains duplicate same-target candidates"
            )
        if not same_profile[0].eligible:
            raise RetryRouteUnavailableError(
                str(request.command_id),
                ErrorCode.PEER_UNAVAILABLE,
                "same-target retry candidate is currently ineligible",
            )
        return decision, selected

    def _fresh_grant(
        self,
        context: _LoadedRetryContext,
        *,
        current_policy_revision: RevisionValue,
    ) -> tuple[str, CapabilityTier, EnforcementLevel, str]:
        request = context.request
        if current_policy_revision != request.policy_revision:
            raise PolicyStaleError(
                request.policy_revision,
                current_policy_revision,
            )
        evidence = self._enforcement_evidence.resolve(
            peer_instance_id=request.selected_peer_instance_id,
            profile_id=request.selected_profile_id,
        )
        if evidence.peer_instance_id != request.selected_peer_instance_id:
            raise CapabilityAuthorizationDeniedError(
                str(request.command_id),
                "machine-owned enforcement evidence belongs to a different instance",
            )
        if context.current_capability.selected_peer_kind != evidence.peer_kind:
            raise CapabilityLeaseViolation(
                "active capability peer kind differs from machine-owned evidence"
            )
        try:
            floor = require_enforcement_floor(
                evidence.peer_kind,
                request.required_capability_tier,
                evidence,
            )
        except CapabilityLeaseViolation as error:
            raise CapabilityAuthorizationDeniedError(
                str(request.command_id), error.invariant
            ) from error
        decision = self._capability_policy.decide(
            subject_principal_id=request.authenticated_principal,
            selected_peer_kind=evidence.peer_kind,
            selected_peer_instance_id=request.selected_peer_instance_id,
            selected_profile_id=request.selected_profile_id,
            policy_revision=request.policy_revision,
            required_tier=request.required_capability_tier,
            minimum_enforcement=floor,
        )
        expected_decision_binding = (
            request.authenticated_principal,
            evidence.peer_kind,
            request.selected_peer_instance_id,
            request.selected_profile_id,
            request.required_capability_tier,
            request.policy_revision,
        )
        actual_decision_binding = (
            decision.subject_principal_id,
            decision.selected_peer_kind,
            decision.selected_peer_instance_id,
            decision.selected_profile_id,
            decision.required_tier,
            decision.policy_revision,
        )
        if not decision.granted:
            raise CapabilityAuthorizationDeniedError(
                str(request.command_id),
                decision.denial_reason or "capability policy denied retry",
            )
        if actual_decision_binding != expected_decision_binding:
            raise CapabilityAuthorizationDeniedError(
                str(request.command_id),
                "capability grant decision does not match the retry target",
            )
        minimum_enforcement = decision.minimum_enforcement
        if minimum_enforcement is None or minimum_enforcement < floor:
            raise CapabilityAuthorizationDeniedError(
                str(request.command_id),
                "capability policy minimum enforcement is below the mandatory floor",
            )
        return (
            evidence.peer_kind,
            request.required_capability_tier,
            minimum_enforcement,
            decision.issuer_id,
        )

    def authorize_retry(
        self,
        command_id: CommandID | str,
        previous_attempt_id: str,
        *,
        route_intent: SameTargetRoute,
        expected_request_revision: int,
        expected_previous_attempt_revision: int,
        expected_highest_attempt_number: int,
        frozen_max_attempts: int,
        current_policy_revision: RevisionValue,
        reconciliation_complete: bool,
        heartbeat_timeout_ms: int,
    ) -> RetryAuthorizationBundle:
        """Authorize and persist a same-target retry in one transaction."""

        with self._store.unit_of_work() as unit:
            context = self._load_common_context(
                unit,
                command_id,
                previous_attempt_id,
                expected_request_revision=expected_request_revision,
                expected_previous_attempt_revision=(
                    expected_previous_attempt_revision
                ),
                expected_highest_attempt_number=(
                    expected_highest_attempt_number
                ),
                frozen_max_attempts=frozen_max_attempts,
                reconciliation_complete=reconciliation_complete,
            )
            route_decision, _ = self._validate_same_target_route(
                unit,
                context,
                route_intent,
            )
            (
                peer_kind,
                authorized_tier,
                minimum_enforcement,
                issuer_id,
            ) = self._fresh_grant(
                context,
                current_policy_revision=current_policy_revision,
            )

            timestamp = self._clock.now()
            fencing_token = unit.allocate_fencing_token()
            lease_id = self._ids.new_id("lease")
            new_lease = reserve_lease(
                LeaseReservationRequest(
                    session_id=context.current_lease.session_id,
                    owner_principal_id=(
                        context.current_lease.fence.owner_principal_id
                    ),
                    owner_instance_id=(
                        context.current_lease.fence.owner_instance_id
                    ),
                    heartbeat_timeout_ms=heartbeat_timeout_ms,
                    command_id=context.request.command_id,
                    authority_epoch=(
                        context.current_lease.fence.authority_epoch
                    ),
                    owner_peer_id=context.current_lease.fence.owner_peer_id,
                ),
                lease_id=lease_id,
                fencing_token=fencing_token,
                created_at=timestamp,
            )
            route_binding = ValidatedRetryRouteBinding(
                configuration_revision=context.request.configuration_revision,
                selected_peer_instance_id=(
                    context.request.selected_peer_instance_id
                ),
                selected_profile_id=context.request.selected_profile_id,
                route_decision_digest=context.request.route_decision_digest,
            )
            updated_request, updated_attempt = reduce_authorize_retry(
                context.request,
                context.previous_attempt,
                new_lease,
                route_binding=route_binding,
                reconciliation_complete=reconciliation_complete,
                updated_at=timestamp,
            )
            capability = CapabilityLease(
                capability_lease_id=self._ids.new_id("capability-lease"),
                command_id=updated_request.command_id,
                admission_receipt_id=context.receipt.admission_receipt_id,
                session_lease_id=new_lease.lease_id,
                subject_principal_id=updated_request.authenticated_principal,
                selected_peer_kind=peer_kind,
                required_tier=updated_request.required_capability_tier,
                authorized_tier=authorized_tier,
                minimum_enforcement=minimum_enforcement,
                selected_peer_instance_id=(
                    updated_request.selected_peer_instance_id
                ),
                selected_profile_id=updated_request.selected_profile_id,
                route_decision_digest=updated_request.route_decision_digest,
                policy_revision=updated_request.policy_revision,
                issuer_id=issuer_id,
                issued_at=timestamp,
                expires_at=self._capability_policy.expires_at(timestamp),
                authorized_attempt_number=(
                    context.highest_attempt_number + 1
                ),
                previous_attempt_id=context.previous_attempt.attempt_id,
            )
            validate_capability_binding(
                updated_request,
                context.receipt,
                new_lease,
                capability,
                expected_peer_kind=peer_kind,
                previous_attempt=context.previous_attempt,
            )

            unit.add_lease(new_lease)
            self._faults.hit(FaultPoint.AFTER_RETRY_LEASE_WRITE)
            unit.add_capability_lease(capability)
            self._faults.hit(FaultPoint.AFTER_RETRY_CAPABILITY_WRITE)
            if updated_attempt != context.previous_attempt:
                if not unit.cas_update_attempt(
                    context.previous_attempt,
                    updated_attempt,
                ):
                    raise_attempt_cas(unit, context.previous_attempt)
                self._faults.hit(
                    FaultPoint.AFTER_RETRY_PREVIOUS_ATTEMPT_CAS
                )
            if not unit.cas_update_request(
                context.request,
                updated_request,
            ):
                raise_request_cas(unit, context.request)
            self._faults.hit(FaultPoint.AFTER_RETRY_REQUEST_CAS)
            self._faults.hit(FaultPoint.BEFORE_COMMIT)
            unit.commit()

        self._faults.hit(FaultPoint.AFTER_COMMIT)
        return RetryAuthorizationBundle(
            request=updated_request,
            previous_attempt=updated_attempt,
            session_lease=new_lease,
            capability_lease=capability,
            route_decision=route_decision,
        )


__all__ = [
    "RetryAuthorizationBundle",
    "RetryAuthorizationCoordinator",
    "RetryAuthorizationUnitOfWork",
    "SameTargetRoute",
]
