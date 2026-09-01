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

    return {
        "room_id": room_id,
        "room_summary": room_summary,
        "unread_count": unread_count,
        "active_participants": active_participants,
    }
