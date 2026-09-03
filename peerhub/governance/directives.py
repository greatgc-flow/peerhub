"""Directive governance lifecycle operations over governed targets."""

from __future__ import annotations

from typing import cast
from collections.abc import Sequence

from peerhub.core.context import Clock, IdSource
from peerhub.core.errors import InvalidMutationError, RecordNotFoundError
from peerhub.core.protocol import CommandID, JsonValue

from .broker import GovernanceBroker
from .contract import EffectIntent, MutationRequest, MutationSubmission, TargetState


class DirectiveService:
    """Propose, migrate, and retire directive TargetStates."""

    def __init__(self, broker: GovernanceBroker, *, clock: Clock, ids: IdSource) -> None:
        self._broker = broker
        self._clock = clock
        self._ids = ids

    def get_target(self, directive_id: str) -> TargetState | None:
        return self._broker.get_target(f"directive:{directive_id}")

    def list_all(self) -> Sequence[TargetState]:
        return self._broker.list_targets("directive")

    def propose(
        self,
        *,
        directive_id: str,
        title: str,
        rule: str,
        effective_date: str,
        proposer_id: str,
        category: str | None = None,
    ) -> MutationSubmission:
        timestamp = self._clock.now()
        state: dict[str, JsonValue] = {
            "schema": "peerhub.governance-directive.v1",
            "kind": "directive",
            "directive_id": directive_id,
            "lifecycle": "PROPOSED",
            "content": {
                "title": title,
                "rule": rule,
                "effective_date": effective_date,
            },
            "category": category,
            "provenance": {
                "proposer": {"actor_id": proposer_id, "actor_type": "peer"},
                "proposed_at": timestamp,
                "source_command": "directive-add",
            },
            "validity": {
                "expires_at": None,
                "retired_at": None,
                "superseded_by": None,
            },
        }
        return self._submit(
            f"directive:{directive_id}", 0, proposer_id, "directive-add", state
        )

    def migrate(
        self,
        *,
        directive_id: str,
        title: str,
        rule_markdown: str,
        digest: str,
        consumers: Sequence[dict[str, JsonValue]],
        source_path: str,
        migrated_by: str = "terminal",
    ) -> MutationSubmission:
        timestamp = self._clock.now()
        state: dict[str, JsonValue] = {
            "schema": "peerhub.governance-directive.v1",
            "kind": "directive",
            "directive_id": directive_id,
            "lifecycle": "ACTIVE",
            "content": {
                "title": title,
                "rule": rule_markdown,
            },
            "digest": digest,
            "consumers": tuple(consumers),
            "provenance": {
                "migrated_by": migrated_by,
                "migrated_at": timestamp,
                "source_path": source_path,
                "source_directive_id": directive_id,
            },
            "validity": {
                "expires_at": None,
                "retired_at": None,
                "superseded_by": None,
            },
        }
        return self._submit(
            f"directive:{directive_id}", 0, migrated_by, "directive-migrate", state
        )

    def retire(
        self, 
        directive_id: str, 
        *, 
        actor_id: str, 
        reason: str, 
        expected_revision: int | None = None
    ) -> MutationSubmission:
        target, state = self._load(directive_id, {"ACTIVE"})
        timestamp = self._clock.now()
        validity = dict(cast(dict[str, JsonValue], state["validity"]))
        validity.update({"retired_at": timestamp, "retirement_reason": reason})
        state["validity"] = validity
        state["lifecycle"] = "RETIRED"
        return self._submit(
            f"directive:{directive_id}", 
            target.revision if expected_revision is None else expected_revision, 
            actor_id, 
            "directive-clear", 
            state
        )

    def _load(
        self, directive_id: str, allowed: set[str]
    ) -> tuple[TargetState, dict[str, JsonValue]]:
        target = self._broker.get_target(f"directive:{directive_id}")
        if target is None:
            raise RecordNotFoundError("directive", directive_id)
        state = dict(target.state)
        if state.get("lifecycle") not in allowed:
            raise InvalidMutationError("operation is not valid in the current directive lifecycle")
        return target, state

    def _submit(
        self,
        target_id: str,
        expected_revision: int,
        actor_id: str,
        operation: str,
        desired_state: dict[str, JsonValue],
    ) -> MutationSubmission:
        request_id = self._ids.new_id("directives-request")
        return self._broker.submit(
            MutationRequest(
                request_id=request_id,
                command_id=CommandID(self._ids.new_id("directives-command")),
                correlation_id=self._ids.new_id("directives-correlation"),
                client_id="peerhub.directives",
                command_type=operation,
                idempotency_key=request_id,
                actor_id=actor_id,
                policy_revision="protocol-v2",
                target_id=target_id,
                expected_revision=expected_revision,
                operation=operation,
                desired_state=desired_state,
                effect_intent=EffectIntent(kind="directives.noop", payload={}),
            )
        )
