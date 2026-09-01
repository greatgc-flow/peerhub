"""Project ratified proposal effects into immutable write requests.

This module deliberately stops at the governance request record.  It never
opens, parses, or writes the hinted architecture document.
"""

from __future__ import annotations

from collections.abc import Mapping

from peerhub.core.context import Clock, IdSource
from peerhub.core.errors import (
    InvalidMutationError,
    RecordNotFoundError,
    StaleRevisionError,
)
from peerhub.core.protocol import CommandID, JsonValue

from .broker import GovernanceBroker
from .contract import (
    EffectIntent,
    EffectOutcome,
    MutationRequest,
    OutboxState,
    TargetState,
)


RATIFIED_INVARIANT_EFFECT_KIND = (
    "governance.ratified-invariant-write-request"
)


class RatifiedInvariantRequestProjector:
    """Create one immutable request from one committed approval effect."""

    def __init__(
        self,
        broker: GovernanceBroker,
        *,
        clock: Clock,
        ids: IdSource,
    ) -> None:
        self._broker = broker
        self._clock = clock
        self._ids = ids

    @staticmethod
    def _request_state(
        payload: Mapping[str, JsonValue],
    ) -> tuple[str, dict[str, JsonValue]]:
        request_id = _required_text(payload, "request_id")
        round_id = _required_text(payload, "round_id")
        decision_hash = _required_text(payload, "decision_hash")
        expected_id = (
            f"ratified-invariant-write-request:{round_id}:{decision_hash}"
        )
        if request_id != expected_id:
            raise InvalidMutationError(
                "ratified invariant request identity does not match its snapshot"
            )
        approved_revision = payload.get("approved_revision")
        if type(approved_revision) is not int or approved_revision < 1:
            raise InvalidMutationError(
                "approved_revision must be a positive integer"
            )
        if payload.get("target_doc_hint") != "10-invariants.md":
            raise InvalidMutationError(
                "ratified invariant request target_doc_hint is not allowed"
            )

        state: dict[str, JsonValue] = {
            **dict(payload),
            "schema": "peerhub.ratified-invariant-write-request.v1",
            "kind": "ratified-invariant-write-request",
            "scope": round_id,
            "schema_version": 1,
            "status": "REQUESTED",
        }
        return request_id, state

    def _create_immutable(
        self,
        target_id: str,
        state: dict[str, JsonValue],
    ) -> TargetState:
        mutation_request_id = f"{target_id}:create"
        try:
            self._broker.submit(
                MutationRequest(
                    request_id=mutation_request_id,
                    command_id=CommandID(f"{target_id}:command"),
                    correlation_id=f"{target_id}:correlation",
                    client_id="peerhub.invariant-request-projector",
                    command_type="ratified-invariant-write-request.create",
                    idempotency_key=mutation_request_id,
                    actor_id="system:invariant-request-projector",
                    policy_revision="proposal-v1",
                    target_id=target_id,
                    expected_revision=0,
                    operation="ratified-invariant-write-request.create",
                    desired_state=state,
                    effect_intent=EffectIntent(
                        kind="ratified-invariant-write-request.noop",
                        payload={},
                    ),
                )
            )
        except StaleRevisionError as exc:
            existing = self._broker.get_target(target_id)
            if existing is None or dict(existing.state) != state:
                raise InvalidMutationError(
                    "ratified invariant write-request collision does not match "
                    "the committed approval snapshot"
                ) from exc
            return existing

        created = self._broker.get_target(target_id)
        if created is None or dict(created.state) != state:
            raise InvalidMutationError(
                "ratified invariant write-request was not materialized exactly"
            )
        return created

    def project_event(self, event_id: str) -> TargetState:
        """Claim and project one effect, safely resuming the same claim."""

        event = self._broker.get_outbox_event(event_id)
        if event is None:
            raise RecordNotFoundError("outbox_event", event_id)
        if event.payload.get("effect_kind") != RATIFIED_INVARIANT_EFFECT_KIND:
            raise InvalidMutationError(
                "outbox event is not a ratified invariant write request"
            )
        payload = event.payload.get("effect_payload")
        if not isinstance(payload, Mapping):
            raise InvalidMutationError("effect_payload must be an object")
        target_id, state = self._request_state(payload)

        if event.state is OutboxState.CONSUMED:
            existing = self._broker.get_target(target_id)
            if existing is None or dict(existing.state) != state:
                raise InvalidMutationError(
                    "consumed invariant request effect lacks its exact target"
                )
            return existing

        owner_id = "peerhub.invariant-request-projector"
        attempt_id = f"ratified-invariant-request:{event_id}"
        self._broker.claim_effect(
            event_id,
            owner_id=owner_id,
            attempt_id=attempt_id,
        )
        created = self._create_immutable(target_id, state)
        self._broker.record_effect_result(
            event_id,
            owner_id=owner_id,
            attempt_id=attempt_id,
            outcome=EffectOutcome.EFFECT_SUCCEEDED,
            evidence_refs=(target_id,),
        )
        return created


def _required_text(value: Mapping[str, JsonValue], field: str) -> str:
    result = value.get(field)
    if not isinstance(result, str) or not result.strip():
        raise InvalidMutationError(f"{field} must be a non-empty string")
    return result


__all__ = [
    "RATIFIED_INVARIANT_EFFECT_KIND",
    "RatifiedInvariantRequestProjector",
]
