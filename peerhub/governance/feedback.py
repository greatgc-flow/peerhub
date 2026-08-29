"""Lightweight governance feedback journal (``GAP-YYYYMMDD-NNN`` items).

A self-contained journal domain: append an item, read them all, resolve one.
It coordinates with no other domain, so it lives here beside ``LessonService``
and ``ConsensusService`` rather than in ``peerhub.application`` (where
``PeerRegistryService``/``RoleAssignmentService`` live because they reach
across the adapters/health boundaries). The ``governance.feedback.*`` method
namespace already recorded in ``LEGACY_CATALOG`` matches that placement.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import datetime, timezone

from peerhub.core.context import Clock, IdSource
from peerhub.core.errors import RecordNotFoundError
from peerhub.core.protocol import CommandID, JsonValue, require_text

from .broker import GovernanceBroker
from .contract import (
    EffectIntent,
    MutationRequest,
    MutationSubmission,
    TargetState,
)

# ``\d{3,}`` rather than ``\d{3}``: the sequence is rendered with ``:03d``,
# which pads to three digits but does not truncate, so a journal that ever
# passes 999 items in one day keeps allocating (1000, 1001, ...) instead of
# silently colliding. Legacy's own allocator has the same widening behaviour.
_FEEDBACK_ID = re.compile(r"^GAP-\d{8}-(\d{3,})$")


class FeedbackService:
    """Append, read, and resolve durable feedback-journal items."""

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

    def _submit(
        self,
        *,
        target_id: str,
        expected_revision: int,
        actor_id: str,
        operation: str,
        desired_state: dict[str, JsonValue],
    ) -> MutationSubmission:
        request_id = self._ids.new_id("feedback-request")
        return self._broker.submit(
            MutationRequest(
                request_id=request_id,
                command_id=CommandID(self._ids.new_id("feedback-command")),
                correlation_id=self._ids.new_id("feedback-correlation"),
                client_id="peerhub.feedback",
                command_type=operation,
                idempotency_key=request_id,
                actor_id=actor_id,
                policy_revision="protocol-v2",
                target_id=target_id,
                expected_revision=expected_revision,
                operation=operation,
                desired_state=desired_state,
                effect_intent=EffectIntent(kind="feedback.noop", payload={}),
            )
        )

    @staticmethod
    def _target_id(feedback_id: str) -> str:
        return f"feedback:{feedback_id}"

    @staticmethod
    def _utc_date_token(timestamp: int) -> str:
        """Return the ``YYYYMMDD`` component for one injected timestamp.

        Explicitly UTC, never host-local: legacy uses a naive
        ``datetime.now()``, which would make the allocated ID depend on the
        operator's timezone. The ratified design overrides that for
        determinism and testability.
        """

        return datetime.fromtimestamp(timestamp, tz=timezone.utc).strftime(
            "%Y%m%d"
        )

    def _next_feedback_id(self, date_token: str) -> str:
        prefix = f"GAP-{date_token}-"
        highest = 0
        for target in self._broker.list_targets("feedback", None):
            feedback_id = target.state.get("feedback_id")
            if not isinstance(feedback_id, str):
                continue
            if not feedback_id.startswith(prefix):
                continue
            match = _FEEDBACK_ID.match(feedback_id)
            if match is None:
                continue
            highest = max(highest, int(match.group(1)))
        return f"{prefix}{highest + 1:03d}"

    def add_feedback(
        self,
        *,
        source_peer: str,
        category: str,
        severity: str,
        title: str,
        detail: str,
        actor_id: str,
    ) -> MutationSubmission:
        """Append one new journal item with a freshly allocated GAP ID.

        Never idempotent and never deduplicated: every call allocates a new
        ID and creates a new target, matching legacy's append-only journal.
        """

        normalized_source_peer = require_text(source_peer, "source_peer")
        normalized_category = require_text(category, "category")
        normalized_severity = require_text(severity, "severity")
        normalized_title = require_text(title, "title")
        normalized_actor_id = require_text(actor_id, "actor_id")
        # ``detail`` is deliberately not require_text: legacy's own default is
        # the empty string, so demanding non-empty text here would reject a
        # call legacy accepts.
        if not isinstance(detail, str):  # pyright: ignore[reportUnnecessaryIsInstance]
            raise ValueError("detail must be a string")

        now = self._clock.now()
        feedback_id = self._next_feedback_id(self._utc_date_token(now))

        desired_state: dict[str, JsonValue] = {
            "kind": "feedback",
            "scope": None,
            "schema_version": 1,
            "feedback_id": feedback_id,
            "source_peer": normalized_source_peer,
            "category": normalized_category,
            "severity": normalized_severity,
            "title": normalized_title,
            "detail": detail,
            "status": "open",
            "owner": None,
            "created_at": now,
            "created_by": normalized_actor_id,
            "resolved_at": None,
            "updated_at": now,
        }

        # ``expected_revision=0``: a concurrent creator that already took this
        # sequence number loses the CAS and surfaces as a real
        # StaleRevisionError. Deliberately no retry-with-the-next-number loop
        # -- every other per-entity domain here surfaces a create-time CAS
        # loss rather than papering over it.
        return self._submit(
            target_id=self._target_id(feedback_id),
            expected_revision=0,
            actor_id=normalized_actor_id,
            operation="feedback.add",
            desired_state=desired_state,
        )

    def get_feedback(self, feedback_id: str) -> TargetState:
        """Return one journal item, or raise if the ID does not exist."""

        normalized = require_text(feedback_id, "feedback_id")
        target = self._broker.get_target(self._target_id(normalized))
        if target is None:
            raise RecordNotFoundError("feedback", normalized)
        return target

    def list_feedback(self) -> Sequence[TargetState]:
        """Return every journal item, resolved ones included."""

        return self._broker.list_targets("feedback", None)

    def resolve_feedback(
        self,
        feedback_id: str,
        *,
        status: str,
        owner: str | None = None,
        actor_id: str,
    ) -> MutationSubmission:
        """Set one item's status, refreshing its resolution timestamps.

        Repeating the call with the same status is not an error and not a
        no-op: it refreshes ``resolved_at``/``updated_at``, exactly as legacy
        does. Omitting ``owner`` preserves whatever owner is already stored.
        """

        normalized_feedback_id = require_text(feedback_id, "feedback_id")
        # Validated free text, not an enum: the parity ledger's input schema
        # accepts an arbitrary status and only demonstrates done/dismissed,
        # so a closed vocabulary would be an unproven narrowing.
        normalized_status = require_text(status, "status")
        normalized_actor_id = require_text(actor_id, "actor_id")
        normalized_owner = (
            None if owner is None else require_text(owner, "owner")
        )

        current = self.get_feedback(normalized_feedback_id)
        now = self._clock.now()

        desired_state: dict[str, JsonValue] = dict(current.state)
        desired_state["status"] = normalized_status
        if normalized_owner is not None:
            desired_state["owner"] = normalized_owner
        desired_state["resolved_at"] = now
        desired_state["updated_at"] = now

        return self._submit(
            target_id=current.target_id,
            expected_revision=current.revision,
            actor_id=normalized_actor_id,
            operation="feedback.resolve",
            desired_state=desired_state,
        )
