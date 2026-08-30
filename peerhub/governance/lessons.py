"""Lesson governance lifecycle operations over governed targets."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from typing import cast

from peerhub.core.context import Clock, IdSource
from peerhub.core.errors import InvalidMutationError, RecordNotFoundError
from peerhub.core.protocol import CommandID, JsonValue

from .broker import GovernanceBroker
from .contract import EffectIntent, MutationRequest, MutationSubmission, TargetState


class LessonService:
    """Propose, approve, and activate lesson TargetStates."""

    def __init__(self, broker: GovernanceBroker, *, clock: Clock, ids: IdSource) -> None:
        self._broker = broker
        self._clock = clock
        self._ids = ids

    def get_target(self, lesson_id: str) -> TargetState | None:
        return self._broker.get_target(f"lesson:{lesson_id}")

    def propose(
        self,
        *,
        lesson_id: str,
        title: str,
        rule: str,
        category: str,
        severity: str,
        proposer_id: str,
        affected_peers: Sequence[str],
        scope_kind: str = "global",
        workspace_id: str | None = None,
        sticky: bool = False,
        os: Sequence[str] | None = None,
        shell: Sequence[str] | None = None,
        task_types: Sequence[str] | None = None,
    ) -> MutationSubmission:
        timestamp = self._clock.now()
        state: dict[str, JsonValue] = {
            "schema": "peerhub.lesson.v1",
            "kind": "lesson",
            "lesson_id": lesson_id,
            "lifecycle": "PROPOSED",
            "content": {
                "title": title,
                "rule": rule,
                "category": category,
                "severity": severity,
            },
            "sticky": sticky,
            "scope": {"kind": scope_kind, "workspace_id": workspace_id},
            "affected_peers": tuple(affected_peers),
            "applicability": {
                "os": tuple(os) if os is not None else None,
                "shell": tuple(shell) if shell is not None else None,
                "task_types": tuple(task_types) if task_types is not None else None,
            },
            "source_evidence": (),
            "provenance": {
                "proposer": {"actor_id": proposer_id, "actor_type": "peer"},
                "proposed_at": timestamp,
                "source_command": "lessons-propose",
            },
            "approval": None,
            "enforcement": {
                "artifact_id": None,
                "artifact_uri": None,
                "validation_status": "NOT_REQUIRED",
            },
            "validity": {
                "expires_at": None,
                "retired_at": None,
                "superseded_by": None,
            },
            "delivery": {"mode": "separate_targets", "required": True},
        }
        return self._submit(
            f"lesson:{lesson_id}", 0, proposer_id, "lessons-propose", state
        )

    def approve(
        self,
        lesson_id: str,
        *,
        approved_by_actor_id: str,
        authority_target_id: str | None = None,
        expected_revision: int | None = None,
    ) -> MutationSubmission:
        target, state = self._load(lesson_id, {"PROPOSED"})
        timestamp = self._clock.now()
        authority: dict[str, JsonValue] = {
            "target_id": authority_target_id,
            "resolution": "RESOLVED",
            "outcome": "AUTHORIZE_LESSON_ACTIVATION",
            "resolved_at": timestamp,
        }
        hash_payload = {
            "lesson_id": lesson_id,
            "actor_id": approved_by_actor_id,
            "authority_target_id": authority_target_id,
            "outcome": authority["outcome"],
        }
        resolution_hash = "sha256:" + hashlib.sha256(
            json.dumps(hash_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        authority["resolution_sha256"] = resolution_hash
        state["approval"] = {
            "method": "ratified_governance_proposal",
            "approved_by": (
                {
                    "actor_id": approved_by_actor_id,
                    "actor_type": "human",
                    "approved_at": timestamp,
                },
            ),
            "authority": authority,
        }
        return self._submit(
            f"lesson:{lesson_id}",
            target.revision if expected_revision is None else expected_revision,
            approved_by_actor_id,
            "lessons-approve",
            state,
        )

    def activate(
        self,
        lesson_id: str,
        *,
        actor_id: str,
        expected_revision: int | None = None,
    ) -> MutationSubmission:
        target, state = self._load(lesson_id, {"PROPOSED", "APPROVED"})
        if state.get("approval") is None:
            raise InvalidMutationError("lesson activation requires approval")
        state["lifecycle"] = "ACTIVE"
        return self._submit(
            f"lesson:{lesson_id}",
            target.revision if expected_revision is None else expected_revision,
            actor_id,
            "lessons-activate",
            state,
        )

    def retire(self, lesson_id: str, *, actor_id: str, reason: str = "MANUAL", expected_revision: int | None = None) -> MutationSubmission:
        target, state = self._load(lesson_id, {"ACTIVE"})
        timestamp = self._clock.now()
        validity = dict(cast(dict[str, JsonValue], state["validity"]))
        validity.update({"retired_at": timestamp, "retirement_reason": reason})
        state["validity"] = validity
        state["lifecycle"] = "RETIRED"
        return self._submit(f"lesson:{lesson_id}", target.revision if expected_revision is None else expected_revision, actor_id, "lessons-retire", state)

    def supersede(self, lesson_id: str, *, actor_id: str, replacement_lesson_id: str, expected_revision: int | None = None) -> MutationSubmission:
        target, state = self._load(lesson_id, {"ACTIVE"})
        validity = dict(cast(dict[str, JsonValue], state["validity"]))
        validity["superseded_by"] = replacement_lesson_id
        state["validity"] = validity
        state["lifecycle"] = "SUPERSEDED"
        return self._submit(f"lesson:{lesson_id}", target.revision if expected_revision is None else expected_revision, actor_id, "lessons-supersede", state)

    def quarantine(self, lesson_id: str, *, actor_id: str, reason: str, evidence: str, expected_revision: int | None = None) -> MutationSubmission:
        target, state = self._load(lesson_id, {"PROPOSED", "ACTIVE"})
        state["lifecycle"] = "QUARANTINED"
        state["quarantine"] = {"reason": reason, "evidence": evidence, "actor_id": actor_id, "quarantined_at": self._clock.now()}
        return self._submit(f"lesson:{lesson_id}", target.revision if expected_revision is None else expected_revision, actor_id, "lessons-quarantine", state)

    def record_delivery_pending(self, lesson_id: str, peer_id: str, *, delivery_method: str = "broadcast", actor_id: str = "peerhub") -> MutationSubmission:
        state: dict[str, JsonValue] = {
            "schema": "peerhub.lesson-delivery.v1", "kind": "lesson-delivery", "scope": lesson_id,
            "lesson_id": lesson_id, "peer_id": peer_id, "status": "PENDING", "delivery_revision": 0,
            "delivered_at": None, "delivery_method": delivery_method,
            "delivery_evidence": {"command_id": None, "correlation_id": None, "result_sha256": None},
        }
        return self._submit(f"lesson-delivery:{lesson_id}:{peer_id}", 0, actor_id, "lesson.delivery_pending", state)

    def record_delivery_complete(self, lesson_id: str, peer_id: str, *, command_id: str, correlation_id: str, actor_id: str = "peerhub") -> MutationSubmission:
        target = self._broker.get_target(f"lesson-delivery:{lesson_id}:{peer_id}")
        if target is None:
            raise RecordNotFoundError("lesson-delivery", peer_id)
        state = dict(target.state)
        if state.get("status") != "PENDING":
            raise InvalidMutationError("delivery is not pending")
        timestamp = self._clock.now()
        evidence = {"command_id": command_id, "correlation_id": correlation_id}
        result_hash = "sha256:" + hashlib.sha256(json.dumps(evidence, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
        state.update({"status": "DELIVERED", "delivery_revision": 1, "delivered_at": timestamp, "delivery_evidence": {**evidence, "result_sha256": result_hash}})
        return self._submit(target.target_id, target.revision, actor_id, "lesson.delivery_complete", state)

    def _load(
        self, lesson_id: str, allowed: set[str]
    ) -> tuple[TargetState, dict[str, JsonValue]]:
        target = self._broker.get_target(f"lesson:{lesson_id}")
        if target is None:
            raise RecordNotFoundError("lesson", lesson_id)
        state = dict(target.state)
        if state.get("lifecycle") not in allowed:
            raise InvalidMutationError("operation is not valid in the current lesson lifecycle")
        return target, state

    def _submit(
        self,
        target_id: str,
        expected_revision: int,
        actor_id: str,
        operation: str,
        desired_state: dict[str, JsonValue],
    ) -> MutationSubmission:
        request_id = self._ids.new_id("lessons-request")
        return self._broker.submit(
            MutationRequest(
                request_id=request_id,
                command_id=CommandID(self._ids.new_id("lessons-command")),
                correlation_id=self._ids.new_id("lessons-correlation"),
                client_id="peerhub.lessons",
                command_type=operation,
                idempotency_key=request_id,
                actor_id=actor_id,
                policy_revision="protocol-v2",
                target_id=target_id,
                expected_revision=expected_revision,
                operation=operation,
                desired_state=desired_state,
                effect_intent=EffectIntent(kind="lessons.noop", payload={}),
            )
        )
