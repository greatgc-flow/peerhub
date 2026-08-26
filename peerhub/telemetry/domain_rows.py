"""Pure SUMMARY-row formatters for governance and duty-lease domains."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import cast

from peerhub.core.protocol import JsonValue
from peerhub.dispatch.duty_lease import DutyLeaseSnapshot
from peerhub.telemetry.presenter import (
    _dw,  # pyright: ignore[reportPrivateUsage]
    _format_countdown,  # pyright: ignore[reportPrivateUsage]
    _pad,  # pyright: ignore[reportPrivateUsage]
)


def _identity(value: object) -> str:
    """Apply the presenter's width helpers without changing an unpadded field."""
    text = str(value)
    return _pad(text, _dw(text))


def format_consensus_row(round_state: dict[str, JsonValue], now: int) -> str:
    """Format a consensus state; consensus rounds currently have no deadline field."""
    participants = cast(
        "dict[str, JsonValue]", round_state.get("participants") or {}
    )
    quorum = cast("dict[str, JsonValue]", participants.get("quorum") or {})
    votes = cast("dict[str, JsonValue]", round_state.get("votes") or {})
    required_participants = cast(
        "tuple[str, ...]", participants.get("required") or ()
    )
    phase = _identity(round_state.get("phase", "unknown")).upper()
    quorum_required = quorum.get("required", 0)
    votes_required = (
        len(required_participants) if required_participants else int(cast(int, quorum_required))
    )
    return f"CONSENSUS {phase} {len(votes)}/{votes_required} Q:{quorum_required} T-—"


def format_task_row(task_state: dict[str, JsonValue]) -> str:
    checkpoint = task_state.get("checkpoint") is not None
    approval = cast("dict[str, JsonValue]", task_state.get("approval") or {})
    return (
        f"TASK {_identity(task_state.get('current_stage', 'unknown')).upper()} "
        f"{_identity(task_state.get('state', 'unknown')).upper()} "
        f"CP:{'yes' if checkpoint else 'no'} AP:{'yes' if approval.get('required', False) else 'no'}"
    )


def format_task_row_narrow(task_state: dict[str, JsonValue]) -> str:
    checkpoint = task_state.get("checkpoint") is not None
    approval = cast("dict[str, JsonValue]", task_state.get("approval") or {})
    approval_required = bool(approval.get("required", False))
    stage = _identity(task_state.get("current_stage", "unknown")).upper()
    return f"TASK {stage} CP{'✓' if checkpoint else '—'} AP{'✓' if approval_required else '—'}"


def format_duty_row(lease: DutyLeaseSnapshot | None, now: int) -> str:
    if lease is None:
        return "DUTY UNHELD"
    if now >= lease.heartbeat_expires_at:
        heartbeat = "EXPIRED"
    else:
        heartbeat = _format_countdown(
            lease.heartbeat_expires_at,
            datetime.fromtimestamp(now, tz=timezone.utc),
        )
    return f"DUTY {_identity(lease.role)} {lease.term} HB:{heartbeat} {_identity(lease.room_id)}"
