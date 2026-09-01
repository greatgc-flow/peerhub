"""Read-only composition for the core native room status view."""

from collections.abc import Mapping

from peerhub.core.protocol import JsonValue
from peerhub.governance.rooms import RoomsService


def collect_room_status(
    rooms: RoomsService,
    *,
    room_id: str,
) -> Mapping[str, JsonValue]:
    """Return the room summary and room-wide unread mailbox count."""

    unread_count = rooms.count_unread_messages(room_id=room_id)
    summary = rooms.get_room_summary(room_id)
    room_summary: JsonValue = None
    if summary is not None:
        room_summary = {
            "mission": summary.state.get("mission"),
            "blocked": summary.state.get("blocked"),
            "phase": summary.state.get("phase"),
        }
    return {
        "room_id": room_id,
        "room_summary": room_summary,
        "unread_count": unread_count,
    }
