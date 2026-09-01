"""Immediate legacy-compatible room mailbox broadcast orchestration."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from peerhub.core.errors import InvalidMutationError
from peerhub.core.protocol import JsonValue
from peerhub.governance.rooms import RoomsService


@dataclass(frozen=True)
class RoomBroadcastResult:
    """Per-target auditable outcome of one non-idempotent room broadcast."""

    room_id: str
    delivered: tuple[Mapping[str, JsonValue], ...]


class RoomBroadcastCoordinator:
    """Fan one plain mailbox message out to resolved room participants."""

    def __init__(self, *, rooms: RoomsService) -> None:
        self._rooms = rooms

    def broadcast(
        self,
        *,
        room_id: str,
        from_: str,
        msg: str,
        targets: tuple[str, ...] | None,
        msg_type: str = "MSG",
        priority: str | None = None,
    ) -> RoomBroadcastResult:
        """Deliver one fresh private message per resolved legacy target."""

        self._require_text(room_id, "room_id")
        self._require_text(from_, "from")
        self._require_text(msg, "msg")
        self._require_text(msg_type, "msg_type")
        if priority is not None:
            self._require_text(priority, "priority")

        participants = self._participant_snapshot(
            self._rooms.list_participants(room_id)
        )
        recipients = (
            self._room_recipients(
                participants,
                sender_instance_id=from_,
                sender_profile_id=from_,
            )
            if targets is None
            else self._explicit_recipients(participants, targets)
        )
        outcomes: list[Mapping[str, JsonValue]] = []
        for target, recipient in recipients:
            if recipient is None:
                outcomes.append({
                    "target": target,
                    "status": "ERROR",
                    "error": "target is not a participant in the room",
                })
                continue
            delivery = self._rooms.send_message(
                room_id=room_id,
                sender_instance_id=from_,
                sender_profile_id=from_,
                recipient_instance_id=recipient["instance_id"],
                recipient_profile_id=recipient["profile_id"],
                body=msg,
                message_type=msg_type,
                priority=priority,
            )
            outcomes.append({
                "target": target,
                "status": "OK",
                "recipient_instance_id": recipient["instance_id"],
                "recipient_profile_id": recipient["profile_id"],
                "inbox_message_target_id": delivery.receipt.target_id,
            })
        return RoomBroadcastResult(room_id=room_id, delivered=tuple(outcomes))

    @staticmethod
    def _require_text(value: str, name: str) -> None:
        if not value:
            raise InvalidMutationError(f"{name} must be a nonempty string")

    @staticmethod
    def _participant_snapshot(
        participants: Sequence[Mapping[str, JsonValue]],
    ) -> tuple[dict[str, str], ...]:
        snapshot: list[dict[str, str]] = []
        for participant in participants:
            instance_id = participant.get("instance_id")
            profile_id = participant.get("profile_id")
            if not isinstance(instance_id, str) or not isinstance(profile_id, str):
                raise InvalidMutationError("room participant is malformed")
            snapshot.append({"instance_id": instance_id, "profile_id": profile_id})
        return tuple(snapshot)

    @staticmethod
    def _room_recipients(
        participants: Sequence[dict[str, str]],
        *,
        sender_instance_id: str,
        sender_profile_id: str,
    ) -> tuple[tuple[str, dict[str, str]], ...]:
        return tuple(
            (participant["instance_id"], participant)
            for participant in participants
            if not (
                participant["instance_id"] == sender_instance_id
                and participant["profile_id"] == sender_profile_id
            )
        )

    @staticmethod
    def _explicit_recipients(
        participants: Sequence[dict[str, str]],
        targets: tuple[str, ...],
    ) -> tuple[tuple[str, dict[str, str] | None], ...]:
        resolved: list[tuple[str, dict[str, str] | None]] = []
        for target in targets:
            recipient = next(
                (
                    participant
                    for participant in participants
                    if participant["instance_id"] == target
                ),
                None,
            )
            if recipient is None:
                recipient = next(
                    (
                        participant
                        for participant in participants
                        if participant["profile_id"] == target
                    ),
                    None,
                )
            resolved.append((target, recipient))
        return tuple(resolved)
