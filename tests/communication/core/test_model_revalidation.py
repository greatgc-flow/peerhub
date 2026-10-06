"""Models mutated or built unvalidated after construction never commit or succeed falsely at the store boundary."""
import pytest
from pydantic import ValidationError

from peerhub.extensions.observation import ObservationStore
from peerhub.extensions.observation_model import ResourcePool
from peerhub.core.models import Offset, Peer, Stream, StreamState


def test_peer_mutated_after_construct_is_rejected_without_commit(harness):
    peer = Peer(peer_id="bad")
    with pytest.raises(ValidationError):
        peer.created_at = "yesterday"  # assignment itself is validated
    before = harness.state_digest()
    forged = Peer.model_construct(peer_id="bad", created_at="yesterday", metadata={}, display_name=None, adapter_ref=None,
                                  schema_version="1.0")
    with pytest.raises(ValidationError):
        harness.store.register_peer(forged)
    assert harness.state_digest() == before and harness.store.get_peer("bad") is None
    ok = harness.store.register_peer(Peer(peer_id="good"))  # positive control
    assert ok.peer_id == "good"


def test_stream_mutated_after_construct_is_rejected_without_commit(harness):
    stream = Stream(stream_id="s")
    with pytest.raises(ValidationError):
        stream.revision = 0
    before = harness.state_digest()
    forged = Stream.model_construct(stream_id="s", title=None, state=StreamState.OPEN, members=[], revision=0, metadata={},
                                    created_at="2026-01-01T00:00:00Z", schema_version="1.0")
    with pytest.raises(ValidationError):
        harness.store.create_stream(forged)
    assert harness.state_digest() == before and harness.store.get_stream("s") is None
    assert harness.store.create_stream(Stream(stream_id="s")).revision == 1
    assert harness.store.get_stream("s").revision == 1


def test_offset_assignment_is_validated():
    off = Offset(peer_id="a", stream_id="s", read_through_position=0, revision=1)
    with pytest.raises(ValidationError):
        off.revision = 0


def test_observation_pool_and_observation_revalidated(tmp_path):
    db = tmp_path / "o.db"
    st = ObservationStore(db)
    forged = ResourcePool.model_construct(resource_pool_id="", provider="x", kind="QUOTA", metadata={}, schema_version="1.0")
    with pytest.raises(ValidationError):
        st.register_resource_pool(forged)
    assert st.get_resource_pool("") is None
    assert st.register_resource_pool(ResourcePool(resource_pool_id="p", provider="x", kind="QUOTA")).resource_pool_id == "p"
