"""Raise a durable room alert and fan it out to live participants."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from peerhub.core.context import Clock, IdSource
from peerhub.core.errors import InvalidMutationError, StaleRevisionError
from peerhub.core.protocol import CommandID, JsonValue, require_text
from peerhub.dispatch.room_session import (
    RoomParticipationCoordinator,
    RoomSessionSnapshot,
)
from peerhub.governance.broker import GovernanceBroker
from peerhub.governance.contract import EffectIntent, MutationRequest
from peerhub.governance.rooms import RoomsService


@dataclass(frozen=True)
class AlertRaiseResult:
    """Auditable outcome of one alert-slot update and mailbox fan-out."""

    alert_id: str
    alert_target_id: str
    room_id: str
    recipient_profile_ids: tuple[str, ...]
    inbox_message_target_ids: tuple[str, ...]


class AlertRaiseCoordinator:
    """Coordinate the room-alert singleton, live sessions, and inboxes."""

    def __init__(
        self,
        broker: GovernanceBroker,
        *,
        rooms: RoomsService,
        room_sessions: RoomParticipationCoordinator,
        clock: Clock,
        ids: IdSource,
    ) -> None:
        self._broker = broker
        self._rooms = rooms
        self._room_sessions = room_sessions
        self._clock = clock
        self._ids = ids

    def raise_alert(
        self,
        *,
        room_id: str,
        raiser_instance_id: str,
        raiser_profile_id: str,
        severity: str = "P1",
        message: str = "",
    ) -> AlertRaiseResult:
        """Overwrite the current alert slot, then notify live peers."""

        normalized_room_id = require_text(room_id, "room_id")
        normalized_instance_id = require_text(
            raiser_instance_id, "raiser_instance_id"
        )
        normalized_profile_id = require_text(
            raiser_profile_id, "raiser_profile_id"
        )
        normalized_severity = require_text(severity, "severity").upper()
        if normalized_severity not in {"P0", "P1"}:
            raise InvalidMutationError(
                f"invalid severity {severity!r}; must be P0 or P1"
            )
        if not isinstance(message, str):  # pyright: ignore[reportUnnecessaryIsInstance]
            raise ValueError("message must be a string")

        alert_id = self._ids.new_id("room-alert")
        timestamp = self._clock.now()
        alert_target_id = f"room-alert:{normalized_room_id}"
        winning_sessions: Sequence[RoomSessionSnapshot] = ()

        for _ in range(16):
            sessions = tuple(
                self._room_sessions.list_active_sessions(normalized_room_id)
            )
            current = self._broker.get_target(alert_target_id)
            expected_revision = 0 if current is None else current.revision
            ack_pending = tuple(
                {
                    "instance_id": session.owner.instance_id,
                    "profile_id": session.owner.profile_id,
                }
                for session in sessions
            )
            state: dict[str, JsonValue] = {
                "kind": "room-alert",
                "scope": normalized_room_id,
                "schema_version": 1,
                "room_id": normalized_room_id,
                "alert_id": alert_id,
                "severity": normalized_severity,
                "message": message,
                "status": "OPEN",
                "raised_by": {
                    "instance_id": normalized_instance_id,
                    "profile_id": normalized_profile_id,
                },
                "raised_at": timestamp,
                "ack_pending": ack_pending,
                "blocked": (
                    f"{normalized_severity} Alert: {message[:40]}..."
                ),
                "updated_at": timestamp,
            }
            try:
                self._submit(
                    target_id=alert_target_id,
                    expected_revision=expected_revision,
                    actor_id=normalized_instance_id,
                    alert_id=alert_id,
                    state=state,
                )
                winning_sessions = sessions
                break
            except StaleRevisionError:
                continue
        else:
            raise InvalidMutationError(
                "room alert changed repeatedly while raising an alert"
            )

        recipient_profile_ids: list[str] = []
        inbox_message_target_ids: list[str] = []
        seen: set[tuple[str, str]] = set()
        raiser_identity = (normalized_instance_id, normalized_profile_id)
        for session in winning_sessions:
            identity = (
                session.owner.instance_id,
                session.owner.profile_id,
            )
            if identity == raiser_identity or identity in seen:
                continue
            seen.add(identity)
            delivery = self._rooms.send_message(
                room_id=normalized_room_id,
                sender_instance_id=normalized_instance_id,
                sender_profile_id=normalized_profile_id,
                recipient_instance_id=identity[0],
                recipient_profile_id=identity[1],
                body=(
                    f"[CRITICAL-ALERT] {normalized_severity}: {message}"
                ),
                message_type="ALERT",
                resource_ref=alert_target_id,
                correlation_id=alert_id,
                priority="CRITICAL",
            )
            recipient_profile_ids.append(identity[1])
            inbox_message_target_ids.append(delivery.receipt.target_id)

        return AlertRaiseResult(
            alert_id=alert_id,
            alert_target_id=alert_target_id,
            room_id=normalized_room_id,
            recipient_profile_ids=tuple(recipient_profile_ids),
            inbox_message_target_ids=tuple(inbox_message_target_ids),
        )

    def _submit(
        self,
        *,
        target_id: str,
        expected_revision: int,
        actor_id: str,
        alert_id: str,
        state: dict[str, JsonValue],
    ) -> None:
        request_id = self._ids.new_id("alert-raise-request")
        self._broker.submit(
            MutationRequest(
                request_id=request_id,
                command_id=CommandID(
                    self._ids.new_id("alert-raise-command")
                ),
                correlation_id=alert_id,
                client_id="peerhub.alert-raise",
                command_type="alert.raise",
                idempotency_key=request_id,
                actor_id=actor_id,
                policy_revision="protocol-v2",
                target_id=target_id,
                expected_revision=expected_revision,
                operation="alert.raise",
                desired_state=state,
                effect_intent=EffectIntent(
                    kind="alert-raise.noop",
                    payload={},
                ),
            )
        )
