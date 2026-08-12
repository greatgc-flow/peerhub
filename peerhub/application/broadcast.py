"""Happy-path orchestration for one prompt dispatched to multiple peers."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from peerhub.adapters.contract import AdapterRequest, SessionAction
from peerhub.adapters.registry import ResolvedPeerTarget, resolve_peer_target
from peerhub.application.direct_ask import _DirectAskRouteRequestFactory  # pyright: ignore[reportPrivateUsage]
from peerhub.core.context import Clock, IdSource
from peerhub.core.execution import TransportLimits
from peerhub.core.identity import AuthenticatedSubject
from peerhub.dispatch.capability import CapabilityTier
from peerhub.dispatch.contract import (
    CommandEnvelope,
    CompletionContract,
    CompletionContractKind,
    RequestState,
)
from peerhub.dispatch.materializer import ArtifactMaterializer
from peerhub.runtime import Runtime


_CLIENT_ID = "peerhub-broadcast-coordinator"
_COMPLETED_REQUEST_STATES = frozenset(
    {RequestState.SUCCEEDED_VERIFIED, RequestState.DELIVERED_UNVERIFIED}
)


@dataclass(frozen=True)
class FanOutRequest:
    workspace_root: Path
    prompt: str
    targets: list[tuple[str, str | None]]
    required_capability_tier: CapabilityTier
    limits: TransportLimits
    authenticated_subject: AuthenticatedSubject
    wave_of: str | None = None


@dataclass(frozen=True)
class BroadcastLegResult:
    target: str
    leg_state: str
    command_id: str
    response_text: str | None


@dataclass(frozen=True)
class FanOutResult:
    round_id: str
    disposition: str
    legs: tuple[BroadcastLegResult, ...]


def _canonical_leg_target(target: ResolvedPeerTarget) -> str:
    return f"{target.peer_kind}/{target.profile.profile_id}"


def _domain_hash(domain: str, round_id: str, leg_target: str) -> str:
    value = f"{domain}:{round_id}:{leg_target}"
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class BroadcastCoordinator:
    """Run the sequential broadcast workflow.

    Failure classification and partial-round disposition are implemented.
    A failed leg no longer raises, and the round is closed with a real disposition.
    
    Still deferred: timeout-specific handling, deadline-based closing, and 
    crash-linkage/recovery. Exceptions raised inside `_dispatch_leg` itself 
    (e.g., from `admit_request`, `prepare_for_dispatch`, or 
    `dispatch_and_execute`) still abort the whole fan_out and leave the 
    round open with a NULL disposition.
    """

    def __init__(self, runtime: Runtime, clock: Clock, ids: IdSource):
        self.runtime = runtime
        self.clock = clock
        self.ids = ids

    def fan_out(self, request: FanOutRequest) -> FanOutResult:
        resolved_targets = self._resolve_targets(request)
        round_id = self.ids.new_id("broadcast-round")
        prompt_digest = hashlib.sha256(request.prompt.encode("utf-8")).hexdigest()

        self._create_round(
            round_id=round_id,
            wave_of=request.wave_of,
            prompt_digest=prompt_digest,
            targets=resolved_targets,
        )

        leg_results = tuple(
            self._dispatch_leg(
                request=request,
                round_id=round_id,
                prompt_digest=prompt_digest,
                target=target,
            )
            for target in resolved_targets
        )
        all_completed = all(leg.leg_state == "completed" for leg in leg_results)
        # Note: In future increments, there will be states like `timed_out`.
        # `not any` is robust to these future states, whereas `all(failed)` would misclassify them.
        none_completed = not any(leg.leg_state == "completed" for leg in leg_results)
        if all_completed:
            disposition = "all_completed"
        elif none_completed:
            disposition = "none_completed"
        else:
            disposition = "partial"

        self._close_completed_round(round_id, disposition)
        return FanOutResult(
            round_id=round_id,
            disposition=disposition,
            legs=leg_results,
        )

    @staticmethod
    def _resolve_targets(request: FanOutRequest) -> tuple[ResolvedPeerTarget, ...]:
        if not request.targets:
            raise ValueError("broadcast requires at least one target")

        resolved = tuple(
            resolve_peer_target(peer_name, profile_id=profile_id)
            for peer_name, profile_id in request.targets
        )
        canonical_targets = tuple(_canonical_leg_target(target) for target in resolved)
        if len(canonical_targets) != len(set(canonical_targets)):
            raise ValueError("broadcast targets must be unique")
        return resolved

    def _create_round(
        self,
        *,
        round_id: str,
        wave_of: str | None,
        prompt_digest: str,
        targets: tuple[ResolvedPeerTarget, ...],
    ) -> None:
        now = self.clock.now()
        leg_rows: list[tuple[str, str, str, str, None, str, None]] = []
        for target in targets:
            leg_target = _canonical_leg_target(target)
            client_leg_request_id = _domain_hash(
                "peerhub.broadcast.client-request.v1",
                round_id,
                leg_target,
            )
            leg_rows.append(
                (
                    round_id,
                    leg_target,
                    _CLIENT_ID,
                    client_leg_request_id,
                    None,
                    "admitting",
                    None,
                )
            )

        with self.runtime.state_store.unit_of_work() as uow:
            db = uow._db()  # pyright: ignore[reportPrivateUsage]
            db.execute(
                """
                INSERT INTO broadcast_rounds (
                    broadcast_round_id, wave_of, prompt_digest,
                    requested_targets, deadline_at, status, disposition,
                    created_at, closed_at
                ) VALUES (?, ?, ?, ?, NULL, 'open', NULL, ?, NULL)
                """,
                (round_id, wave_of, prompt_digest, len(targets), now),
            )
            db.executemany(
                """
                INSERT INTO broadcast_legs (
                    broadcast_round_id, leg_target, client_id,
                    client_leg_request_id, command_id, leg_state, terminal_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                leg_rows,
            )
            uow.commit()

    def _dispatch_leg(
        self,
        *,
        request: FanOutRequest,
        round_id: str,
        prompt_digest: str,
        target: ResolvedPeerTarget,
    ) -> BroadcastLegResult:
        leg_target = _canonical_leg_target(target)
        client_leg_request_id = _domain_hash(
            "peerhub.broadcast.client-request.v1",
            round_id,
            leg_target,
        )
        idempotency_key = _domain_hash(
            "peerhub.broadcast.admission-idempotency.v1",
            round_id,
            leg_target,
        )

        policy = self.runtime.application_workflows._health._policy  # pyright: ignore[reportPrivateUsage]
        route_request_factory = _DirectAskRouteRequestFactory(
            target=target,
            clock=self.clock,
            ids=self.ids,
            client_request_id=client_leg_request_id,
            policy_id=policy.policy_id,
            policy_revision=policy.revision,
            required_capability_tier=request.required_capability_tier,
        )
        envelope = CommandEnvelope(
            protocol_major=1,
            protocol_minor=0,
            schema_version="1.0.0",
            client_request_id=client_leg_request_id,
            correlation_id=self.ids.new_id("corr"),
            client_id=_CLIENT_ID,
            actor_id="broadcast-coordinator",
            scope={"workspace_root": str(request.workspace_root)},
            method="peer.ask",
            params={
                "required_capability_tier": request.required_capability_tier.name,
                "broadcast_round_id": round_id,
                "prompt_digest": prompt_digest,
                "broadcast_leg_idempotency_key": idempotency_key,
            },
            idempotency_key=idempotency_key,
            expected_policy_revision=policy.revision,
            expected_configuration_revision=1,
            client_timestamp=self.clock.now(),
        )
        completion_contract = CompletionContract(
            contract_id=self.ids.new_id("contract"),
            kind=CompletionContractKind.DELIVERY_ONLY,
            requirements=(),
            replay_safe=False,
        )

        admission_result = self.runtime.application_workflows.admit_request(
            envelope,
            route_request_factory=route_request_factory,
            required_capability_tier=request.required_capability_tier,
            authenticated_subject=request.authenticated_subject,
            completion_contract=completion_contract,
            dispatch_policy_revision=1,
            session_id=self.ids.new_id("session"),
            owner_principal_id=request.authenticated_subject.principal_id,
            owner_instance_id="cli-instance",
            authority_epoch=1,
            heartbeat_timeout_ms=30000,
        )
        if admission_result.dispatch_admission is None:
            raise RuntimeError(f"broadcast leg {leg_target} was not admitted")
        if admission_result.route is None:
            raise RuntimeError(f"broadcast leg {leg_target} has no route plan")

        admitted_request = admission_result.dispatch_admission[0]
        capability_lease = admission_result.dispatch_admission[3]
        command_id = admitted_request.command_id
        self._link_pending_leg(round_id, leg_target, str(command_id))

        self.runtime.application_workflows.prepare_for_dispatch(
            command_id,
            route_decision_id=admission_result.route.decision.decision_id,
            route_request_factory=route_request_factory,
            telemetry_limit=100,
        )
        adapter_request = AdapterRequest(
            request_id=client_leg_request_id,
            prompt_content=request.prompt,
            prompt_reference=None,
            workspace_scope="default",
            profile_id=target.profile.profile_id,
            requested_session_action=SessionAction.NONE,
            completion_contract=completion_contract,
        )
        materializer = ArtifactMaterializer(
            unit_of_work_factory=self.runtime.state_store.unit_of_work,
            workspace_root=request.workspace_root,
            clock=self.clock.now,
        )
        execution_result = self.runtime.application_workflows.dispatch_and_execute(
            command_id,
            capability_lease_id=capability_lease.capability_lease_id,
            peer_instance_id=admitted_request.selected_peer_instance_id,
            current_policy_revision=policy.revision,
            materializer=materializer,
            adapter_request=adapter_request,
            peer_adapter=target.adapter,
            profile=target.profile,
            limits=request.limits,
            workspace_roots={"default": request.workspace_root},
            content_providers={},
            completion_contract=completion_contract,
            heartbeat_timeout_ms=30000,
        )
        if execution_result.request.state not in _COMPLETED_REQUEST_STATES:
            self._fail_leg(round_id, leg_target)
            return BroadcastLegResult(
                target=leg_target,
                leg_state="failed",
                command_id=str(command_id),
                response_text=None,
            )

        self._complete_leg(round_id, leg_target)
        response_text = (
            execution_result.decoded_output.canonical_text
            if execution_result.decoded_output is not None
            else None
        )
        return BroadcastLegResult(
            target=leg_target,
            leg_state="completed",
            command_id=str(command_id),
            response_text=response_text,
        )

    def _link_pending_leg(
        self,
        round_id: str,
        leg_target: str,
        command_id: str,
    ) -> None:
        with self.runtime.state_store.unit_of_work() as uow:
            db = uow._db()  # pyright: ignore[reportPrivateUsage]
            db.execute(
                """
                UPDATE broadcast_legs
                SET command_id = ?, leg_state = 'pending'
                WHERE broadcast_round_id = ? AND leg_target = ?
                """,
                (command_id, round_id, leg_target),
            )
            uow.commit()

    def _complete_leg(self, round_id: str, leg_target: str) -> None:
        with self.runtime.state_store.unit_of_work() as uow:
            db = uow._db()  # pyright: ignore[reportPrivateUsage]
            db.execute(
                """
                UPDATE broadcast_legs
                SET leg_state = 'completed', terminal_at = ?
                WHERE broadcast_round_id = ? AND leg_target = ?
                """,
                (self.clock.now(), round_id, leg_target),
            )
            uow.commit()

    def _fail_leg(self, round_id: str, leg_target: str) -> None:
        with self.runtime.state_store.unit_of_work() as uow:
            db = uow._db()  # pyright: ignore[reportPrivateUsage]
            db.execute(
                """
                UPDATE broadcast_legs
                SET leg_state = 'failed', terminal_at = ?
                WHERE broadcast_round_id = ? AND leg_target = ?
                """,
                (self.clock.now(), round_id, leg_target),
            )
            uow.commit()

    def _close_completed_round(self, round_id: str, disposition: str) -> None:
        with self.runtime.state_store.unit_of_work() as uow:
            db = uow._db()  # pyright: ignore[reportPrivateUsage]
            db.execute(
                """
                UPDATE broadcast_rounds
                SET status = 'closed', closed_at = ?,
                    disposition = ?
                WHERE broadcast_round_id = ?
                """,
                (self.clock.now(), disposition, round_id),
            )
            uow.commit()
