"""Durable communication Core: Peer, Stream, Record and Offset."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .models import Offset, Peer, Record, Stream, StreamState

__all__ = ["Peer", "Stream", "StreamState", "Record", "Offset"]


def __getattr__(name: str) -> object:
    if name not in __all__:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from . import models

    value = getattr(models, name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
