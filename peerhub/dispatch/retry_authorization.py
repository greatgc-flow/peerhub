"""Atomic same-target and failover retry authority coordination."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, TypeAlias, assert_never

from peerhub.core.context import Clock, IdSource
from peerhub.core.errors import (
    AttemptLimitReachedError,
    CapabilityAuthorizationDeniedError,
    InvalidMutationError,
    PolicyStaleError,
    RecordNotFoundError,
    RetryPolicyConflictError,
    RetryRouteUnavailableError,
    RouteExhaustedError,
    StaleRevisionError,
)
from peerhub.core.protocol import CommandID, ErrorCode, RevisionValue, require_text
from peerhub.routing.contract import (
    RouteCandidateDecision,
    RouteDecision,
    RouteRequest,
    canonical_route_decision_digest,
)
from peerhub.routing.model import select_route, validate_route_for_dispatch
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


FAILED_TARGET_EXCLUDED_BY_RETRY = "FAILED_TARGET_EXCLUDED_BY_RETRY"


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
class FailoverRoute:
    """Fresh route facts used to replace a failed selected instance."""

    failed_route_decision_id: str
    failover_route_request: RouteRequest

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "failed_route_decision_id",
            require_text(
                self.failed_route_decision_id,
                "failed_route_decision_id",
            ),
        )
        if not isinstance(self.failover_route_request, RouteRequest):  # pyright: ignore[reportUnnecessaryIsInstance]
            raise ValueError("failover_route_request must be RouteRequest")


RetryRouteIntent: TypeAlias = SameTargetRoute | FailoverRoute


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

    def add_route_decision(self, decision: RouteDecision) -> None:
        """Insert one immutable replacement route and its candidate audit."""

        ...


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
    """Authorize one retry without a partial durable authority or route."""

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

    @staticmethod
    def _require_failed_instance_excluded(
        failed_decision: RouteDecision,
        failover_request: RouteRequest,
        *,
        failed_instance_id: str,
    ) -> None:
        """Require complete, explicit exclusion of the failed instance."""

        failed_audits = tuple(
            candidate
            for candidate in failed_decision.candidates
            if candidate.instance_id == failed_instance_id
        )
        current_by_id = {
            candidate.candidate_id: candidate
            for candidate in failover_request.candidates
        }
        prior_candidates_are_excluded = all(
            (
                current := current_by_id.get(candidate.candidate_id)
            )
            is not None
            and current.instance_id == candidate.instance_id
            and (
                current.representative_profile_id
                == candidate.representative_profile_id
            )
            and not current.eligible
            and (
                current.exclusion_reason
                == FAILED_TARGET_EXCLUDED_BY_RETRY
            )
            for candidate in failed_audits
        )
        every_failed_instance_candidate_is_excluded = all(
            not candidate.eligible
            and (
                candidate.exclusion_reason
                == FAILED_TARGET_EXCLUDED_BY_RETRY
            )
            for candidate in failover_request.candidates
            if candidate.instance_id == failed_instance_id
        )
        if (
            not failed_audits
            or not prior_candidates_are_excluded
            or not every_failed_instance_candidate_is_excluded
        ):
            raise InvalidMutationError(
                "failed route instance candidates must remain explicitly excluded "
                f"with {FAILED_TARGET_EXCLUDED_BY_RETRY}"
            )

    @classmethod
    def _validate_failover_route(
        cls,
        unit: RetryAuthorizationUnitOfWork,
        context: _LoadedRetryContext,
        route_intent: FailoverRoute,
        *,
        decision_id: str,
        created_at: int,
    ) -> tuple[
        RouteDecision,
        RouteCandidateDecision,
        ValidatedRetryRouteBinding,
    ]:
        """Validate failover input and select a replacement without writes."""

        request = context.request
        failed_decision = unit.get_route_decision(
            route_intent.failed_route_decision_id
        )
        if failed_decision is None:
            raise RecordNotFoundError(
                "route_decision",
                route_intent.failed_route_decision_id,
            )
        failed_selected = cls._selected_candidate(failed_decision)
        failed_digest = canonical_route_decision_digest(failed_decision)
        expected_binding = (
            failed_decision.client_request_id,
            failed_decision.configuration.revision,
            failed_decision.required_capability_tier,
            failed_selected.instance_id,
            failed_selected.representative_profile_id,
            failed_digest,
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
                "dispatch request is not bound to the supplied failed route decision"
            )

        failover_request = route_intent.failover_route_request
        if failover_request.client_request_id != request.client_request_id:
            raise InvalidMutationError(
                "failover route request belongs to a different client request"
            )
        if (
            failover_request.required_capability_tier
            is not request.required_capability_tier
        ):
            raise InvalidMutationError(
                "failover route request changes the frozen capability tier"
            )
        persisted_snapshot = unit.get_admission_snapshot(
            failover_request.admission_snapshot.snapshot_id
        )
        if persisted_snapshot is None:
            raise RecordNotFoundError(
                "admission_snapshot",
                failover_request.admission_snapshot.snapshot_id,
            )
        if persisted_snapshot != failover_request.admission_snapshot:
            raise InvalidMutationError(
                "failover route request admission snapshot differs from its durable audit"
            )
        if (
            failover_request.configuration.revision
            != persisted_snapshot.configuration_revision
            or failover_request.configuration.digest
            != persisted_snapshot.configuration_digest
        ):
            raise InvalidMutationError(
                "failover route request configuration differs from its admission snapshot"
            )
        snapshot_targets = {
            (entry.instance_id, entry.profile_id)
            for entry in persisted_snapshot.entries
        }
        if any(
            (
                candidate.instance_id,
                candidate.representative_profile_id,
            )
            not in snapshot_targets
            for candidate in failover_request.candidates
        ):
            raise InvalidMutationError(
                "failover route candidate target/profile is absent from the durable admission snapshot"
            )

        cls._require_failed_instance_excluded(
            failed_decision,
            failover_request,
            failed_instance_id=failed_selected.instance_id,
        )
        result = select_route(
            failover_request,
            decision_id=decision_id,
            created_at=created_at,
        )
        if result.error_code is ErrorCode.ROUTE_EXHAUSTED:
            raise RouteExhaustedError(str(request.command_id))
        replacement = cls._selected_candidate(result.decision)
        if replacement.instance_id == failed_selected.instance_id:
            raise InvalidMutationError(
                "failover route selected the failed peer instance"
            )
        replacement_digest = canonical_route_decision_digest(result.decision)
        route_binding = ValidatedRetryRouteBinding(
            configuration_revision=result.decision.configuration.revision,
            selected_peer_instance_id=replacement.instance_id,
            selected_profile_id=replacement.representative_profile_id,
            route_decision_digest=replacement_digest,
        )
        return result.decision, replacement, route_binding

    def _fresh_grant(
        self,
        context: _LoadedRetryContext,
        *,
        selected_peer_instance_id: str,
        selected_profile_id: str,
        expected_current_peer_kind: str | None,
        current_policy_revision: RevisionValue,
    ) -> tuple[str, CapabilityTier, EnforcementLevel, str]:
        request = context.request
        if current_policy_revision != request.policy_revision:
            raise PolicyStaleError(
                request.policy_revision,
                current_policy_revision,
            )
        evidence = self._enforcement_evidence.resolve(
            peer_instance_id=selected_peer_instance_id,
            profile_id=selected_profile_id,
        )
        if evidence.peer_instance_id != selected_peer_instance_id:
            raise CapabilityAuthorizationDeniedError(
                str(request.command_id),
                "machine-owned enforcement evidence belongs to a different instance",
            )
        if (
            expected_current_peer_kind is not None
            and expected_current_peer_kind != evidence.peer_kind
        ):
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
            selected_peer_instance_id=selected_peer_instance_id,
            selected_profile_id=selected_profile_id,
            policy_revision=request.policy_revision,
            required_tier=request.required_capability_tier,
            minimum_enforcement=floor,
        )
        expected_decision_binding = (
            request.authenticated_principal,
            evidence.peer_kind,
            selected_peer_instance_id,
            selected_profile_id,
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
        route_intent: RetryRouteIntent,
        expected_request_revision: int,
        expected_previous_attempt_revision: int,
        expected_highest_attempt_number: int,
        frozen_max_attempts: int,
        current_policy_revision: RevisionValue,
        reconciliation_complete: bool,
        heartbeat_timeout_ms: int,
    ) -> RetryAuthorizationBundle:
        """Authorize and persist one same-target or failover retry."""

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
            persist_replacement_route = False
            match route_intent:
                case SameTargetRoute():
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
                        selected_peer_instance_id=(
                            context.request.selected_peer_instance_id
                        ),
                        selected_profile_id=(
                            context.request.selected_profile_id
                        ),
                        expected_current_peer_kind=(
                            context.current_capability.selected_peer_kind
                        ),
                        current_policy_revision=current_policy_revision,
                    )
                    timestamp = self._clock.now()
                    route_binding = ValidatedRetryRouteBinding(
                        configuration_revision=(
                            context.request.configuration_revision
                        ),
                        selected_peer_instance_id=(
                            context.request.selected_peer_instance_id
                        ),
                        selected_profile_id=(
                            context.request.selected_profile_id
                        ),
                        route_decision_digest=(
                            context.request.route_decision_digest
                        ),
                    )
                case FailoverRoute():
                    timestamp = self._clock.now()
                    (
                        route_decision,
                        replacement,
                        route_binding,
                    ) = self._validate_failover_route(
                        unit,
                        context,
                        route_intent,
                        decision_id=self._ids.new_id("route-decision"),
                        created_at=timestamp,
                    )
                    (
                        peer_kind,
                        authorized_tier,
                        minimum_enforcement,
                        issuer_id,
                    ) = self._fresh_grant(
                        context,
                        selected_peer_instance_id=replacement.instance_id,
                        selected_profile_id=(
                            replacement.representative_profile_id
                        ),
                        expected_current_peer_kind=None,
                        current_policy_revision=current_policy_revision,
                    )
                    persist_replacement_route = True
                case _ as unreachable:
                    assert_never(unreachable)

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

            if persist_replacement_route:
                unit.add_route_decision(route_decision)
                self._faults.hit(FaultPoint.AFTER_RETRY_ROUTE_WRITE)
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
    "FAILED_TARGET_EXCLUDED_BY_RETRY",
    "FailoverRoute",
    "RetryAuthorizationBundle",
    "RetryAuthorizationCoordinator",
    "RetryAuthorizationUnitOfWork",
    "RetryRouteIntent",
    "SameTargetRoute",
]
