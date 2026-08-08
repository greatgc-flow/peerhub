import pytest
from pathlib import Path
from peerhub.persistence.sqlite import SqliteStateStore
from peerhub.events.contract import ConsumerOffset
from peerhub.core.protocol import EventEnvelope

@pytest.fixture
def store(tmp_path: Path) -> SqliteStateStore:
    db_path = tmp_path / "test.db"
    store = SqliteStateStore(db_path, workspace_home_id="test_workspace")
    store.initialize()
    return store

def test_event_repository_append_and_list(store: SqliteStateStore):
    with store.unit_of_work() as uow:
        envelope = EventEnvelope(
            protocol_major=1,
            protocol_minor=0,
            schema_version="1.0",
            event_id="123e4567-e89b-42d3-a456-426614174000",
            correlation_id="test-cmd-1",
            occurred_at=1000,
            kind="test.kind",
            payload={"key": "value"},
            request_id="req-1",
            round_id=None,
        )
        pos = uow.events.append(envelope, appended_at=1005)
        uow.commit()

    with store.unit_of_work() as uow:
        events = uow.events.list(limit=10)
        assert len(events) == 1
        assert events[0].outbox_position == pos
        assert events[0].envelope.event_id == "123e4567-e89b-42d3-a456-426614174000"
        assert events[0].appended_at == 1005

def test_consumer_offset_cas(store: SqliteStateStore):
    with store.unit_of_work() as uow:
        envelope = EventEnvelope(
            protocol_major=1,
            protocol_minor=0,
            schema_version="1.0",
            event_id="123e4567-e89b-42d3-a456-426614174000",
            correlation_id="test-cmd-1",
            occurred_at=1000,
            kind="test.kind",
            payload={"key": "value"},
            request_id="req-1",
            round_id=None,
        )
        pos = uow.events.append(envelope, appended_at=1005)
        
        offset = ConsumerOffset(
            consumer_id="test-consumer",
            outbox_position=pos,
            event_id="123e4567-e89b-42d3-a456-426614174000",
            revision=1
        )
        uow.events.add_consumer_offset(offset)
        uow.commit()

    with store.unit_of_work() as uow:
        current = uow.events.get_consumer_offset("test-consumer")
        assert current is not None
        assert current.revision == 1

        envelope2 = EventEnvelope(
            protocol_major=1,
            protocol_minor=0,
            schema_version="1.0",
            event_id="123e4567-e89b-42d3-a456-426614174001",
            correlation_id="test-cmd-1",
            occurred_at=1000,
            kind="test.kind",
            payload={"key": "value"},
            request_id="req-1",
            round_id=None,
        )
        pos2 = uow.events.append(envelope2, appended_at=1005)

        updated = ConsumerOffset(
            consumer_id="test-consumer",
            outbox_position=pos2,
            event_id="123e4567-e89b-42d3-a456-426614174001",
            revision=2
        )
        success = uow.events.cas_update_consumer_offset(current, updated)
        assert success is True
        uow.commit()

    with store.unit_of_work() as uow:
        current = uow.events.get_consumer_offset("test-consumer")
        assert current is not None
        assert current.revision == 2
        assert current.event_id == "123e4567-e89b-42d3-a456-426614174001"

def test_consumer_offset_cas_conflict(store: SqliteStateStore):
    with store.unit_of_work() as uow:
        envelope = EventEnvelope(
            protocol_major=1,
            protocol_minor=0,
            schema_version="1.0",
            event_id="123e4567-e89b-42d3-a456-426614174000",
            correlation_id="test-cmd-1",
            occurred_at=1000,
            kind="test.kind",
            payload={"key": "value"},
            request_id="req-1",
            round_id=None,
        )
        pos = uow.events.append(envelope, appended_at=1005)
        
        offset = ConsumerOffset(
            consumer_id="test-consumer-conflict",
            outbox_position=pos,
            event_id="123e4567-e89b-42d3-a456-426614174000",
            revision=1
        )
        uow.events.add_consumer_offset(offset)
        uow.commit()

    with store.unit_of_work() as uow:
        current = uow.events.get_consumer_offset("test-consumer-conflict")
        assert current is not None

        updated_1 = ConsumerOffset(
            consumer_id="test-consumer-conflict",
            outbox_position=pos,
            event_id="123e4567-e89b-42d3-a456-426614174000",
            revision=2
        )
        updated_2 = ConsumerOffset(
            consumer_id="test-consumer-conflict",
            outbox_position=pos,
            event_id="123e4567-e89b-42d3-a456-426614174000",
            revision=2
        )

        success_1 = uow.events.cas_update_consumer_offset(current, updated_1)
        assert success_1 is True

        # Second attempt with the SAME stale 'current' should fail
        success_2 = uow.events.cas_update_consumer_offset(current, updated_2)
        assert success_2 is False
        uow.commit()

def test_event_repository_list_pagination(store: SqliteStateStore):
    with store.unit_of_work() as uow:
        positions = []
        for i in range(5):
            envelope = EventEnvelope(
                protocol_major=1,
                protocol_minor=0,
                schema_version="1.0",
                event_id=f"123e4567-e89b-42d3-a456-42661417400{i}",
                correlation_id="test-cmd-1",
                occurred_at=1000 + i,
                kind="test.kind",
                payload={"index": i},
                request_id="req-1",
                round_id=None,
            )
            pos = uow.events.append(envelope, appended_at=1005 + i)
            positions.append(pos)
        uow.commit()

    with store.unit_of_work() as uow:
        # Fetch first page (limit=2)
        page1 = uow.events.list(limit=2)
        assert len(page1) == 2
        assert page1[0].outbox_position == positions[0]
        assert page1[1].outbox_position == positions[1]

        # Fetch second page using after_position from last item
        page2 = uow.events.list(limit=2, after_position=page1[-1].outbox_position)
        assert len(page2) == 2
        assert page2[0].outbox_position == positions[2]
        assert page2[1].outbox_position == positions[3]

        # Fetch third page
        page3 = uow.events.list(limit=2, after_position=page2[-1].outbox_position)
        assert len(page3) == 1
        assert page3[0].outbox_position == positions[4]
