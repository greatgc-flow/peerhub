"""Named compatibility helpers for retired argument conventions."""

from collections.abc import Mapping
import re

from peerhub.core.protocol import JsonValue


def legacy_room_id(
    arguments: Mapping[str, JsonValue],
    scope: Mapping[str, JsonValue],
) -> str:
    """Resolve legacy's implicit current room from call context or scope."""

    names = ("room_id", "room", "current_room", "current-room")

    def _first(values: Mapping[str, JsonValue]) -> str | None:
        for name in names:
            value = values.get(name)
            if value is not None:
                text = str(value)
                if text:
                    return text
        return None

    direct = _first(arguments)
    if direct is not None:
        return direct
    context = arguments.get("context")
    if isinstance(context, Mapping):
        contextual = _first(context)
        if contextual is not None:
            return contextual
    scoped = _first(scope)
    if scoped is not None:
        return scoped
    scope_context = scope.get("context")
    if isinstance(scope_context, Mapping):
        scoped_contextual = _first(scope_context)
        if scoped_contextual is not None:
            return scoped_contextual
    return ""


def legacy_thread_slug(topic: str) -> str:
    """Match legacy ``thread-new``'s deterministic topic-to-ID conversion."""

    return re.sub(r"[^\w-]", "-", topic.lower())[:40]
