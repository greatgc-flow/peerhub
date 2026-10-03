from pathlib import Path
import pytest
import sqlite3
from concurrent.futures import ThreadPoolExecutor

from peerhub.m1.models import Peer, Stream, StreamState, Record, Offset
from peerhub.m1.store import CoreStore, IdempotencyConflictError, CasMismatchError


@pytest.fixture
def store(tmp_path: Path) -> CoreStore:
    db_file = tmp_path / "test_core.db"
    return CoreStore(db_file)


def test_peer_registration_and_retrieval(store: CoreStore):
    peer = Peer(peer_id="peer-1", display_name="Worker 1", adapter_ref="claude")
    store.register_peer(peer)

    retrieved = store.get_peer("peer-1")
    assert retrieved is not None
    assert retrieved.peer_id == "peer-1"
    assert retrieved.display_name == "Worker 1"


def test_stream_creation_and_retrieval(store: CoreStore):
    p1 = Peer(peer_id="peer-1")
    p2 = Peer(peer_id="peer-2")
    store.register_peer(p1)
    store.register_peer(p2)

    stream = Stream(
        stream_id="stream-1",
        title="Project Discussion",
        members=["peer-1", "peer-2"],
    )
    store.create_stream(stream)

    retrieved = store.get_stream("stream-1")
    assert retrieved is not None
    assert retrieved.stream_id == "stream-1"
    assert retrieved.title == "Project Discussion"
    assert sorted(retrieved.members) == ["peer-1", "peer-2"]
    assert retrieved.state == StreamState.OPEN


def test_append_record_monotonic_position(store: CoreStore):
    store.register_peer(Peer(peer_id="author"))
    store.create_stream(Stream(stream_id="stream-1"))

    r1 = store.append_record(
        stream_id="stream-1",
        author_peer_id="author",
        kind="message",
        body={"text": "first"},
        idempotency_key="key-1",
    )
    r2 = store.append_record(
        stream_id="stream-1",
        author_peer_id="author",
        kind="message",
        body={"text": "second"},
        idempotency_key="key-2",
    )

    assert r1.position == 1
    assert r2.position == 2
    assert r1.payload_digest.startswith("sha256:")

    records = store.read_records("stream-1", after_position=0)
    assert len(records) == 2
    assert records[0].record_id == r1.record_id
    assert records[1].record_id == r2.record_id


def test_append_record_idempotency_reuse(store: CoreStore):
    store.register_peer(Peer(peer_id="author"))
    store.create_stream(Stream(stream_id="stream-1"))

    r1 = store.append_record(
        stream_id="stream-1",
        author_peer_id="author",
        kind="message",
        body={"text": "identical"},
        idempotency_key="key-same",
    )
    # Append with same key and same payload
    r2 = store.append_record(
        stream_id="stream-1",
        author_peer_id="author",
        kind="message",
        body={"text": "identical"},
        idempotency_key="key-same",
    )

    assert r1.record_id == r2.record_id
    assert r1.position == r2.position
    assert store.read_records("stream-1") == [r1]


def test_append_record_idempotency_conflict_on_mismatch(store: CoreStore):
    store.register_peer(Peer(peer_id="author"))
    store.create_stream(Stream(stream_id="stream-1"))

    store.append_record(
        stream_id="stream-1",
        author_peer_id="author",
        kind="message",
        body={"text": "first payload"},
        idempotency_key="key-clash",
    )

    with pytest.raises(IdempotencyConflictError):
        store.append_record(
            stream_id="stream-1",
            author_peer_id="author",
            kind="message",
            body={"text": "DIFFERENT payload"},
            idempotency_key="key-clash",
        )


def test_offset_cas_flow(store: CoreStore):
    store.register_peer(Peer(peer_id="worker"))
    store.create_stream(Stream(stream_id="stream-1"))
    for i in range(10):  # TD-10: offset may not pass the committed head
        store.append_record(stream_id="stream-1", author_peer_id="worker", kind="message", body=i, idempotency_key=f"h{i}")

    initial_offset = store.get_offset("worker", "stream-1")
    assert initial_offset.read_through_position == 0
    assert initial_offset.revision == 1

    # First update: expected revision 1 -> becomes revision 2
    updated = store.advance_offset_cas(
        peer_id="worker",
        stream_id="stream-1",
        new_position=5,
        expected_revision=1,
    )
    assert updated.read_through_position == 5
    assert updated.revision == 2

    # Second update with wrong expected revision -> fail
    with pytest.raises(CasMismatchError):
        store.advance_offset_cas(
            peer_id="worker",
            stream_id="stream-1",
            new_position=10,
            expected_revision=1,  # Stale!
        )

    # Second update with correct expected revision 2 -> success
    updated2 = store.advance_offset_cas(
        peer_id="worker",
        stream_id="stream-1",
        new_position=10,
        expected_revision=2,
    )
    assert updated2.read_through_position == 10
    assert updated2.revision == 3


def test_core_invariant_no_extension_imports():
    """Verify that Core does NOT import any extension module."""
    import sys
    from peerhub import m1
    from peerhub.m1 import models, store

    # Verify modules inside peerhub.m1 only import standard libraries / pydantic / itself
    forbidden_terms = ["extension", "governance", "dispatch", "telemetry", "routing"]
    for mod in [m1, models, store]:
        for attr_name in dir(mod):
            attr = getattr(mod, attr_name)
            if hasattr(attr, "__module__") and attr.__module__:
                for term in forbidden_terms:
                    assert term not in attr.__module__, f"Core module {mod} imports forbidden extension: {attr.__module__}"
