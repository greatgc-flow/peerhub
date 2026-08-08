"""Tests for the event and offset dataclass validation."""

import pytest
import uuid
from peerhub.events.contract import EventLogRecord, ConsumerOffset
from peerhub.core.protocol import EventEnvelope

def _dummy_envelope() -> EventEnvelope:
    return EventEnvelope(
        protocol_major=1,
        protocol_minor=0,
        schema_version="1.0",
        event_id=str(uuid.uuid4()),
        correlation_id="corr-1",
        occurred_at=1000,
        kind="test.kind",
        payload={},
        request_id=None,
        round_id=None,
    )

def test_event_log_record_validation():
    env = _dummy_envelope()

    # Valid
    EventLogRecord(envelope=env, appended_at=1000, outbox_position=1)

    # Invalid appended_at
    with pytest.raises(ValueError, match="appended_at must be a non-negative int"):
        EventLogRecord(envelope=env, appended_at=-1, outbox_position=1)
    
    with pytest.raises(ValueError, match="appended_at must be a non-negative int"):
        EventLogRecord(envelope=env, appended_at="1000", outbox_position=1)  # type: ignore

    # Invalid outbox_position
    with pytest.raises(ValueError, match="outbox_position must be a positive int"):
        EventLogRecord(envelope=env, appended_at=1000, outbox_position=0)

    with pytest.raises(ValueError, match="outbox_position must be a positive int"):
        EventLogRecord(envelope=env, appended_at=1000, outbox_position="1")  # type: ignore


def test_consumer_offset_validation():
    # Valid
    ConsumerOffset(consumer_id="consumer-1", outbox_position=0, event_id="event-1", revision=1)

    # Invalid consumer_id
    with pytest.raises(ValueError, match="consumer_id must be non-empty text"):
        ConsumerOffset(consumer_id="", outbox_position=0, event_id="event-1", revision=1)
    with pytest.raises(ValueError, match="consumer_id must be non-empty text"):
        ConsumerOffset(consumer_id="   ", outbox_position=0, event_id="event-1", revision=1)
    with pytest.raises(ValueError, match="consumer_id must be non-empty text"):
        ConsumerOffset(consumer_id=None, outbox_position=0, event_id="event-1", revision=1)  # type: ignore

    # Invalid event_id
    with pytest.raises(ValueError, match="event_id must be non-empty text"):
        ConsumerOffset(consumer_id="c", outbox_position=0, event_id="", revision=1)
    with pytest.raises(ValueError, match="event_id must be non-empty text"):
        ConsumerOffset(consumer_id="c", outbox_position=0, event_id="   ", revision=1)
    with pytest.raises(ValueError, match="event_id must be non-empty text"):
        ConsumerOffset(consumer_id="c", outbox_position=0, event_id=None, revision=1)  # type: ignore

    # Invalid outbox_position
    with pytest.raises(ValueError, match="outbox_position must be a non-negative int"):
        ConsumerOffset(consumer_id="c", outbox_position=-1, event_id="event-1", revision=1)
    with pytest.raises(ValueError, match="outbox_position must be a non-negative int"):
        ConsumerOffset(consumer_id="c", outbox_position="0", event_id="event-1", revision=1)  # type: ignore

    # Invalid revision
    with pytest.raises(ValueError, match="revision must be a positive int"):
        ConsumerOffset(consumer_id="c", outbox_position=0, event_id="event-1", revision=0)
    with pytest.raises(ValueError, match="revision must be a positive int"):
        ConsumerOffset(consumer_id="c", outbox_position=0, event_id="event-1", revision="1")  # type: ignore
