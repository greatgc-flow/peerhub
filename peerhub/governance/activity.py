"""Read-only helpers for active governed domain records."""

from __future__ import annotations

from collections.abc import Sequence

from .broker import GovernanceBroker
from .contract import TargetState


def list_active_consensus_rounds(
    broker: GovernanceBroker,
    room_id: str | None = None,
) -> Sequence[TargetState]:
    """Return open consensus rounds, optionally restricted by scope."""
    return tuple(
        target
        for target in broker.list_targets("consensus-round", room_id)
        if target.state.get("status") == "open"
    )


def list_active_tasks(
    broker: GovernanceBroker,
    room_id: str | None = None,
) -> Sequence[TargetState]:
    """Return tasks that have not reached a terminal state."""
    terminal = {"SUCCEEDED", "FAILED", "CANCELLED"}
    return tuple(
        target
        for target in broker.list_targets("task", room_id)
        if target.state.get("state") not in terminal
    )


def list_active_lessons(
    broker: GovernanceBroker,
    scope: str | None = None,
) -> Sequence[TargetState]:
    """Return active lessons; lesson scope is currently an object in state."""
    # lessons.py stores its object-shaped scope as NULL in the indexed string
    # projection, so a non-NULL string filter cannot be applied at the backend.
    targets = broker.list_targets("lesson", None)
    return tuple(
        target
        for target in targets
        if target.state.get("lifecycle") == "ACTIVE"
        and _lesson_scope_matches(target, scope)
    )


def _lesson_scope_matches(target: TargetState, requested: str | None) -> bool:
    if requested is None:
        return True
    state_scope = target.state.get("scope")
    return isinstance(state_scope, dict) and state_scope.get("workspace_id") == requested
