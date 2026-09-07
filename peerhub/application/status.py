"""Read-only composition for the core native room status view."""

from collections.abc import Mapping

from peerhub.core.protocol import JsonValue
from peerhub.governance.rooms import RoomsService
from peerhub.dispatch.room_session import RoomParticipationCoordinator


def collect_room_status(
    rooms: RoomsService,
    *,
    room_id: str,
    room_sessions: RoomParticipationCoordinator | None = None,
) -> Mapping[str, JsonValue]:
    """Return the room summary, unread count, and active participants."""

    unread_count = rooms.count_unread_messages(room_id=room_id)
    summary = rooms.get_room_summary(room_id)
    room_summary: JsonValue = None
    if summary is not None:
        room_summary = {
            "mission": summary.state.get("mission"),
            "blocked": summary.state.get("blocked"),
            "phase": summary.state.get("phase"),
        }
        
    active_participants: tuple[JsonValue, ...] = ()
    if room_sessions is not None:
        sessions = room_sessions.list_active_sessions(room_id)
        active_participants = tuple(
            {
                "instance_id": session.owner.instance_id,
                "profile_id": session.owner.profile_id,
                "session_id": session.session_id,
            }
            for session in sessions
        )

    thread_targets = rooms.list_threads(room_id)
    thread_ids: tuple[str, ...] = tuple(
        str(target.state.get("thread_id") or target.target_id)
        for target in thread_targets
    )
    threads: tuple[dict[str, JsonValue], ...] = tuple(
        {
            "thread_id": target.state.get("thread_id"),
            "subject": target.state.get("subject"),
            "status": target.state.get("status"),
        }
        for target in thread_targets
    )
    messages = rooms.list_messages(room_id)
    message_count = len(messages)
    last_message_at = (
        messages[-1].state.get("created_at") if messages else None
    )

    return {
        "room_id": room_id,
        "room_summary": room_summary,
        "unread_count": unread_count,
        "active_participants": active_participants,
        "thread_ids": thread_ids,
        "threads": threads,
        "message_count": message_count,
        "last_message_at": last_message_at,
    }
