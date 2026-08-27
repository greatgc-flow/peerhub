"""Read-only helpers for active governed domain records."""

from __future__ import annotations

from collections.abc import Sequence
from uuid import uuid4

from peerhub.core.errors import InvalidMutationError, RecordNotFoundError
from peerhub.core.protocol import CommandID, JsonValue
from peerhub.dispatch.room_session import RoomSessionSnapshot, RoomSessionState

from .broker import GovernanceBroker
from .contract import EffectIntent, MutationRequest, MutationSubmission, TargetState


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


def rebuild_room_session_bindings(
    broker: GovernanceBroker,
    room_id: str,
    active_sessions: Sequence[RoomSessionSnapshot],
) -> MutationSubmission:
    """Replace a room's rebuildable session-binding projection.

    ``active_sessions`` is intentionally supplied by the caller so this
    cross-cutting helper remains independent of both room and participation
    coordinators. The room session table remains authoritative.
    """
    room = broker.get_target(room_id)
    if room is None:
        raise RecordNotFoundError("room", room_id)
    if room.state.get("kind") != "room":
        raise InvalidMutationError("target is not a room")

    bindings: tuple[JsonValue, ...] = tuple(
        {
            "binding_id": session.session_id,
            "instance_id": session.owner.instance_id,
            "profile_id": session.owner.profile_id,
            "session_id": session.session_id,
            "role": "participant",
            "bound_at": session.created_at,
        }
        for session in active_sessions
        if session.room_id == room_id and session.state is RoomSessionState.ACTIVE
    )
    desired_state: dict[str, JsonValue] = dict(room.state)
    desired_state["session_bindings"] = bindings
    request_id = str(uuid4())
    return broker.submit(
        MutationRequest(
            request_id=request_id,
            command_id=CommandID(str(uuid4())),
            correlation_id=str(uuid4()),
            client_id="peerhub.room-session-bindings",
            command_type="room.session_bindings.rebuild",
            idempotency_key=request_id,
            actor_id="peerhub.maintenance",
            policy_revision="protocol-v2",
            target_id=room_id,
            expected_revision=room.revision,
            operation="room.session_bindings.rebuild",
            desired_state=desired_state,
            effect_intent=EffectIntent(
                kind="room.session-bindings.noop", payload={}
            ),
        )
    )


def _lesson_scope_matches(target: TargetState, requested: str | None) -> bool:
    if requested is None:
        return True
    state_scope = target.state.get("scope")
    return isinstance(state_scope, dict) and state_scope.get("workspace_id") == requested
