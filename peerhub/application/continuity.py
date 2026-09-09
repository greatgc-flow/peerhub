"""Cross-dispatch context continuity for the ``peerhub ask`` path.

Ratified backlog item G (``docs/reviews/p-drive-mece-migration-audit-
2026-09-09.md`` section 5): peerhub already records durable room
checkpoints (``RoomsService.checkpoint``, the ``ctx-save`` equivalent) and
durable task checkpoints (``TaskService.checkpoint``), but nothing read
them back into a SUBSEQUENT dispatch's outgoing prompt, so every ``peerhub
ask`` started from zero.

This module is the read side. It resolves the most recent relevant
checkpoints for a workspace, optionally narrowed to a room and/or a task,
and renders them as the ``[CONTINUITY]`` block that
``assemble_ask_prompt`` injects alongside the directive/lesson/room-context
layers. All bounds come from ``ask_config``; nothing here is a literal.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from peerhub.application.ask_config import ContinuityConfig
from peerhub.core.protocol import JsonValue
from peerhub.governance.broker import GovernanceBroker
from peerhub.governance.contract import TargetState

CONTINUITY_BLOCK_HEADER = "[CONTINUITY]"

_ROOM_CHECKPOINT_KIND = "checkpoint-created"
_TASK_KIND = "task"
_TERMINAL_TASK_STATES = frozenset({"SUCCEEDED", "FAILED", "CANCELLED"})


@dataclass(frozen=True)
class RoomCheckpointSummary:
    """One recorded room checkpoint, flattened for rendering."""

    checkpoint_id: str
    room_id: str
    created_at: int
    as_of_event_seq: int
    markdown: str


@dataclass(frozen=True)
class TaskCheckpointSummary:
    """One checkpointed task, flattened for rendering."""

    task_id: str
    stage: str
    captured_at: int
    coordinator: str | None
    completed_units: tuple[str, ...]
    remaining_units: tuple[str, ...]
    summary: str


@dataclass(frozen=True)
class ContinuitySnapshot:
    """The checkpoint state carried forward into one dispatch."""

    room_checkpoints: tuple[RoomCheckpointSummary, ...]
    task_checkpoints: tuple[TaskCheckpointSummary, ...]

    def is_empty(self) -> bool:
        return not self.room_checkpoints and not self.task_checkpoints


def _mapping(value: JsonValue | None) -> Mapping[str, JsonValue] | None:
    return value if isinstance(value, Mapping) else None


def _text(value: object, default: str = "") -> str:
    return value if isinstance(value, str) else default


def _int_value(value: object, default: int = 0) -> int:
    return value if type(value) is int else default


def _units(value: JsonValue | None, limit: int) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    items = tuple(item for item in value if isinstance(item, str))
    return items[:limit]


def _room_checkpoint_summary(
    target: TargetState,
) -> RoomCheckpointSummary | None:
    checkpoint = _mapping(target.state.get("checkpoint"))
    if checkpoint is None:
        return None
    room_id = _text(target.state.get("room_id"))
    if not room_id:
        return None
    return RoomCheckpointSummary(
        checkpoint_id=_text(checkpoint.get("checkpoint_id")),
        room_id=room_id,
        created_at=_int_value(
            checkpoint.get("created_at"),
            _int_value(target.state.get("created_at")),
        ),
        as_of_event_seq=_int_value(checkpoint.get("as_of_event_seq")),
        markdown=_text(checkpoint.get("markdown")),
    )


def _task_checkpoint_summary(
    target: TargetState,
    *,
    max_units: int,
) -> TaskCheckpointSummary | None:
    checkpoint = _mapping(target.state.get("checkpoint"))
    if checkpoint is None:
        return None
    executor = _mapping(target.state.get("executor"))
    coordinator_raw = (
        executor.get("coordinator") if executor is not None else None
    )
    objective = _mapping(target.state.get("objective"))
    return TaskCheckpointSummary(
        task_id=_text(target.state.get("task_id"), target.target_id),
        stage=_text(checkpoint.get("stage")),
        captured_at=_int_value(checkpoint.get("captured_at"), target.updated_at),
        coordinator=(
            coordinator_raw if isinstance(coordinator_raw, str) else None
        ),
        completed_units=_units(checkpoint.get("completed_units"), max_units),
        remaining_units=_units(checkpoint.get("remaining_units"), max_units),
        summary=(
            _text(objective.get("summary")) if objective is not None else ""
        ),
    )


def _newest_first_rooms(
    summaries: Sequence[RoomCheckpointSummary],
) -> tuple[RoomCheckpointSummary, ...]:
    return tuple(
        sorted(
            summaries,
            key=lambda item: (item.created_at, item.checkpoint_id),
            reverse=True,
        )
    )


def _newest_first_tasks(
    summaries: Sequence[TaskCheckpointSummary],
) -> tuple[TaskCheckpointSummary, ...]:
    return tuple(
        sorted(
            summaries,
            key=lambda item: (item.captured_at, item.task_id),
            reverse=True,
        )
    )


def resolve_continuity(
    broker: GovernanceBroker,
    *,
    peer_kind: str,
    room_id: str | None,
    task_id: str | None,
    config: ContinuityConfig,
) -> ContinuitySnapshot:
    """Resolve the checkpoints a new dispatch should carry forward.

    Scoping, narrowest first:

    * ``task_id`` -- that task's own checkpoint, nothing inferred.
    * ``room_id`` -- that room's most recent checkpoints, plus checkpointed
      tasks scoped to it.
    * neither -- the workspace's most recent room checkpoints, plus
      checkpointed tasks coordinated by ``peer_kind``. Tasks attributed to
      another peer (or to none) are only considered when
      ``include_unattributed_tasks`` is set, and only as a fallback once no
      peer-attributed task matched.

    The state store is already workspace-scoped -- its SQLite file lives
    under the workspace's own ``.peerhub`` home -- so "workspace" needs no
    explicit filter here.
    """

    if not config.enabled:
        return ContinuitySnapshot((), ())

    room_summaries: list[RoomCheckpointSummary] = []
    if config.max_room_checkpoints > 0:
        for target in broker.list_targets(_ROOM_CHECKPOINT_KIND, room_id):
            room_summary = _room_checkpoint_summary(target)
            if room_summary is not None:
                room_summaries.append(room_summary)

    task_summaries: tuple[TaskCheckpointSummary, ...] = ()
    if config.max_task_checkpoints > 0:
        if task_id is not None:
            target = broker.get_target(task_id)
            scoped = (
                None
                if target is None
                else _task_checkpoint_summary(
                    target, max_units=config.max_units_per_task
                )
            )
            task_summaries = () if scoped is None else (scoped,)
        else:
            candidates: list[TaskCheckpointSummary] = []
            for target in broker.list_targets(_TASK_KIND, room_id):
                if target.state.get("state") in _TERMINAL_TASK_STATES:
                    continue
                candidate = _task_checkpoint_summary(
                    target, max_units=config.max_units_per_task
                )
                if candidate is not None:
                    candidates.append(candidate)
            attributed = [
                item for item in candidates if item.coordinator == peer_kind
            ]
            if attributed:
                task_summaries = _newest_first_tasks(attributed)
            elif config.include_unattributed_tasks or room_id is not None:
                task_summaries = _newest_first_tasks(candidates)

    return ContinuitySnapshot(
        room_checkpoints=_newest_first_rooms(room_summaries)[
            : config.max_room_checkpoints
        ],
        task_checkpoints=task_summaries[: config.max_task_checkpoints],
    )


def render_continuity_block(
    snapshot: ContinuitySnapshot,
    *,
    config: ContinuityConfig,
    include_room_markdown: bool,
) -> str:
    """Render the ``[CONTINUITY]`` prompt block, or "" when there is nothing.

    ``include_room_markdown`` is False when the caller already emitted the
    room's live ``[HANDOFF]`` projection, so the checkpoint contributes its
    provenance instead of repeating the same text twice in one prompt.
    """

    if snapshot.is_empty():
        return ""

    lines: list[str] = [CONTINUITY_BLOCK_HEADER]
    for room in snapshot.room_checkpoints:
        lines.append(
            f"- Room checkpoint {room.checkpoint_id} (room={room.room_id}, "
            f"as_of_event_seq={room.as_of_event_seq}, "
            f"created_at={room.created_at})"
        )
        if include_room_markdown and room.markdown:
            lines.append(room.markdown)
    for task in snapshot.task_checkpoints:
        stage = task.stage or "none"
        header = f"- Task checkpoint {task.task_id} (stage={stage}"
        if task.coordinator:
            header += f", coordinator={task.coordinator}"
        header += f", captured_at={task.captured_at})"
        lines.append(header)
        if task.summary:
            lines.append(f"  Summary: {task.summary}")
        if task.completed_units:
            lines.append("  Completed: " + ", ".join(task.completed_units))
        if task.remaining_units:
            lines.append("  Remaining: " + ", ".join(task.remaining_units))

    block = "\n".join(lines)
    if config.max_chars and len(block) > config.max_chars:
        block = (
            block[: config.max_chars].rstrip() + "\n[... continuity truncated]"
        )
    return block
