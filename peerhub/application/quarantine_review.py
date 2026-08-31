"""Quarantine-review consumer.

Coordinates between the operational-error journal (which records escalation
intent) and the health domain (which owns the actual circuit state).
"""

from __future__ import annotations

from collections.abc import Sequence

from peerhub.application.peer_registry import PeerRegistryService
from peerhub.core.context import Clock, IdSource
from peerhub.core.errors import InvalidMutationError, RecordNotFoundError
from peerhub.core.identity import AuthenticatedSubject, require_authenticated_subject
from peerhub.core.protocol import CommandID, JsonValue, require_text
from peerhub.governance.broker import GovernanceBroker
from peerhub.governance.contract import (
    EffectIntent,
    MutationRequest,
    MutationSubmission,
    TargetState,
)
from peerhub.health.contract import PolicyScope
from peerhub.health.service import HealthService


class QuarantineReviewCoordinator:
    """Read quarantine-review requests and escalate or dismiss them."""

    def __init__(
        self,
        broker: GovernanceBroker,
        *,
        peer_registry: PeerRegistryService,
        health: HealthService,
        clock: Clock,
        ids: IdSource,
    ) -> None:
        self._broker = broker
        self._peer_registry = peer_registry
        self._health = health
        self._clock = clock
        self._ids = ids

    def _submit(
        self,
        *,
        target_id: str,
        expected_revision: int,
        actor_id: str,
        operation: str,
        desired_state: dict[str, JsonValue],
    ) -> MutationSubmission:
        request_id = self._ids.new_id("quarantine-review-request")
        return self._broker.submit(
            MutationRequest(
                request_id=request_id,
                command_id=CommandID(
                    self._ids.new_id("quarantine-review-command")
                ),
                correlation_id=self._ids.new_id(
                    "quarantine-review-correlation"
                ),
                client_id="peerhub.quarantine-review",
                command_type=operation,
                idempotency_key=request_id,
                actor_id=actor_id,
                policy_revision="protocol-v2",
                target_id=target_id,
                expected_revision=expected_revision,
                operation=operation,
                desired_state=desired_state,
                effect_intent=EffectIntent(
                    kind="quarantine-review.noop",
                    payload={},
                ),
            )
        )

    def list_pending_quarantine_reviews(self) -> Sequence[TargetState]:
        """Return all quarantine-review targets in REQUESTED status."""
        reviews: list[TargetState] = []
        for target in self._broker.list_targets("quarantine-review", None):
            if target.state.get("status") == "REQUESTED":
                reviews.append(target)
        return tuple(reviews)

    def resolve_quarantine_review(
        self,
        review_id: str,
        *,
        decision: str,
        actor: AuthenticatedSubject,
        reason: str,
    ) -> MutationSubmission:
        """Resolve one review by dismissing or escalating it."""

        authenticated = require_authenticated_subject(actor)
        normalized_review_id = require_text(review_id, "review_id")
        normalized_decision = require_text(decision, "decision")
        if normalized_decision not in ("DISMISS", "ESCALATE"):
            raise ValueError("decision must be DISMISS or ESCALATE")

        normalized_reason = require_text(reason, "reason")

        target_id = f"quarantine-review:{normalized_review_id}"
        current = self._broker.get_target(target_id)
        if current is None:
            raise RecordNotFoundError("quarantine-review", normalized_review_id)

        now = self._clock.now()
        desired_state: dict[str, JsonValue] = dict(current.state)

        desired_state["status"] = (
            "ESCALATED" if normalized_decision == "ESCALATE" else "DISMISSED"
        )
        desired_state["resolved_at"] = now
        desired_state["resolved_by"] = authenticated.principal_id
        desired_state["reason"] = normalized_reason
        desired_state["updated_at"] = now

        submission = self._submit(
            target_id=current.target_id,
            expected_revision=current.revision,
            actor_id=authenticated.principal_id,
            operation="quarantine-review.resolve",
            desired_state=desired_state,
        )

        if normalized_decision == "ESCALATE":
            peer_key = current.state.get("peer_key")
            if not isinstance(peer_key, str) or not peer_key:
                raise InvalidMutationError("quarantine-review target lacks a valid peer_key")

            peer_node = self._peer_registry.get_node(peer_key)
            peer_kind = peer_node.state.get("peer_kind")
            profile_id = peer_node.state.get("profile_id")

            if not isinstance(peer_kind, str) or not peer_kind:
                raise InvalidMutationError("peer node has malformed peer_kind")
            if not isinstance(profile_id, str) or not profile_id:
                raise InvalidMutationError("peer node has malformed profile_id")

            self._health.authorize_administrative_recovery(
                instance_id=peer_kind,
                profile_id=profile_id,
                scope=PolicyScope.PROFILE,
                subject=authenticated,
                reason=normalized_reason,
                requested_at=now,
            )

        return submission
