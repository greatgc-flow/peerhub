"""Cross-domain immediate lesson broadcast orchestration."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from uuid import uuid4

from peerhub.core.errors import InvalidMutationError, RecordNotFoundError
from peerhub.core.protocol import CommandID, JsonValue
from peerhub.governance.broker import GovernanceBroker
from peerhub.governance.contract import EffectIntent, MutationRequest
from peerhub.governance.lessons import LessonService
from peerhub.governance.rooms import RoomsService


@dataclass(frozen=True)
class LessonBroadcastResult:
    """Auditable outcome of one immediate lesson broadcast campaign."""

    campaign_id: str
    campaign_target_id: str
    lesson_id: str
    room_id: str
    recipient_profile_ids: tuple[str, ...]
    inbox_message_target_ids: tuple[str, ...]
    delivery_target_ids: tuple[str, ...]


class LessonBroadcastCoordinator:
    """Coordinate active lesson delivery across independent lesson/room stores."""

    def __init__(
        self,
        *,
        broker: GovernanceBroker,
        lessons: LessonService,
        rooms: RoomsService,
    ) -> None:
        self._broker = broker
        self._lessons = lessons
        self._rooms = rooms

    def broadcast(
        self,
        *,
        lesson_id: str,
        room_id: str,
        sender_instance_id: str,
        sender_profile_id: str,
        created_at: int,
    ) -> LessonBroadcastResult:
        """Immediately deliver an active lesson and record each pending delivery."""

        lesson = self._lessons.get_target(lesson_id)
        if lesson is None:
            raise RecordNotFoundError("lesson", lesson_id)
        if lesson.state.get("lifecycle") != "ACTIVE":
            raise InvalidMutationError("lesson broadcast requires an active lesson")
        if type(created_at) is not int or created_at < 0:
            raise ValueError("created_at must be a nonnegative integer")

        body = self._lesson_message_body(lesson_id, lesson.state)
        recipients = self._recipient_snapshot(
            self._rooms.list_participants(room_id),
            sender_instance_id=sender_instance_id,
            sender_profile_id=sender_profile_id,
        )
        campaign_id = str(uuid4())
        campaign_target_id = f"lesson-broadcast:{campaign_id}"
        campaign_state: dict[str, JsonValue] = {
            "kind": "lesson-broadcast",
            "scope": room_id,
            "schema_version": 1,
            "campaign_id": campaign_id,
            "lesson_id": lesson_id,
            "room_id": room_id,
            "sender": {
                "instance_id": sender_instance_id,
                "profile_id": sender_profile_id,
            },
            "recipients": tuple(recipients),
            "message_type": "LESSON",
            "body": body,
            "created_at": created_at,
        }
        self._record_campaign(
            target_id=campaign_target_id,
            actor_id=sender_instance_id,
            campaign_state=campaign_state,
        )

        inbox_message_target_ids: list[str] = []
        delivery_target_ids: list[str] = []
        recipient_profile_ids: list[str] = []
        for recipient in recipients:
            recipient_instance_id = recipient["instance_id"]
            recipient_profile_id = recipient["profile_id"]
            delivery = self._rooms.send_message(
                room_id=room_id,
                sender_instance_id=sender_instance_id,
                sender_profile_id=sender_profile_id,
                recipient_instance_id=recipient_instance_id,
                recipient_profile_id=recipient_profile_id,
                body=body,
                message_type="LESSON",
                resource_ref=f"lesson:{lesson_id}",
                correlation_id=campaign_id,
            )
            pending = self._lessons.record_delivery_pending(
                lesson_id,
                recipient_profile_id,
                delivery_method="broadcast",
                actor_id=sender_instance_id,
            )
            recipient_profile_ids.append(recipient_profile_id)
            inbox_message_target_ids.append(delivery.receipt.target_id)
            delivery_target_ids.append(pending.receipt.target_id)

        return LessonBroadcastResult(
            campaign_id=campaign_id,
            campaign_target_id=campaign_target_id,
            lesson_id=lesson_id,
            room_id=room_id,
            recipient_profile_ids=tuple(recipient_profile_ids),
            inbox_message_target_ids=tuple(inbox_message_target_ids),
            delivery_target_ids=tuple(delivery_target_ids),
        )

    @staticmethod
    def _lesson_message_body(
        lesson_id: str,
        state: Mapping[str, JsonValue],
    ) -> str:
        content = state.get("content")
        if not isinstance(content, Mapping):
            raise InvalidMutationError("active lesson content is malformed")
        title = content.get("title")
        rule = content.get("rule")
        if not isinstance(title, str) or not isinstance(rule, str):
            raise InvalidMutationError("active lesson content is malformed")
        return f"[LESSON {lesson_id}] {title}\n{rule}"

    @staticmethod
    def _recipient_snapshot(
        participants: Sequence[Mapping[str, JsonValue]],
        *,
        sender_instance_id: str,
        sender_profile_id: str,
    ) -> tuple[dict[str, str], ...]:
        recipients: list[dict[str, str]] = []
        for participant in participants:
            instance_id = participant.get("instance_id")
            profile_id = participant.get("profile_id")
            if not isinstance(instance_id, str) or not isinstance(profile_id, str):
                raise InvalidMutationError("room participant is malformed")
            if (
                instance_id == sender_instance_id
                and profile_id == sender_profile_id
            ):
                continue
            recipients.append(
                {"instance_id": instance_id, "profile_id": profile_id}
            )
        return tuple(recipients)

    def _record_campaign(
        self,
        *,
        target_id: str,
        actor_id: str,
        campaign_state: Mapping[str, JsonValue],
    ) -> None:
        request_id = str(uuid4())
        self._broker.submit(
            MutationRequest(
                request_id=request_id,
                command_id=CommandID(str(uuid4())),
                correlation_id=str(campaign_state["campaign_id"]),
                client_id="peerhub.lesson-broadcast",
                command_type="lesson.broadcast.record",
                idempotency_key=request_id,
                actor_id=actor_id,
                policy_revision="protocol-v2",
                target_id=target_id,
                expected_revision=0,
                operation="lesson.broadcast.record",
                desired_state=campaign_state,
                effect_intent=EffectIntent(
                    kind="lesson-broadcast.noop",
                    payload={},
                ),
            )
        )
