"""Published DTOs for the generic event log and consumer offsets."""

from __future__ import annotations

from dataclasses import dataclass

from peerhub.core.protocol import EventEnvelope


@dataclass(frozen=True)
class EventLogRecord:
    """A canonical event stored in the append-only event log."""

    envelope: EventEnvelope
    appended_at: int
    outbox_position: int

    def __post_init__(self) -> None:
        if type(self.appended_at) is not int or self.appended_at < 0:
            raise ValueError("appended_at must be a non-negative int")
        if type(self.outbox_position) is not int or self.outbox_position < 1:
            raise ValueError("outbox_position must be a positive int")


@dataclass(frozen=True)
class ConsumerOffset:
    """Revisioned consumer checkpoint for canonical event order."""

    consumer_id: str
    outbox_position: int
    event_id: str
    revision: int

    def __post_init__(self) -> None:
        if type(self.consumer_id) is not str or not self.consumer_id.strip():
            raise ValueError("consumer_id must be non-empty text")
        if type(self.event_id) is not str or not self.event_id.strip():
            raise ValueError("event_id must be non-empty text")
        if type(self.outbox_position) is not int or self.outbox_position < 0:
            raise ValueError("outbox_position must be a non-negative int")
        if type(self.revision) is not int or self.revision < 1:
            raise ValueError("revision must be a positive int")
